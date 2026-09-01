"""Quick search: parallel one-item search across search-capable sources."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.dashboard import quicksearch as quicksearch_module
from repuestos_radar.dashboard.quicksearch import (
    DAILY_CAP,
    SEARCHABLE_PLATFORMS,
    QuickSearchReport,
    QuickSourceReport,
    format_report,
    quick_search,
    runs_today,
)
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, QuickSearchRun, TrackedItem
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.sources import Source


def _source(slug: str, platform: str) -> Source:
    return Source(
        slug=slug,
        name=slug.title(),
        url=f"https://{slug}.example",
        platform=platform,
        address="x",
        city="Rosario",
        trust_notes="test",
    )


def _listing(slug: str, external_id: str, title: str, price: str) -> NormalizedListing:
    return NormalizedListing(
        source_slug=slug,
        external_id=external_id,
        title=title,
        price=Decimal(price),
        currency="ARS",
        condition=Condition.UNKNOWN,
        url=f"https://{slug}.example/p/{external_id}",
        fetched_at=date.today(),
    )


class FakeAdapter:
    def __init__(self, source: Source, listings=None, error: str | None = None):
        self.source = source
        self.skipped = 0
        self._listings = listings or []
        self._error = error
        self.closed = False

    def fetch(self, query: str) -> list[NormalizedListing]:
        if self._error:
            raise AdapterError(self._error, slug=self.source.slug)
        return list(self._listings)

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


@pytest.fixture()
def session():
    engine = get_engine("sqlite://")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


@pytest.fixture()
def item(session):
    tracked = TrackedItem(query="modulo a32")
    session.add(tracked)
    session.commit()
    return tracked


def test_searchable_platforms_exclude_tiendanube():
    assert "tiendanube" not in SEARCHABLE_PLATFORMS
    assert {"woocommerce", "wix"} == SEARCHABLE_PLATFORMS


def test_quick_search_stores_results_and_skips_crawl_only(session, item):
    woo = _source("shopa", "woocommerce")
    nube = _source("shopb", "tiendanube")
    adapters = [FakeAdapter(woo, [_listing("shopa", "1", "Modulo Samsung A32 incell", "20700")])]
    report = quick_search(session, item, [woo, nube], adapters=adapters)

    assert not report.capped
    by_slug = {s.slug: s for s in report.sources}
    assert by_slug["shopa"].searched and by_slug["shopa"].inserted == 1
    assert not by_slug["shopb"].searched
    stored = session.scalars(select(Listing)).all()
    assert len(stored) == 1 and stored[0].source_slug == "shopa"


def test_quick_search_records_a_run_and_caps_at_daily_limit(session, item):
    woo = _source("shopa", "woocommerce")
    for _ in range(DAILY_CAP):
        quick_search(session, item, [woo], adapters=[FakeAdapter(woo)])
    assert runs_today(session) == DAILY_CAP

    report = quick_search(session, item, [woo], adapters=[FakeAdapter(woo)])
    assert report.capped
    assert report.sources == []
    assert session.scalars(select(QuickSearchRun)).all().__len__() == DAILY_CAP


def test_one_failing_source_does_not_abort_the_others(session, item):
    good = _source("good", "woocommerce")
    bad = _source("bad", "wix")
    adapters = [
        FakeAdapter(good, [_listing("good", "9", "Modulo A32 oled", "30000")]),
        FakeAdapter(bad, error="bad: HTTP 500"),
    ]
    report = quick_search(session, item, [good, bad], adapters=adapters)
    by_slug = {s.slug: s for s in report.sources}
    assert by_slug["good"].inserted == 1
    assert by_slug["bad"].failure == "bad: HTTP 500"


def test_progress_callback_fires_once_per_searched_source(session, item):
    woo = _source("shopa", "woocommerce")
    nube = _source("shopb", "tiendanube")
    seen: list[str] = []
    quick_search(session, item, [woo, nube], adapters=[FakeAdapter(woo)], progress=seen.append)
    assert seen == ["Shopa"]


def test_adapters_are_closed(session, item):
    woo = _source("shopa", "woocommerce")
    fake = FakeAdapter(woo)
    quick_search(session, item, [woo], adapters=[fake])
    assert fake.closed


def test_unsupported_platform_aborts_before_spending_cap(session, item, monkeypatch):
    """A config error building an adapter (unsupported platform) must raise
    cleanly and record no QuickSearchRun row — never spend a cap slot on a
    run that never actually visited a store. adapters=None so the real
    build_adapters() runs; SEARCHABLE_PLATFORMS is patched so the bogus
    platform is treated as searchable (today's registry has no such gap,
    so this simulates a future misconfigured sources.yaml entry)."""
    bogus = _source("bogus", "not-a-real-platform")
    monkeypatch.setattr(
        quicksearch_module, "SEARCHABLE_PLATFORMS", frozenset({"not-a-real-platform"})
    )
    with pytest.raises(ValueError, match="not-a-real-platform"):
        quick_search(session, item, [bogus])
    assert runs_today(session) == 0
    assert session.scalars(select(QuickSearchRun)).all() == []


def test_format_report_lines_are_grepable():
    report = QuickSearchReport(item_id=3, query="modulo a32")
    report.sources = [
        QuickSourceReport(
            slug="shopa",
            name="Shopa",
            searched=True,
            fetched=2,
            inserted=1,
            matches=1,
            low_confidence=1,
        ),
        QuickSourceReport(slug="shopb", name="Shopb", searched=False),
        QuickSourceReport(slug="shopc", name="Shopc", searched=True, failure='HTTP "500"'),
    ]
    text = format_report(report)
    assert 'quick search: item=3 query="modulo a32"' in text
    assert (
        "source=shopa searched=yes fetched=2 inserted=1 match=1 low_confidence=1 status=ok" in text
    )
    assert "source=shopb searched=no reason=crawl-only" in text
    assert "source=shopc searched=yes status=failed error=\"HTTP '500'\"" in text


def test_format_report_capped():
    report = QuickSearchReport(item_id=3, query="modulo a32", capped=True)
    assert "daily cap reached" in format_report(report)
