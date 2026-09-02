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
    error, saved = admin._add_service(session, "Cambio módulo A32", item.id, "85000")
    assert error is None
    assert saved == text_es.SERVICE_SAVED
    (service,) = list_services(session)
    assert service.price_ars == Decimal("85000.00")


def test_admin_add_service_surfaces_overwrite_of_existing_label(session):
    """Re-adding an existing label replaces its price and part link — the UI
    must say so instead of a plain "Guardado."."""
    first = TrackedItem(query="modulo a32")
    second = TrackedItem(query="modulo a52")
    session.add_all([first, second])
    session.commit()
    admin._add_service(session, "Cambio módulo", first.id, "85000")
    error, saved = admin._add_service(session, "Cambio módulo", second.id, "90000")
    assert error is None
    assert saved == text_es.SERVICE_UPDATED_EXISTING
    (service,) = list_services(session)
    assert service.tracked_item_id == second.id
    assert service.price_ars == Decimal("90000.00")


def test_admin_add_service_rejects_bad_price(session):
    item = TrackedItem(query="modulo a32")
    session.add(item)
    session.commit()
    assert admin._add_service(session, "Cambio", item.id, "nan") == (
        text_es.PRICE_NOT_A_NUMBER,
        None,
    )
    assert admin._add_service(session, "  ", item.id, "100") == (text_es.LABEL_EMPTY, None)
    assert list_services(session) == []


def test_admin_set_price_reports_missing_row(session):
    """Editing a row another session just deleted must not report "Guardado."."""
    assert admin._set_service_price(session, 42, "80000") == text_es.SERVICE_NOT_FOUND


def test_flash_roundtrip():
    state = {}
    admin._flash(state, "Guardado.")
    assert admin._pop_flash(state) == ("success", "Guardado.")
    assert admin._pop_flash(state) is None
    admin._flash(state, "Ya está.", kind="info")
    assert admin._pop_flash(state) == ("info", "Ya está.")


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
