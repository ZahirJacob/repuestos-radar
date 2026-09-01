"""Precios: one card per tracked part — best price, margin, warnings at a glance."""

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from repuestos_radar.analysis import TierAnalysis, analyze_item, latest_day, listings_for_day
from repuestos_radar.dashboard import data, text_es
from repuestos_radar.dashboard.detail import source_names
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.relevance import Relevance
from repuestos_radar.report import TIER_LABELS_ES, format_ars


def _best_offer(analyses: list[TierAnalysis]):
    offers = [o for a in analyses for o in a.offers if not o.outlier]
    return min(offers, key=lambda o: o.price, default=None)


def _best_margin(session: Session, item_id: int, analyses: list[TierAnalysis]):
    services = session.scalars(
        select(ServicePrice).where(ServicePrice.tracked_item_id == item_id)
    ).all()
    margins = [m for s in services for m in margins_for(s.price_ars, analyses)]
    return max(margins, key=lambda m: m.margin, default=None)


def _needs_review(analyses: list[TierAnalysis]) -> bool:
    return any(
        offer.outlier or offer.relevance == Relevance.LOW_CONFIDENCE.value
        for analysis in analyses
        for offer in analysis.offers
    )


def render() -> None:
    from repuestos_radar.dashboard.app import PAGES  # late: app builds PAGES first

    st.title(text_es.NAV_PRICES)
    with data.open_session() as session:
        items = session.scalars(
            select(TrackedItem).where(TrackedItem.active).order_by(TrackedItem.id)
        ).all()
        for item in items:
            with st.container(border=True):
                st.subheader(item.query)
                day = latest_day(session, item.id)
                analyses = analyze_item(listings_for_day(session, item.id, day)) if day else []
                best = _best_offer(analyses)
                if best is None:
                    st.markdown(f"*{text_es.NO_DATA_TODAY}*")
                else:
                    tier_label = TIER_LABELS_ES[best.tier]
                    store = source_names().get(best.source_slug, best.source_slug)
                    st.markdown(
                        f"{text_es.BEST_PREFIX} **{format_ars(best.price)}** — "
                        f"{store} ({tier_label})"
                    )
                    margin = _best_margin(session, item.id, analyses)
                    if margin is not None:
                        amount = format_ars(abs(margin.margin))
                        if margin.margin >= 0:
                            st.markdown(f":green[{text_es.MARGIN_GAIN.format(amount=amount)}]")
                        else:
                            st.markdown(f":red[{text_es.MARGIN_LOSS.format(amount=amount)}]")
                    if _needs_review(analyses):
                        st.markdown(f":orange[{text_es.NEEDS_REVIEW_DOT}]")
                if st.button(text_es.SEE_DETAIL, key=f"detail-{item.id}", use_container_width=True):
                    st.session_state["selected_item_id"] = item.id
                    st.switch_page(PAGES["detail"])
