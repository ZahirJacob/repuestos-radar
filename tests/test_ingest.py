"""Tests for the end-to-end ingestion runner. Fake adapters + SQLite in-memory."""

from datetime import date
from decimal import Decimal

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import repuestos_radar.ingest
from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.ingest import (
    RunReport,
    SourceReport,
    _select_sources,
    build_adapters,
    format_report,
    main,
    run_ingestion,
)
from repuestos_radar.models import Base, Listing, TrackedItem
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.sources import Source


def make_source(
    slug: str, platform: str = "woocommerce", *, blocked: frozenset[str] = frozenset()
) -> Source:
    return Source(
        slug=slug,
        name=slug.title(),
        url=f"https://{slug}.example.com.ar",
        platform=platform,
        address="Calle Falsa 123",
        city="Rosario",
        trust_notes="Test shop.",
        blocked_channels=blocked,
    )


BOTH = frozenset({"daily", "quick"})


def make_listing(slug: str, external_id: str, title: str) -> NormalizedListing:
    return NormalizedListing(
        source_slug=slug,
        external_id=external_id,
        title=title,
        price=Decimal("10000"),
        currency="ARS",
        condition=Condition.UNKNOWN,
        url=f"https://{slug}.example.com.ar/producto/{external_id}",
        fetched_at=date(2026, 8, 31),
    )


class FakeAdapter:
    """Structural stand-in for the Adapter protocol (plus close/context manager).

    ``listings_by_query`` maps a query to the listings fetch() returns;
    ``skipped_by_query`` sets the per-fetch malformed counter; ``error``
    makes every fetch raise it.
    """

    def __init__(
        self,
        slug: str,
        listings_by_query: dict[str, list[NormalizedListing]] | None = None,
        skipped_by_query: dict[str, int] | None = None,
        error: Exception | None = None,
        pages_fetched: int | None = None,
        budget_exhausted: bool | None = None,
    ) -> None:
        self.source = make_source(slug)
        self.skipped = 0
        self.fetch_calls: list[str] = []
        self.closed = False
        self._listings = listings_by_query or {}
        self._skipped = skipped_by_query or {}
        self._error = error
        # Crawl-based adapters expose these attributes; search-based ones do
        # not, so the base fake only grows them when the test asks for them.
        if pages_fetched is not None:
            self.pages_fetched = pages_fetched
        if budget_exhausted is not None:
            self.budget_exhausted = budget_exhausted

    def fetch(self, query: str) -> list[NormalizedListing]:
        self.fetch_calls.append(query)
        if self._error is not None:
            raise self._error
        self.skipped = self._skipped.get(query, 0)
        return self._listings.get(query, [])

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_item(session: Session, query: str, active: bool = True) -> TrackedItem:
    item = TrackedItem(query=query, active=active)
    session.add(item)
    session.commit()
    return item


def happy_adapters() -> tuple[FakeAdapter, FakeAdapter]:
    """Two sources serving two tracked queries with known relevance labels."""
    shop_a = FakeAdapter(
        "shop-a",
        listings_by_query={
            "modulo a34": [
                make_listing("shop-a", "1", "Modulo Samsung A34 Oled"),  # match
                make_listing("shop-a", "2", "Funda Samsung A34"),  # reject
            ],
            "bateria iphone 11": [
                make_listing("shop-a", "3", "Bateria iPhone 11 Original"),  # match
            ],
        },
        skipped_by_query={"modulo a34": 2},
    )
    shop_b = FakeAdapter(
        "shop-b",
        listings_by_query={
            "modulo a34": [
                make_listing("shop-b", "9", "Modulo Vidrio A34"),  # low_confidence (soft term)
            ],
        },
    )
    return shop_a, shop_b


def test_happy_path_persists_rows_with_relevance_labels(session: Session) -> None:
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11")
    shop_a, shop_b = happy_adapters()

    report = run_ingestion(session, [shop_a, shop_b])

    assert report.ok
    assert report.active_items == 2
    rows = session.scalars(select(Listing)).all()
    assert len(rows) == 4
    by_key = {(r.source_slug, r.external_id): r for r in rows}
    assert by_key[("shop-a", "1")].relevance == "match"
    assert by_key[("shop-a", "2")].relevance == "reject"
    assert by_key[("shop-a", "3")].relevance == "match"
    assert by_key[("shop-b", "9")].relevance == "low_confidence"
    assert all(isinstance(r.relevance_score, float) for r in rows)
    # Each adapter instance is reused across all tracked items, in order.
    assert shop_a.fetch_calls == ["modulo a34", "bateria iphone 11"]
    assert shop_b.fetch_calls == ["modulo a34", "bateria iphone 11"]


def test_happy_path_report_counts(session: Session) -> None:
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11")
    shop_a, shop_b = happy_adapters()

    report = run_ingestion(session, [shop_a, shop_b])

    report_a, report_b = report.sources
    assert report_a.slug == "shop-a"
    assert report_a.items_queried == 2
    assert report_a.listings_fetched == 3
    assert report_a.malformed_skipped == 2
    assert report_a.inserted == 3
    assert report_a.already_stored == 0
    assert (report_a.matches, report_a.low_confidence, report_a.rejects) == (2, 0, 1)
    assert report_a.failure is None
    assert report_b.slug == "shop-b"
    assert report_b.items_queried == 2
    assert report_b.listings_fetched == 1
    assert report_b.malformed_skipped == 0
    assert (report_b.matches, report_b.low_confidence, report_b.rejects) == (0, 1, 0)


def test_one_failing_source_does_not_abort_the_run(session: Session) -> None:
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11")
    shop_a, _ = happy_adapters()
    broken = FakeAdapter("broken", error=AdapterError("broken: HTTP 503", slug="broken"))

    report = run_ingestion(session, [broken, shop_a])

    assert report.ok
    broken_report, ok_report = report.sources
    assert broken_report.failure == "broken: HTTP 503"
    assert broken_report.inserted == 0
    # The failing source is given up for the run: no second fetch.
    assert broken.fetch_calls == ["modulo a34"]
    assert ok_report.failure is None
    assert ok_report.inserted == 3
    assert {r.source_slug for r in session.scalars(select(Listing))} == {"shop-a"}


def test_unexpected_exception_is_isolated_like_adapter_error(session: Session) -> None:
    add_item(session, "modulo a34")
    shop_a, _ = happy_adapters()
    weird = FakeAdapter("weird", error=RuntimeError("boom"))

    report = run_ingestion(session, [weird, shop_a])

    assert report.ok
    assert report.sources[0].failure == "unexpected RuntimeError: boom"
    assert report.sources[1].failure is None


def test_all_sources_failing_means_run_not_ok(session: Session) -> None:
    add_item(session, "modulo a34")
    adapters = [
        FakeAdapter("dead-1", error=AdapterError("dead-1: unreachable", slug="dead-1")),
        FakeAdapter("dead-2", error=AdapterError("dead-2: robots disallow", slug="dead-2")),
    ]

    report = run_ingestion(session, adapters)

    assert not report.ok
    assert all(s.failure is not None for s in report.sources)
    assert session.scalars(select(Listing)).all() == []
    assert "result=failure" in format_report(report)


def test_no_active_items_is_a_successful_no_op(session: Session) -> None:
    add_item(session, "modulo a34", active=False)
    shop_a, shop_b = happy_adapters()

    report = run_ingestion(session, [shop_a, shop_b])

    assert report.ok
    assert report.active_items == 0
    assert shop_a.fetch_calls == []
    assert shop_b.fetch_calls == []
    assert session.scalars(select(Listing)).all() == []
    assert "no active tracked items" in format_report(report)


def test_inactive_items_are_not_queried(session: Session) -> None:
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11", active=False)
    shop_a, _ = happy_adapters()

    run_ingestion(session, [shop_a])

    assert shop_a.fetch_calls == ["modulo a34"]


def test_second_run_same_day_inserts_nothing(session: Session) -> None:
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11")

    first = run_ingestion(session, list(happy_adapters()))
    second = run_ingestion(session, list(happy_adapters()))

    assert sum(s.inserted for s in first.sources) == 4
    assert second.ok
    assert sum(s.inserted for s in second.sources) == 0
    assert sum(s.already_stored for s in second.sources) == 4
    # Relevance is still counted on a re-run: classification happens before storage.
    assert sum(s.matches + s.low_confidence + s.rejects for s in second.sources) == 4
    assert len(session.scalars(select(Listing)).all()) == 4


def test_partial_progress_is_committed_before_a_failure(session: Session) -> None:
    """A crash mid-run must not lose the (item, source) saves already made."""
    add_item(session, "modulo a34")
    add_item(session, "bateria iphone 11")

    class FailsOnSecondFetch(FakeAdapter):
        def fetch(self, query: str) -> list[NormalizedListing]:
            if len(self.fetch_calls) == 1:
                self.fetch_calls.append(query)
                raise AdapterError("flaky: timeout", slug=self.source.slug)
            return super().fetch(query)

    flaky = FailsOnSecondFetch(
        "flaky",
        listings_by_query={"modulo a34": [make_listing("flaky", "1", "Modulo Samsung A34")]},
    )

    report = run_ingestion(session, [flaky])

    assert not report.ok
    assert report.sources[0].items_queried == 1
    assert report.sources[0].inserted == 1
    session.rollback()  # committed work must survive a rollback
    assert len(session.scalars(select(Listing)).all()) == 1


def test_format_report_is_grep_able(session: Session) -> None:
    add_item(session, "modulo a34")
    shop_a, _ = happy_adapters()
    broken = FakeAdapter("broken", error=AdapterError("broken: HTTP 503", slug="broken"))

    text = format_report(run_ingestion(session, [shop_a, broken]))

    assert (
        "source=shop-a items=1 fetched=2 skipped=2 inserted=2 already_stored=0 "
        "match=1 low_confidence=0 reject=1 status=ok"
    ) in text
    assert "source=broken items=0" in text
    assert 'status=failed error="broken: HTTP 503"' in text
    assert (
        "summary: sources_ok=1/2 skipped=0 fetched=2 inserted=2 already_stored=0 result=success"
    ) in text
    assert "status=skipped" not in text


def test_format_report_lists_cloud_blocked_sources_as_skipped(session: Session) -> None:
    """Skipped stores get their own grep-able row (no counters) and the summary
    counts them apart: sources_ok only covers sources actually attempted."""
    add_item(session, "modulo a34")
    shop_a, _ = happy_adapters()

    report = run_ingestion(session, [shop_a], skipped=["evophone", "litoral-accesorios"])

    assert report.ok
    assert report.skipped == ["evophone", "litoral-accesorios"]
    assert [s.slug for s in report.sources] == ["shop-a"]
    text = format_report(report)
    lines = text.splitlines()
    assert "source=evophone status=skipped reason=cloud_blocked" in lines
    assert "source=litoral-accesorios status=skipped reason=cloud_blocked" in lines
    assert "summary: sources_ok=1/1 skipped=2 fetched=2 inserted=2" in text
    assert text.endswith("result=success")


def test_skipped_sources_do_not_rescue_a_failed_run(session: Session) -> None:
    """Exit-code semantics are unchanged: a skipped store is not an ok store."""
    add_item(session, "modulo a34")
    broken = FakeAdapter("broken", error=AdapterError("broken: HTTP 503", slug="broken"))

    report = run_ingestion(session, [broken], skipped=["evophone"])

    assert not report.ok
    assert "summary: sources_ok=0/1 skipped=1" in format_report(report)


def test_select_sources_default_run_leaves_out_daily_blocked_only() -> None:
    """Only the daily channel matters here: a store blocked for the quick
    search alone (or on both channels) is skipped only when daily is named."""
    registry = [
        make_source("shop-a"),
        make_source("blocked", blocked=BOTH),
        make_source("daily-only", blocked=frozenset({"daily"})),
        make_source("quick-only", blocked=frozenset({"quick"})),
        make_source("shop-c"),
    ]

    chosen, skipped = _select_sources(registry, None)

    assert [s.slug for s in chosen] == ["shop-a", "quick-only", "shop-c"]
    assert skipped == ["blocked", "daily-only"]


def test_select_sources_explicit_slug_still_runs_a_cloud_blocked_store() -> None:
    """--source SLUG is how a blocked store gets re-tested, so it must run."""
    registry = [make_source("shop-a"), make_source("blocked", blocked=BOTH)]

    chosen, skipped = _select_sources(registry, ["blocked"])

    assert [s.slug for s in chosen] == ["blocked"]
    assert skipped == []


def test_crawl_coverage_is_reported_for_crawl_based_sources(session: Session) -> None:
    add_item(session, "modulo a34")
    crawler = FakeAdapter(
        "crawler",
        listings_by_query={"modulo a34": [make_listing("crawler", "1", "Modulo Samsung A34")]},
        pages_fetched=12,
        budget_exhausted=False,
    )
    shop_a, _ = happy_adapters()

    report = run_ingestion(session, [crawler, shop_a])

    crawl_report, search_report = report.sources
    assert crawl_report.pages_fetched == 12
    assert crawl_report.budget_exhausted is False
    # Search-based adapters have no crawl attributes: the fields stay unset.
    assert search_report.pages_fetched is None
    assert search_report.budget_exhausted is None

    text = format_report(report)
    crawler_line = next(line for line in text.splitlines() if "source=crawler" in line)
    assert "pages=12 crawl=full" in crawler_line
    assert crawler_line.count("\n") == 0
    shop_line = next(line for line in text.splitlines() if "source=shop-a" in line)
    assert "pages=" not in shop_line
    assert "crawl=" not in shop_line


def test_exhausted_crawl_budget_is_reported_as_partial(session: Session) -> None:
    add_item(session, "modulo a34")
    crawler = FakeAdapter(
        "crawler",
        listings_by_query={"modulo a34": [make_listing("crawler", "1", "Modulo Samsung A34")]},
        pages_fetched=80,
        budget_exhausted=True,
    )

    text = format_report(run_ingestion(session, [crawler]))

    crawler_line = next(line for line in text.splitlines() if "source=crawler" in line)
    assert "pages=80 crawl=partial" in crawler_line
    assert "status=ok" in crawler_line


def test_failed_crawl_still_reports_its_coverage(session: Session) -> None:
    """A source whose crawl dies mid-way must still report how far it got —
    that is exactly the run where pages= is most useful."""
    add_item(session, "modulo a34")
    crawler = FakeAdapter(
        "crawler",
        error=AdapterError("crawler: giving up after 3 attempts", slug="crawler"),
        pages_fetched=37,
        budget_exhausted=False,
    )

    report = run_ingestion(session, [crawler])

    (crawl_report,) = report.sources
    assert crawl_report.failure is not None
    assert crawl_report.pages_fetched == 37
    assert crawl_report.budget_exhausted is False
    line = next(line for line in format_report(report).splitlines() if "source=crawler" in line)
    assert "pages=37 crawl=full" in line
    assert "status=failed" in line


def test_unexpected_failure_also_reports_crawl_coverage(session: Session) -> None:
    add_item(session, "modulo a34")
    crawler = FakeAdapter(
        "crawler", error=RuntimeError("boom"), pages_fetched=80, budget_exhausted=True
    )

    report = run_ingestion(session, [crawler])

    (crawl_report,) = report.sources
    assert crawl_report.pages_fetched == 80
    assert crawl_report.budget_exhausted is True
    line = next(line for line in format_report(report).splitlines() if "source=crawler" in line)
    assert "pages=80 crawl=partial" in line
    assert "status=failed" in line


def test_save_failure_mid_transaction_is_rolled_back_and_next_source_persists(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save that dirties the transaction and then fails must not poison the
    session for the sources that follow (interpretation call 3)."""
    add_item(session, "modulo a34")
    shop_a, shop_b = happy_adapters()
    real_save = repuestos_radar.ingest.save_classified_listings
    calls: list[int] = []

    def poisoned_save(sess, tracked_item_id, classified):
        inserted = real_save(sess, tracked_item_id, classified)
        calls.append(tracked_item_id)
        if len(calls) == 1:  # first source only: rows are pending, then the DB "dies"
            raise OperationalError("INSERT INTO listings ...", {}, Exception("db went away"))
        return inserted

    monkeypatch.setattr(repuestos_radar.ingest, "save_classified_listings", poisoned_save)

    report = run_ingestion(session, [shop_a, shop_b])

    assert report.ok
    failed, succeeded = report.sources
    assert failed.failure is not None
    assert failed.failure.startswith("unexpected OperationalError:")
    assert failed.inserted == 0
    assert succeeded.failure is None
    assert succeeded.inserted == 1
    # shop-a's pending rows were rolled back; only shop-b's commit stuck.
    assert {r.source_slug for r in session.scalars(select(Listing))} == {"shop-b"}
    # The multi-line SQLAlchemy message is collapsed: one report line per source.
    assert "\n" not in failed.failure
    assert "[SQL:" in failed.failure
    shop_a_lines = [line for line in format_report(report).splitlines() if "source=shop-a" in line]
    assert len(shop_a_lines) == 1
    assert "status=failed" in shop_a_lines[0]


def test_format_report_failure_line_swaps_double_quotes() -> None:
    report = RunReport(
        active_items=1,
        sources=[SourceReport(slug="s", failure='HTTP 503 "Service Unavailable"')],
    )

    text = format_report(report)

    line = next(line for line in text.splitlines() if line.startswith("source=s"))
    assert "error=\"HTTP 503 'Service Unavailable'\"" in line


def test_build_adapters_maps_platforms() -> None:
    adapters = build_adapters([make_source("wix-shop", "wix"), make_source("woo-shop")])
    try:
        assert type(adapters[0]).__name__ == "WixAdapter"
        assert type(adapters[1]).__name__ == "WooCommerceAdapter"
        assert [a.source.slug for a in adapters] == ["wix-shop", "woo-shop"]
    finally:
        for adapter in adapters:
            adapter.close()


def test_build_adapters_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="shopify"):
        build_adapters([make_source("woo-shop"), make_source("odd-shop", "shopify")])


def test_build_adapters_closes_built_adapters_on_any_constructor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[FakeAdapter] = []

    def fake_adapter_for(source: Source) -> FakeAdapter:
        if source.slug == "boom":
            raise RuntimeError("constructor exploded")
        adapter = FakeAdapter(source.slug)
        built.append(adapter)
        return adapter

    monkeypatch.setattr(repuestos_radar.ingest, "adapter_for", fake_adapter_for)

    with pytest.raises(RuntimeError, match="constructor exploded"):
        build_adapters([make_source("ok-shop"), make_source("boom")])

    assert [adapter.closed for adapter in built] == [True]


def test_empty_report_ok_property() -> None:
    assert RunReport(active_items=0, sources=[]).ok


# --- main() wiring, against a temp SQLite file ------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Point DATABASE_URL at a temp SQLite file and return its engine URL."""
    url = f"sqlite+pysqlite:///{tmp_path / 'radar.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def seed_item(url: str, query: str) -> None:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(TrackedItem(query=query))
        session.commit()
    engine.dispose()


def test_main_exit_0_and_closes_adapters(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    shop_a, shop_b = happy_adapters()
    monkeypatch.setattr(repuestos_radar.ingest, "load_sources", lambda: [])
    monkeypatch.setattr(repuestos_radar.ingest, "build_adapters", lambda sources: [shop_a, shop_b])

    exit_code = main([])

    assert exit_code == 0
    assert shop_a.closed and shop_b.closed
    out = capsys.readouterr().out
    assert "result=success" in out
    assert "source=shop-a" in out


def test_main_exit_1_when_every_source_fails(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    dead = FakeAdapter("dead", error=AdapterError("dead: unreachable", slug="dead"))
    monkeypatch.setattr(repuestos_radar.ingest, "load_sources", lambda: [])
    monkeypatch.setattr(repuestos_radar.ingest, "build_adapters", lambda sources: [dead])

    exit_code = main([])

    assert exit_code == 1
    assert dead.closed
    assert "result=failure" in capsys.readouterr().out


def patch_registry(
    monkeypatch, slugs: list[str], blocked: frozenset[str] = frozenset()
) -> list[list[str]]:
    """Fake registry + adapter factory; returns the slug lists build_adapters saw."""
    built: list[list[str]] = []

    def fake_build(sources):
        built.append([s.slug for s in sources])
        return [FakeAdapter(s.slug) for s in sources]

    monkeypatch.setattr(
        repuestos_radar.ingest,
        "load_sources",
        lambda: [
            make_source(slug, blocked=BOTH if slug in blocked else frozenset()) for slug in slugs
        ],
    )
    monkeypatch.setattr(repuestos_radar.ingest, "build_adapters", fake_build)
    return built


def test_main_source_filter_runs_only_the_named_source(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "shop-b", "shop-c"])

    exit_code = main(["--source", "shop-b"])

    assert exit_code == 0
    assert built == [["shop-b"]]
    out = capsys.readouterr().out
    assert "source=shop-b" in out
    assert "source=shop-a" not in out


def test_main_source_filter_is_repeatable_and_keeps_registry_order(
    monkeypatch, capsys, cli_db
) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "shop-b", "shop-c"])

    exit_code = main(["--source", "shop-c", "--source", "shop-a", "--source", "shop-a"])

    assert exit_code == 0
    assert built == [["shop-a", "shop-c"]]  # registry order, duplicates folded


def test_main_unknown_source_slug_aborts_before_any_fetch(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "shop-b"])

    exit_code = main(["--source", "shop-a", "--source", "nope"])

    assert exit_code == 1
    assert built == []  # no adapters were built, nothing was fetched
    out = capsys.readouterr().out
    assert "ingestion aborted (config error)" in out
    assert "nope" in out


def test_main_without_source_flag_runs_everything(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "shop-b"])

    exit_code = main([])

    assert exit_code == 0
    assert built == [["shop-a", "shop-b"]]


def test_main_default_run_skips_cloud_blocked_and_reports_them(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "blocked", "shop-c"], frozenset({"blocked"}))

    exit_code = main([])

    assert exit_code == 0
    assert built == [["shop-a", "shop-c"]]  # no adapter is even built for the blocked store
    out = capsys.readouterr().out
    assert "source=blocked status=skipped reason=cloud_blocked" in out
    assert "sources_ok=2/2 skipped=1" in out


def test_main_default_run_with_every_store_blocked_is_a_failure(
    monkeypatch, capsys, cli_db
) -> None:
    """Policy edge: nothing attempted means nothing ok, so the run fails."""
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["a", "b"], frozenset({"a", "b"}))

    assert main([]) == 1
    assert built == [[]]
    assert "sources_ok=0/0 skipped=2" in capsys.readouterr().out


def test_main_explicit_source_runs_a_cloud_blocked_store(monkeypatch, capsys, cli_db) -> None:
    seed_item(cli_db, "modulo a34")
    built = patch_registry(monkeypatch, ["shop-a", "blocked"], frozenset({"blocked"}))

    exit_code = main(["--source", "blocked"])

    assert exit_code == 0
    assert built == [["blocked"]]
    out = capsys.readouterr().out
    assert "source=blocked items=1" in out
    assert "status=skipped" not in out
    assert "sources_ok=1/1 skipped=0" in out


def test_main_exit_1_on_config_error(monkeypatch, capsys) -> None:
    def bad_registry():
        raise ValueError("duplicate source slug 'x'")

    monkeypatch.setattr(repuestos_radar.ingest, "load_sources", bad_registry)

    assert main([]) == 1
    assert "ingestion aborted (config error)" in capsys.readouterr().out


def test_main_exit_1_on_yaml_syntax_error(monkeypatch, capsys) -> None:
    def bad_yaml():
        raise yaml.YAMLError("while parsing a block mapping\nfound unexpected ':'")

    monkeypatch.setattr(repuestos_radar.ingest, "load_sources", bad_yaml)

    assert main([]) == 1
    out = capsys.readouterr().out
    assert "ingestion aborted (config error)" in out
    # The multi-line YAML message is collapsed onto the single abort line.
    assert "while parsing a block mapping found unexpected ':'" in out


def test_main_exit_1_when_database_unreachable(monkeypatch, capsys, tmp_path) -> None:
    # A SQLite file inside a missing directory cannot be opened -> OperationalError.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'missing' / 'x.db'}")
    shop_a, _ = happy_adapters()
    monkeypatch.setattr(repuestos_radar.ingest, "load_sources", lambda: [])
    monkeypatch.setattr(repuestos_radar.ingest, "build_adapters", lambda sources: [shop_a])

    assert main([]) == 1
    assert shop_a.closed  # the ExitStack still closes adapters on the way out
    assert "ingestion aborted (database error)" in capsys.readouterr().out


def test_run_ingestion_classifies_with_the_items_kind(session: Session) -> None:
    """A phone item rejects the spare parts that share its model words; the
    same titles under a part item keep matching (the kind is what changed)."""
    session.add(TrackedItem(query="samsung s24 ultra", kind="phone"))
    session.add(TrackedItem(query="bateria samsung s24 ultra"))
    session.commit()
    shop = FakeAdapter(
        "shop-a",
        listings_by_query={
            "samsung s24 ultra": [
                make_listing("shop-a", "1", "Samsung Galaxy S24 Ultra 256GB"),
                make_listing("shop-a", "2", "Bateria Samsung S24 Ultra"),
            ],
            "bateria samsung s24 ultra": [
                make_listing("shop-a", "2", "Bateria Samsung S24 Ultra"),
            ],
        },
    )

    report = run_ingestion(session, [shop])

    assert report.sources[0].failure is None
    by_item = {}
    for listing in session.scalars(select(Listing)):
        by_item[(listing.tracked_item.query, listing.external_id)] = listing.relevance
    assert by_item == {
        ("samsung s24 ultra", "1"): "match",
        ("samsung s24 ultra", "2"): "reject",
        ("bateria samsung s24 ultra", "2"): "match",
    }
