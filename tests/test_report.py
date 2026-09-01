"""Tests for the Spanish daily report."""

from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem
from repuestos_radar.report import format_ars, render_report


@pytest.fixture()
def session():
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


# Local copy of the listing helper (tests never import from sibling test files).
def _store_listing(
    session, item_id, source, price, day, relevance="match", title="Modulo A32 OLED"
):
    session.add(
        Listing(
            tracked_item_id=item_id,
            source_slug=source,
            external_id=f"{source}-{price}",
            title=title,
            price=Decimal(price),
            currency="ARS",
            condition="unknown",
            url="https://example.test/p",
            fetched_date=day,
            relevance=relevance,
            relevance_score=1.0,
        )
    )


def test_format_ars_argentine_style():
    assert format_ars(Decimal("20700")) == "$20.700"
    assert format_ars(Decimal("1449999.50")) == "$1.450.000"
    assert format_ars(Decimal("900")) == "$900"
    # .50 rounds up: HALF_UP, not banker's HALF_EVEN (which would give $20.700).
    assert format_ars(Decimal("20700.50")) == "$20.701"


def test_report_renders_sections_margins_and_warnings(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    today = date(2026, 9, 1)
    _store_listing(session, item.id, "novocell", "45000", today, title="Modulo A32 OLED")
    _store_listing(session, item.id, "celuphone", "41000", today, title="Modulo A32 OLED")
    _store_listing(
        session,
        item.id,
        "gofix",
        "40000",
        today,
        title="Modulo A32 OLED",
        relevance="low_confidence",
    )
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("75000"))
    )
    session.commit()

    text = render_report(session, today=today)
    assert "modulo samsung a32" in text
    assert "$41.000" in text  # cheapest OLED store price, Argentine format
    assert "revisar" in text  # low-confidence flagged in words
    assert "Cambio módulo A32" in text
    assert "$34.000" in text  # margin with the cheapest OLED
    # Tier label in apposition so every tier (incl. "Sin calidad indicada")
    # parses as Spanish.
    assert "con el repuesto de Celuphone (OLED)" in text
    assert "gofix" not in text.replace("GoFix", "")  # store display names, not slugs


def test_report_says_when_a_day_has_no_data(session):
    session.add(TrackedItem(query="bateria iphone 11"))
    session.commit()
    text = render_report(session, today=date(2026, 9, 1))
    assert "sin datos" in text.lower()


def test_report_says_sin_datos_when_latest_day_has_only_rejects(session):
    # latest_day counts days whose listings are all relevance="reject", so
    # listings_for_day can come back empty for the day it returns. The report
    # must say "sin datos" for the item — never skip it silently or crash.
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    _store_listing(session, item.id, "novocell", "45000", date(2026, 9, 1), relevance="reject")
    session.commit()

    text = render_report(session)  # no today arg: exercises the latest_day path
    assert "sin datos" in text.lower()
    assert "modulo samsung a32" in text


def test_report_skips_paused_items(session):
    session.add(TrackedItem(query="modulo samsung a32", active=False))
    session.commit()
    text = render_report(session, today=date(2026, 9, 1))
    assert "modulo samsung a32" not in text


def test_report_flags_a_negative_margin(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    today = date(2026, 9, 1)
    _store_listing(session, item.id, "novocell", "80000", today, title="Modulo A32 Original")
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("75000"))
    )
    session.commit()

    text = render_report(session, today=today)
    assert "perdés" in text  # the report says it plainly, no "$-5.000"
    assert "$5.000" in text
    assert "más de lo que cobrás" in text  # voseo, standard comparative
    assert "una sola tienda" in text  # "tiendas" everywhere, never "negocio"


def test_trend_line_states_the_actual_day_gap(session):
    # The compared day can be up to 2 days off the nominal window; the report
    # must state the real gap, not claim "hace 7 días" for an 8-day-old price.
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    today = date(2026, 9, 1)
    for source in ("novocell", "celuphone"):
        _store_listing(session, item.id, source, "40000", date(2026, 8, 24))
        _store_listing(session, item.id, source, "44000", today)
    session.commit()

    text = render_report(session, today=today)
    assert "hace 8 días" in text
    assert "hace 7 días" not in text
