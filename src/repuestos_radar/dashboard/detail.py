"""Part detail: stores by tier, fair price, margins, trend — M3 priority order."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import select

from repuestos_radar.analysis import (
    BASIS_MEDIAN,
    StoreOffer,
    TierAnalysis,
    analyze_item,
    latest_day,
    listings_for_day,
    tier_trends,
)
from repuestos_radar.dashboard import data, distance, text_es
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.relevance import Relevance
from repuestos_radar.report import TIER_LABELS_ES, format_ars
from repuestos_radar.sources import load_sources


@st.cache_data
def source_names() -> dict[str, str]:
    return {source.slug: source.name for source in load_sources()}


def _trend_chart(series: Sequence[tuple[date, Decimal]]) -> alt.Chart:
    """Fair-price history as a plain Altair line: no tooltip, no pan/zoom.

    Not ``st.line_chart``: its built-in Vega tooltip sticks open after a
    touch on phones (the client's main device). A deliberately static chart
    is the fix, so this must stay free of tooltip encodings and of
    ``.interactive()``.
    """
    frame = pd.DataFrame(
        {
            text_es.TREND_CHART_DAY_COLUMN: [d for d, _ in series],
            text_es.TREND_CHART_PRICE_COLUMN: [float(p) for _, p in series],
        }
    )
    return (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                text_es.TREND_CHART_DAY_COLUMN,
                type="temporal",
                axis=alt.Axis(format="%d/%m"),
                title=text_es.TREND_CHART_DAY_COLUMN,
            ),
            y=alt.Y(
                text_es.TREND_CHART_PRICE_COLUMN,
                type="quantitative",
                title=text_es.TREND_CHART_PRICE_COLUMN,
            ),
        )
    )


@st.cache_data
def _store_coords() -> dict[str, tuple[float, float]]:
    return {
        source.slug: (source.lat, source.lon) for source in load_sources() if source.lat is not None
    }


def _distance_for(
    slug: str,
    reference: tuple[float, float] | None,
    coords: dict[str, tuple[float, float]],
) -> str | None:
    if reference is None or slug not in coords:
        return None
    lat, lon = coords[slug]
    return distance.format_distance_km(distance.haversine_km(reference[0], reference[1], lat, lon))


def _sorted_offers(
    offers: Sequence[StoreOffer],
    sort_key: str,
    reference: tuple[float, float] | None,
    coords: dict[str, tuple[float, float]],
) -> tuple[StoreOffer, ...]:
    if sort_key != "distancia" or reference is None:
        return tuple(sorted(offers, key=lambda o: o.price))

    def sort_value(offer: StoreOffer) -> tuple[int, float]:
        if offer.source_slug not in coords:
            return (1, float(offer.price))  # unknown position: last, then by price
        lat, lon = coords[offer.source_slug]
        return (0, distance.haversine_km(reference[0], reference[1], lat, lon))

    return tuple(sorted(offers, key=sort_value))


def _adopt_reading(state, location: dict | None) -> bool:
    """Adopt a NEW geolocation component reading as the reference point.

    The component replays its last reading on every rerun, not just on a
    click, so only a reading that differs from the last one ADOPTED is taken.
    Comparing against ``reference_point`` itself would re-adopt the stale
    reading right after "Volver al local" clears it, making that button
    visibly do nothing. Returns True when the state changed (caller reruns).
    """
    if not location or location.get("latitude") is None:
        return False
    point = (location["latitude"], location["longitude"])
    if point == state.get("geo_last_reading"):
        return False
    state["geo_last_reading"] = point
    state["reference_point"] = point
    return True


def _reference_point() -> tuple[float, float] | None:
    """The shop by default; the visitor's position while they opt in this visit."""
    shop = distance.shop_location()
    current = st.session_state.get("reference_point")
    columns = st.columns([3, 2])
    with columns[0]:
        if current is not None:
            st.markdown(f"📍 {text_es.FROM_MY_LOCATION}")
            if st.button(text_es.BACK_TO_SHOP):
                st.session_state.pop("reference_point")
                st.rerun()
        else:
            st.markdown(f"📍 {text_es.FROM_SHOP}" if shop else f"📍 {text_es.NO_SHOP_LOCATION}")
    with columns[1]:
        # The component import is guarded: under AppTest, or on any component
        # breakage, the page degrades to shop-only distances (no button).
        try:
            from streamlit_geolocation import streamlit_geolocation

            location = streamlit_geolocation()  # renders the permission button
        except Exception:
            location = None
        if _adopt_reading(st.session_state, location):
            st.rerun()
    return current or shop


def _offer_line(offer: StoreOffer, names: dict[str, str], distance_text: str | None) -> str:
    name = names.get(offer.source_slug, offer.source_slug)
    parts = [f"[{name}]({offer.url})", f"**{format_ars(offer.price)}**"]
    if distance_text is not None:
        parts.append(distance_text)
    line = " — ".join(parts)
    warnings = []
    if offer.outlier:
        warnings.append(text_es.OUTLIER_WARNING)
    if offer.relevance == Relevance.LOW_CONFIDENCE.value:
        warnings.append(text_es.LOW_CONFIDENCE_WARNING)
    if warnings:
        line += f"  \n:orange[⚠ {'; '.join(warnings)}]"
    return line


def _fair_price_line(analysis: TierAnalysis) -> str:
    if analysis.basis == BASIS_MEDIAN:
        line = f"{text_es.FAIR_PRICE_PREFIX} **{format_ars(analysis.fair_price)}**"
        if analysis.store_count <= 3:
            line += " — " + text_es.FAIR_PRICE_RANGE.format(
                low=format_ars(analysis.price_min),
                high=format_ars(analysis.price_max),
                count=analysis.store_count,
            )
        return line
    return f"*{text_es.SINGLE_STORE_NOTE}*"


def _select_item(session) -> TrackedItem | None:
    items = session.scalars(
        select(TrackedItem).where(TrackedItem.active).order_by(TrackedItem.id)
    ).all()
    if not items:
        return None
    by_id = {item.id: item for item in items}
    selected = st.session_state.get("selected_item_id")
    index = list(by_id).index(selected) if selected in by_id else 0
    choice = st.selectbox(
        text_es.PICK_ITEM, list(by_id), index=index, format_func=lambda i: by_id[i].query
    )
    st.session_state["selected_item_id"] = choice
    return by_id[choice]


def render() -> None:
    st.title(text_es.NAV_DETAIL)
    names = source_names()
    with data.open_session() as session:
        item = _select_item(session)
        if item is None:
            st.markdown(f"*{text_es.NO_DATA_AT_ALL}*")
            return
        day = latest_day(session, item.id)
        if day is None:
            st.markdown(f"*{text_es.NO_DATA_TODAY}*")
            return
        analyses = analyze_item(listings_for_day(session, item.id, day))

        reference = _reference_point()
        coords = _store_coords()
        sort = st.radio(
            text_es.SORT_LABEL, [text_es.SORT_PRICE, text_es.SORT_DISTANCE], horizontal=True
        )
        sort_key = "distancia" if sort == text_es.SORT_DISTANCE else "precio"

        for analysis in analyses:
            st.subheader(TIER_LABELS_ES[analysis.tier])
            for offer in _sorted_offers(analysis.offers, sort_key, reference, coords):
                st.markdown(
                    _offer_line(offer, names, _distance_for(offer.source_slug, reference, coords))
                )
            st.markdown(_fair_price_line(analysis))

        services = session.scalars(
            select(ServicePrice).where(ServicePrice.tracked_item_id == item.id)
        ).all()
        if services:
            st.subheader(text_es.MARGIN_HEADER)
            for service in services:
                for tier_margin in margins_for(service.price_ars, analyses):
                    verb = (
                        text_es.MARGIN_VERB_GAIN
                        if tier_margin.margin >= 0
                        else text_es.MARGIN_VERB_LOSS
                    )
                    st.markdown(
                        text_es.MARGIN_LINE.format(
                            label=service.label,
                            service=format_ars(service.price_ars),
                            verb=verb,
                            amount=format_ars(abs(tier_margin.margin)),
                            store=names.get(tier_margin.part_source, tier_margin.part_source),
                            tier=TIER_LABELS_ES[tier_margin.tier],
                        )
                    )

        for analysis in analyses:
            points = tier_trends(session, item.id, analysis.tier, day)
            shown = [p for p in points if p.direction]
            if shown:
                trend_text = " · ".join(
                    f"{p.direction} {str(abs(p.pct_change)).replace('.', ',')}% "
                    # The real gap, not the nominal window — a stored day can
                    # land up to _TREND_TOLERANCE_DAYS off the target
                    # (report.py does the same for the exact same reason).
                    + text_es.TREND_VS.format(days=(day - p.compared_date).days)
                    for p in shown
                )
                st.caption(f"{TIER_LABELS_ES[analysis.tier]}: {trend_text}")
            with st.expander(f"{text_es.TREND_CHART_LABEL} — {TIER_LABELS_ES[analysis.tier]}"):
                series = data.fair_price_series(session, item.id, analysis.tier, day)
                if len(series) >= 2:
                    st.altair_chart(_trend_chart(series), use_container_width=True)
                else:
                    st.markdown(f"*{text_es.NO_TREND}*")
