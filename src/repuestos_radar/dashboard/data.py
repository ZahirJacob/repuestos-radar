"""Session plumbing and small query helpers for the dashboard pages."""

from datetime import date, timedelta
from decimal import Decimal

import streamlit as st
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from repuestos_radar.analysis import analyze_item, listings_for_day
from repuestos_radar.dashboard import demo
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing


@st.cache_resource
def cached_engine() -> Engine:
    if demo.is_demo():
        return demo.engine()
    engine = get_engine()
    init_db(engine)
    return engine


def open_session() -> Session:
    engine = cached_engine()
    if demo.is_demo():
        demo.refresh_if_stale(engine, date.today())
    return get_session_factory(engine)()


def overall_latest_day(session: Session) -> date | None:
    """Most recent day with any stored listing at all — the freshness footer."""
    return session.scalar(select(func.max(Listing.fetched_date)))


def fair_price_series(
    session: Session, tracked_item_id: int, tier: str, end_day: date, days: int = 30
) -> list[tuple[date, Decimal]]:
    """(day, fair price) for the tier over the trailing window, for the chart."""
    stored_days = session.scalars(
        select(Listing.fetched_date)
        .where(
            Listing.tracked_item_id == tracked_item_id,
            Listing.fetched_date.between(end_day - timedelta(days=days), end_day),
        )
        .distinct()
        .order_by(Listing.fetched_date)
    ).all()
    series = []
    for day in stored_days:
        for analysis in analyze_item(listings_for_day(session, tracked_item_id, day)):
            if analysis.tier == tier and analysis.fair_price is not None:
                series.append((day, analysis.fair_price))
    return series
