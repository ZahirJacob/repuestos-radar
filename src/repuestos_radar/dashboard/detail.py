"""Part detail: stores by tier, fair price, margins, trend — M3 priority order."""

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from urllib.parse import quote

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
from repuestos_radar.dashboard import data, demo, distance, radar
from repuestos_radar.dashboard.text import t
from repuestos_radar.margin import TierMargin, margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.relevance import Relevance
from repuestos_radar.report import escape_md_dollars, md_ars
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
            t.TREND_CHART_DAY_COLUMN: [d for d, _ in series],
            t.TREND_CHART_PRICE_COLUMN: [float(p) for _, p in series],
        }
    )
    return (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                t.TREND_CHART_DAY_COLUMN,
                type="temporal",
                axis=alt.Axis(format="%d/%m"),
                title=t.TREND_CHART_DAY_COLUMN,
            ),
            y=alt.Y(
                t.TREND_CHART_PRICE_COLUMN,
                type="quantitative",
                title=t.TREND_CHART_PRICE_COLUMN,
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


def distance_from_shop(slug: str) -> str | None:
    """Distance text from the Activcelu shop to a store, or None when either
    position is unknown (the home cards use this; the detail page passes its
    own reference point to ``_distance_for``)."""
    return _distance_for(slug, distance.shop_location(), _store_coords())


def distance_pill(distance_text: str) -> str:
    """A distance as a gray pill: ``:gray-background[◎\u00a01,8 km]``.

    The space after the pin is non-breaking: on a phone-width screen the
    pill otherwise wraps between the pin and the number.
    """
    return f":gray-background[◎\u00a0{distance_text}]"


def _adopt_reading(state, location: dict | None) -> bool:
    """Adopt a geolocation reading as the reference point; True when the
    state changed (the caller reruns).

    A reading equal to the last one adopted is ignored. The guarantee this
    gives: while a request is mounted, a rerun caused by any other widget
    hands the same answer back, and it must not count as a new tap. It never
    blocks a deliberate tap, because ``_request_location`` resets the baseline
    on every tap and the component is unmounted right after adoption (so
    "Volver al local" has nothing to replay against).
    """
    if not location or location.get("latitude") is None:
        return False
    point = (location["latitude"], location["longitude"])
    if point == state.get("geo_last_reading"):
        return False
    state["geo_last_reading"] = point
    state["reference_point"] = point
    return True


def _reading_from_answer(answer: object) -> dict | None:
    """Flatten a ``get_geolocation()`` answer to ``{"latitude", "longitude"}``.

    The component answers None until the browser replies, then either
    ``{"coords": {"latitude": ..., "longitude": ..., ...}, "timestamp": ...}``
    or ``{"error": {"code": ..., "message": ...}}`` (permission denied and
    the like are resolved, not raised). Anything else — no answer yet, a
    missing or non-numeric coordinate, NaN/inf, or a point off the globe —
    is treated as no reading.
    """
    if not isinstance(answer, dict):
        return None
    coords = answer.get("coords")
    if not isinstance(coords, dict):
        return None
    try:
        latitude = float(coords["latitude"])
        longitude = float(coords["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return {"latitude": latitude, "longitude": longitude}


def _answer_is_denied(answer: object) -> bool:
    return isinstance(answer, dict) and bool(answer.get("error"))


def _request_location(state) -> None:
    """Tap on "Usar mi ubicación": ask the browser for a FRESH position.

    Bumping the request id gives the component a new key, so a new iframe
    asks the browser again instead of Streamlit replaying the previous
    answer. The adopted-reading baseline is reset on purpose: a deliberate
    second tap from the same spot must still take effect after "Volver al
    local", and ``_adopt_reading`` would otherwise reject the identical
    reading as a replay.
    """
    state["geo_requested"] = True
    state["geo_request_id"] = state.get("geo_request_id", 0) + 1
    state.pop("geo_last_reading", None)
    state.pop("geo_denied", None)


def _back_to_shop(state) -> None:
    """Tap on "Volver al local": drop the visitor's position and any pending
    or failed request. The geo reading is session-only and never stored."""
    state.pop("reference_point", None)
    state.pop("geo_requested", None)
    state.pop("geo_denied", None)


def _geolocation():
    """The component's ``get_geolocation``, or None when it cannot be
    imported. Guarded so that a broken or missing component degrades the page
    to shop-only distances (the button is shown disabled) instead of a crash.
    """
    try:
        from streamlit_js_eval import get_geolocation
    except Exception:
        return None
    return get_geolocation


def _ask_browser(request_id: int) -> object:
    """Render the geolocation component for this request and return its
    answer so far (None until the browser replies, or on any component
    breakage)."""
    ask = _geolocation()
    if ask is None:
        return None
    try:
        return ask(component_key=f"geo-{request_id}")
    except Exception:
        return None


def _origin_labels() -> tuple[str, str, str]:
    """(from-line, back-button, denied-note) for the default origin: the shop,
    or the public stand-in the demo measures from (never naming the client)."""
    if demo.is_demo():
        return t.DEMO_FROM_SHOP, t.DEMO_BACK_TO_SHOP, t.DEMO_LOCATION_DENIED
    return t.FROM_SHOP, t.BACK_TO_SHOP, t.LOCATION_DENIED


def _reference_point() -> tuple[float, float] | None:
    """The shop by default; the visitor's position while they opt in this visit."""
    shop = distance.shop_location()
    from_shop, back_to_shop, location_denied = _origin_labels()
    state = st.session_state
    current = state.get("reference_point")
    with st.container(border=True):
        if current is not None:
            st.markdown(f"◎ {t.FROM_MY_LOCATION}")
        else:
            st.markdown(f"◎ {from_shop}" if shop else f"◎ {t.NO_SHOP_LOCATION}")
        use_column, back_column = st.columns(2)
        if use_column.button(
            t.USE_MY_LOCATION,
            type="primary",
            icon=":material/my_location:",
            width="stretch",
            disabled=_geolocation() is None,  # no component: an honest dead button
        ):
            _request_location(state)
        if back_column.button(
            back_to_shop,
            type="secondary",
            icon=":material/storefront:",
            width="stretch",
        ):
            _back_to_shop(state)
            st.rerun()
        if state.get("geo_denied"):
            st.caption(location_denied)
        if state.get("geo_requested"):
            answer = _ask_browser(state.get("geo_request_id", 0))
            if _answer_is_denied(answer):
                state["geo_denied"] = True
                state.pop("geo_requested", None)
                st.rerun()
            elif _adopt_reading(state, _reading_from_answer(answer)):
                # Adopted: stop rendering the component, so nothing replays.
                state.pop("geo_requested", None)
                st.rerun()
    return current or shop


def _store_link(name: str, url: str) -> str:
    """``[name](url)`` for a web URL, plain ``name`` for anything else.

    Listing URLs come from the stores' own JSON. New rows are validated on
    ingest (schema.py), but rows stored before that existed are still shown,
    so a non-http(s) value renders as text rather than a tappable link.
    Markdown-breaking characters (``)``, spaces) are percent-encoded so a
    store's URL cannot cut the link short; ``%`` stays as-is so an already
    encoded URL is not double-encoded.
    """
    if not url.lower().startswith(("http://", "https://")):
        return name
    return f"[{name}]({quote(url, safe=":/?#[]@!$&'*+,;=%~-._")})"


def _offer_line(offer: StoreOffer, names: dict[str, str], distance_text: str | None) -> str:
    """One store offer as a markdown block: price first and big, then the
    store link with the distance and any warnings as pills on the same line.

    The price is a ``####`` heading rather than a two-column row: Streamlit
    stacks columns on a phone-width screen, so a side-by-side layout would
    come apart exactly where the client reads it.
    """
    name = names.get(offer.source_slug, offer.source_slug)
    parts = [_store_link(name, offer.url)]
    if distance_text is not None:
        parts.append(distance_pill(distance_text))
    # Non-breaking space after the marker, same reason as in distance_pill.
    if offer.outlier:
        parts.append(f":orange-background[⚠\u00a0{t.OUTLIER_WARNING}]")
    if offer.relevance == Relevance.LOW_CONFIDENCE.value:
        parts.append(f":orange-background[⚠\u00a0{t.LOW_CONFIDENCE_WARNING}]")
    return f"#### {md_ars(offer.price)}\n" + " ".join(parts)


def _tier_heading(analysis: TierAnalysis) -> str:
    """``Original · 3 tiendas``: the tier label with its store count in gray."""
    count = (
        t.TIER_STORE_COUNT_ONE
        if analysis.store_count == 1
        else t.TIER_STORE_COUNT.format(count=analysis.store_count)
    )
    return f"{t.TIER_LABELS[analysis.tier]} :gray[· {count}]"


def _sort_key(choice: str | None) -> str:
    """Segmented-control choice to sort key; nothing selected means price."""
    return "distancia" if choice == t.SORT_DISTANCE else "precio"


def _fair_price_highlight(analysis: TierAnalysis) -> str:
    """The fair-price line on a green background (the theme's accent), so it
    stands apart from the store rows above it."""
    return f":green-background[{_fair_price_line(analysis)}]"


def _fair_price_line(analysis: TierAnalysis) -> str:
    if analysis.basis == BASIS_MEDIAN:
        line = f"{t.FAIR_PRICE_PREFIX} **{md_ars(analysis.fair_price)}**"
        if analysis.store_count <= 3:
            line += " — " + t.FAIR_PRICE_RANGE.format(
                low=md_ars(analysis.price_min),
                high=md_ars(analysis.price_max),
                count=analysis.store_count,
            )
        return line
    return f"*{t.SINGLE_STORE_NOTE}*"


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
        t.PICK_ITEM, list(by_id), index=index, format_func=lambda i: by_id[i].query
    )
    st.session_state["selected_item_id"] = choice
    return by_id[choice]


def _margin_line(service: ServicePrice, tier_margin: TierMargin, names: dict[str, str]) -> str:
    """One repair's margin against one tier, as markdown (both prices and the
    admin-typed label go through the dollar escape)."""
    verb = t.MARGIN_VERB_GAIN if tier_margin.margin >= 0 else t.MARGIN_VERB_LOSS
    return t.MARGIN_LINE.format(
        label=escape_md_dollars(service.label),
        service=md_ars(service.price_ars),
        verb=verb,
        amount=md_ars(abs(tier_margin.margin)),
        store=names.get(tier_margin.part_source, tier_margin.part_source),
        tier=t.TIER_LABELS[tier_margin.tier],
    )


def _render_margins(
    services: Sequence[ServicePrice], analyses: Sequence[TierAnalysis], names: dict[str, str]
) -> None:
    with st.container(border=True):
        st.subheader(t.MARGIN_HEADER)
        for service in services:
            for tier_margin in margins_for(service.price_ars, analyses):
                st.markdown(_margin_line(service, tier_margin, names))


def render() -> None:
    radar.page_title(t.NAV_DETAIL)
    names = source_names()
    with data.open_session() as session:
        item = _select_item(session)
        if item is None:
            st.markdown(f"*{t.NO_DATA_AT_ALL}*")
            return
        day = latest_day(session, item.id)
        if day is None:
            st.markdown(f"*{t.NO_DATA_TODAY}*")
            return
        analyses = analyze_item(listings_for_day(session, item.id, day))

        reference = _reference_point()
        coords = _store_coords()
        sort_key = _sort_key(
            st.segmented_control(
                t.SORT_LABEL,
                [t.SORT_PRICE, t.SORT_DISTANCE],
                default=t.SORT_PRICE,
                selection_mode="single",
            )
        )

        for analysis in analyses:
            with st.container(border=True):
                st.subheader(_tier_heading(analysis))
                for offer in _sorted_offers(analysis.offers, sort_key, reference, coords):
                    st.markdown(
                        _offer_line(
                            offer, names, _distance_for(offer.source_slug, reference, coords)
                        )
                    )
                st.markdown(_fair_price_highlight(analysis))

        services = session.scalars(
            select(ServicePrice).where(ServicePrice.tracked_item_id == item.id)
        ).all()
        if services:
            radar.rule()
            _render_margins(services, analyses, names)

        radar.rule()
        st.subheader(t.TREND_HEADER)
        for analysis in analyses:
            points = tier_trends(session, item.id, analysis.tier, day)
            shown = [p for p in points if p.direction]
            if shown:
                trend_text = " · ".join(
                    f"{p.direction} {str(abs(p.pct_change)).replace('.', ',')}% "
                    # The real gap, not the nominal window — a stored day can
                    # land up to _TREND_TOLERANCE_DAYS off the target
                    # (report.py does the same for the exact same reason).
                    + t.TREND_VS.format(days=(day - p.compared_date).days)
                    for p in shown
                )
                st.caption(f"{t.TIER_LABELS[analysis.tier]}: {trend_text}")
            with st.expander(f"{t.TREND_CHART_LABEL} — {t.TIER_LABELS[analysis.tier]}"):
                series = data.fair_price_series(session, item.id, analysis.tier, day)
                if len(series) >= 2:
                    st.altair_chart(_trend_chart(series), width="stretch")
                else:
                    st.markdown(f"*{t.NO_TREND}*")
