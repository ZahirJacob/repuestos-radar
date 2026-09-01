"""Part detail: stores by tier, fair price, margins, trend — M3 priority order."""

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
from repuestos_radar.dashboard import data, text_es
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.relevance import Relevance
from repuestos_radar.report import TIER_LABELS_ES, format_ars
from repuestos_radar.sources import load_sources


@st.cache_data
def source_names() -> dict[str, str]:
    return {source.slug: source.name for source in load_sources()}


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

        for analysis in analyses:
            st.subheader(TIER_LABELS_ES[analysis.tier])
            for offer in analysis.offers:
                st.markdown(_offer_line(offer, names, distance_text=None))
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
                    + text_es.TREND_VS.format(days=p.days_back)
                    for p in shown
                )
                st.caption(f"{TIER_LABELS_ES[analysis.tier]}: {trend_text}")
            with st.expander(f"{text_es.TREND_CHART_LABEL} — {TIER_LABELS_ES[analysis.tier]}"):
                series = data.fair_price_series(session, item.id, analysis.tier, day)
                if len(series) >= 2:
                    frame = pd.DataFrame(
                        {
                            text_es.TREND_CHART_DAY_COLUMN: [d for d, _ in series],
                            text_es.TREND_CHART_PRICE_COLUMN: [float(p) for _, p in series],
                        }
                    ).set_index(text_es.TREND_CHART_DAY_COLUMN)
                    st.line_chart(frame)
                else:
                    st.markdown(f"*{text_es.NO_TREND}*")
