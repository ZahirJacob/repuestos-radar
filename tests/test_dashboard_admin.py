"""Admin-page tests: pure helpers pinned directly (same rationale as the
detail page — AppTest cannot reach function-based pages; see
tests/test_dashboard_detail.py for the full note)."""

from decimal import Decimal

import pytest

from repuestos_radar.dashboard import admin, text_es
from repuestos_radar.dashboard.quicksearch import QuickSearchReport, QuickSourceReport
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import TrackedItem
from repuestos_radar.services import list_services


@pytest.fixture()
def session():
    engine = get_engine("sqlite://")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


def test_price_error_text_maps_reasons():
    assert admin._price_error("not a number") == text_es.PRICE_NOT_A_NUMBER
    assert admin._price_error("not positive") == text_es.PRICE_NOT_POSITIVE


def test_admin_add_service_roundtrip(session):
    item = TrackedItem(query="modulo a32")
    session.add(item)
    session.commit()
    error = admin._add_service(session, "Cambio módulo A32", item.id, "85000")
    assert error is None
    (service,) = list_services(session)
    assert service.price_ars == Decimal("85000.00")


def test_admin_add_service_rejects_bad_price(session):
    item = TrackedItem(query="modulo a32")
    session.add(item)
    session.commit()
    assert admin._add_service(session, "Cambio", item.id, "nan") == text_es.PRICE_NOT_A_NUMBER
    assert admin._add_service(session, "  ", item.id, "100") == text_es.LABEL_EMPTY
    assert list_services(session) == []


def test_skipped_note_lists_crawl_only_sources():
    report = QuickSearchReport(item_id=1, query="x")
    report.sources = [
        QuickSourceReport(slug="a", name="Tienda A", searched=True),
        QuickSourceReport(slug="b", name="Tienda B", searched=False),
        QuickSourceReport(slug="c", name="Tienda C", searched=False),
    ]
    assert admin._skipped_note(report) == text_es.QUICK_SEARCH_SKIPPED_NOTE.format(
        names="Tienda B, Tienda C"
    )
    report.sources = [QuickSourceReport(slug="a", name="Tienda A", searched=True)]
    assert admin._skipped_note(report) is None
