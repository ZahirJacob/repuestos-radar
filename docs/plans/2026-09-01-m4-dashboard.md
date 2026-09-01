# M4 — Dashboard + Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the phone-first Spanish Streamlit dashboard (login, home cards, part detail with distance, admin page) plus the ~1-minute quick search, per `docs/specs/2026-09-01-m4-dashboard-design.md`.

**Architecture:** A `dashboard/` package of thin Streamlit pages over the existing pure analysis functions (`analysis.py`, `margin.py`, `quality.py`). Quick search reuses the existing search-based adapters (WooCommerce Store API, Wix) run in parallel threads — one polite client per host — and stores through the same `save_classified_listings` path as the daily crawl. Tiendanube sources are crawl-only (platform robots.txt disallows `/search/`) and are skipped in quick mode with a visible note.

**Tech Stack:** Python 3.12, Streamlit (+ `streamlit-cookies-controller` for the 30-day login cookie, `streamlit-geolocation` for the location button), SQLAlchemy, httpx, pytest (incl. `streamlit.testing.v1.AppTest`), ruff.

**Spec:** `docs/specs/2026-09-01-m4-dashboard-design.md`

## Global Constraints

- Python 3.12+; run checks with `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` — all three must pass before every push.
- Conventional commits. **Never add AI attribution** (no "Generated with", no `Co-Authored-By`) to any commit or PR — check `git log` before pushing.
- No network access in any test. No HTTP from analysis or rendering code.
- Courtesy policy is untouchable: 1-second delay per host (`PoliteHttpClient` already enforces it), robots.txt honored, honest UA, skip-don't-work-around. Quick search adds at most `10` manual runs per Argentine calendar day.
- Every user-visible string is Spanish and lives in `src/repuestos_radar/dashboard/text_es.py` — no Spanish literals inside page code. Prices via `report.format_ars` ($20.700), dates `dd/mm/yyyy`, decimal comma.
- The dashboard imports analysis dataclasses; it never re-implements math and never parses `report.py` text.
- New DB tables go through `models.py` + `init_db` (create_all), same as `service_prices` did.
- Each PR branches from up-to-date `main`, is opened with `gh pr create`, and merges only after Lara's review (Mo too when Spanish copy changed).

---

# PR 1 — Quick search engine

Branch: `feat/quick-search`

### Task 1: `QuickSearchRun` model (the daily cap's memory)

**Files:**
- Modify: `src/repuestos_radar/models.py`
- Test: `tests/test_models.py` (append)

**Interfaces:**
- Consumes: `Base`, `_utcnow` (both already in `models.py`).
- Produces: `QuickSearchRun` with `id: int`, `tracked_item_id: int` (FK), `ran_on: date` (indexed), `ran_at: datetime` — Task 2 counts rows by `ran_on`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_models.py`, reusing that file's existing session fixture pattern)

```python
def test_quick_search_run_rows_store_day_and_timestamp(session):
    item = TrackedItem(query="modulo a32")
    session.add(item)
    session.flush()
    run = QuickSearchRun(tracked_item_id=item.id, ran_on=date(2026, 9, 1))
    session.add(run)
    session.commit()
    stored = session.get(QuickSearchRun, run.id)
    assert stored.ran_on == date(2026, 9, 1)
    assert stored.ran_at is not None
```

Add `from repuestos_radar.models import QuickSearchRun` (and `date` from `datetime`) to the test file imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'QuickSearchRun'`

- [ ] **Step 3: Implement** (append to `src/repuestos_radar/models.py`)

```python
class QuickSearchRun(Base):
    """One on-demand quick search, recorded to enforce the daily cap.

    ``ran_on`` is the Argentine calendar day the run counts against —
    computed by the caller, stored explicitly so the cap query never does
    timezone math in SQL (SQLite and Postgres disagree on datetime handling).
    """

    __tablename__ = "quick_search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(ForeignKey("tracked_items.id"))
    ran_on: Mapped[date] = mapped_column(Date, index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_schema.py tests/test_db.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/models.py tests/test_models.py
git commit -m "feat: quick_search_runs table for the daily quick-search cap"
```

### Task 2: quick-search orchestrator

**Files:**
- Create: `src/repuestos_radar/dashboard/__init__.py` (empty, one docstring line: `"""Phone-first Streamlit dashboard (M4)."""`)
- Create: `src/repuestos_radar/dashboard/quicksearch.py`
- Test: `tests/test_quicksearch.py`

**Interfaces:**
- Consumes: `build_adapters(sources)` from `ingest.py`; `apply_relevance(query, listings)` from `relevance.py`; `save_classified_listings(session, item_id, classified)` from `storage.py`; `AdapterError`; `QuickSearchRun`.
- Produces (dashboard admin page and CLI use these exact names):
  - `SEARCHABLE_PLATFORMS: frozenset[str]` = `{"woocommerce", "wix"}`
  - `DAILY_CAP: int` = `10`
  - `argentina_today() -> date`
  - `runs_today(session: Session) -> int`
  - `QuickSearchBusy(Exception)`
  - `QuickSourceReport(slug, name, searched: bool, fetched=0, malformed_skipped=0, inserted=0, matches=0, low_confidence=0, failure: str | None = None)`
  - `QuickSearchReport(item_id, query, capped: bool = False, sources: list[QuickSourceReport] = ...)`
  - `quick_search(session, item, sources, *, adapters=None, progress=None) -> QuickSearchReport`

- [ ] **Step 1: Write the failing tests**

`tests/test_quicksearch.py`. Fake adapters implement the `Adapter` protocol shape without HTTP; DB is the same in-memory SQLite pattern as `tests/test_storage.py` (engine via `get_engine("sqlite:///:memory:")` equivalent used there — copy that file's fixture style, do not import from other test files).

```python
"""Quick search: parallel one-item search across search-capable sources."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.dashboard.quicksearch import (
    DAILY_CAP,
    SEARCHABLE_PLATFORMS,
    QuickSearchBusy,
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
    assert SEARCHABLE_PLATFORMS == {"woocommerce", "wix"}


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quicksearch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'repuestos_radar.dashboard'`

- [ ] **Step 3: Implement `quicksearch.py`**

```python
"""On-demand quick search: one tracked item, all search-capable sources, ~1 minute.

The daily deep crawl is the source of complete history; this is the
counter-moment feature — a customer is waiting, so we query each store's own
search endpoint for ONE item and store the results through the exact same
classify-and-save path as the daily run.

Courtesy: sources run in parallel THREADS, but each source keeps its own
sequential PoliteHttpClient — per-host the 1-second delay is intact; no store
sees more load than a single polite visitor. Tiendanube storefronts are
skipped: the platform robots-disallows /search/, and their catalog crawl is
exactly the slow path this feature avoids (skip, don't work around).

A hard daily cap (DAILY_CAP runs per Argentine calendar day, recorded in the
quick_search_runs table) keeps the feature honest even if the button gets
tapped enthusiastically.
"""

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repuestos_radar.adapters import Adapter, AdapterError
from repuestos_radar.ingest import build_adapters
from repuestos_radar.models import QuickSearchRun, TrackedItem
from repuestos_radar.relevance import ClassifiedListing, Relevance, apply_relevance
from repuestos_radar.sources import Source
from repuestos_radar.storage import save_classified_listings

SEARCHABLE_PLATFORMS = frozenset({"woocommerce", "wix"})
DAILY_CAP = 10

_ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# One quick search at a time per server process; the Streamlit app runs in a
# single process, so a plain module lock is the whole story.
_RUN_LOCK = threading.Lock()


class QuickSearchBusy(Exception):
    """Another quick search is already running in this process."""


@dataclass(slots=True)
class QuickSourceReport:
    """What one source did during a quick search."""

    slug: str
    name: str
    searched: bool
    fetched: int = 0
    malformed_skipped: int = 0
    inserted: int = 0
    matches: int = 0
    low_confidence: int = 0
    failure: str | None = None


@dataclass(slots=True)
class QuickSearchReport:
    """Outcome of one quick search across all sources."""

    item_id: int
    query: str
    capped: bool = False
    sources: list[QuickSourceReport] = field(default_factory=list)


def argentina_today() -> date:
    """The calendar day the cap counts against (dad's timezone, not UTC)."""
    return datetime.now(_ARGENTINA_TZ).date()


def runs_today(session: Session) -> int:
    """Quick searches already recorded for today (Argentine calendar day)."""
    return session.scalar(
        select(func.count())
        .select_from(QuickSearchRun)
        .where(QuickSearchRun.ran_on == argentina_today())
    )


def _search_one(adapter: Adapter, query: str) -> tuple[list[ClassifiedListing], int, str | None]:
    """Fetch + classify in a worker thread; DB writes stay on the caller's thread."""
    try:
        listings = adapter.fetch(query)
    except AdapterError as exc:
        return [], adapter.skipped, " ".join(str(exc).split())
    except Exception as exc:  # unexpected: isolate like the ingest runner does
        return [], adapter.skipped, f"unexpected {type(exc).__name__}: {exc}"
    return apply_relevance(query, listings), adapter.skipped, None


def quick_search(
    session: Session,
    item: TrackedItem,
    sources: Sequence[Source],
    *,
    adapters: Sequence[Adapter] | None = None,
    progress: Callable[[str], None] | None = None,
) -> QuickSearchReport:
    """Search every search-capable source for one item, in parallel; store results.

    Sources whose platform is not in SEARCHABLE_PLATFORMS appear in the report
    with searched=False (the UI explains "solo búsqueda diaria"). When the
    daily cap is already spent the report comes back capped=True and nothing
    is fetched. ``adapters`` exists for tests (fakes); production callers let
    build_adapters construct the real ones from the searchable sources.
    Raises QuickSearchBusy when another quick search is running.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        raise QuickSearchBusy("a quick search is already running")
    try:
        report = QuickSearchReport(item_id=item.id, query=item.query)
        if runs_today(session) >= DAILY_CAP:
            report.capped = True
            return report
        # Recorded up front: a run that fails halfway still visited the stores,
        # so it still spends cap.
        session.add(QuickSearchRun(tracked_item_id=item.id, ran_on=argentina_today()))
        session.commit()

        searchable = [s for s in sources if s.platform in SEARCHABLE_PLATFORMS]
        for source in sources:
            if source.platform not in SEARCHABLE_PLATFORMS:
                report.sources.append(
                    QuickSourceReport(slug=source.slug, name=source.name, searched=False)
                )
        if adapters is None:
            adapters = build_adapters(searchable)

        with ExitStack() as stack:
            for adapter in adapters:
                stack.enter_context(adapter)
            with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
                futures = {
                    pool.submit(_search_one, adapter, item.query): adapter for adapter in adapters
                }
                for future in as_completed(futures):
                    adapter = futures[future]
                    classified, malformed, failure = future.result()
                    source_report = QuickSourceReport(
                        slug=adapter.source.slug,
                        name=adapter.source.name,
                        searched=True,
                        malformed_skipped=malformed,
                        failure=failure,
                    )
                    if failure is None:
                        source_report.fetched = len(classified)
                        source_report.inserted = save_classified_listings(
                            session, item.id, classified
                        )
                        session.commit()
                        for entry in classified:
                            if entry.result.relevance is Relevance.MATCH:
                                source_report.matches += 1
                            elif entry.result.relevance is Relevance.LOW_CONFIDENCE:
                                source_report.low_confidence += 1
                    else:
                        session.rollback()
                    report.sources.append(source_report)
                    if progress is not None:
                        progress(adapter.source.name)

        order = {source.slug: index for index, source in enumerate(sources)}
        report.sources.sort(key=lambda s: order[s.slug])
        return report
    finally:
        _RUN_LOCK.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quicksearch.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/dashboard/ tests/test_quicksearch.py
git commit -m "feat: parallel quick search over search-capable sources with daily cap"
```

### Task 3: quick-search CLI (internal team tool)

**Files:**
- Modify: `src/repuestos_radar/dashboard/quicksearch.py` (append `format_report`, `main`)
- Test: `tests/test_quicksearch.py` (append)

**Interfaces:**
- Produces: `python -m repuestos_radar.dashboard.quicksearch ITEM_ID` — grep-able key=value output in the ingest report style; exit 0 when at least one source succeeded, 1 on cap/config/DB errors.

- [ ] **Step 1: Write the failing tests** (append)

```python
from repuestos_radar.dashboard.quicksearch import (
    QuickSearchReport,
    QuickSourceReport,
    format_report,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quicksearch.py -q`
Expected: FAIL with `ImportError: cannot import name 'format_report'`

- [ ] **Step 3: Implement** (append to `quicksearch.py`; add `import argparse`, `import sys`, `import yaml`, `from sqlalchemy.exc import SQLAlchemyError`, and `from repuestos_radar.db import get_engine, get_session_factory, init_db`, `from repuestos_radar.sources import load_sources` to the imports)

```python
def format_report(report: QuickSearchReport) -> str:
    """Render as grep-able key=value lines (same conventions as the ingest report)."""
    query_text = report.query.replace('"', "'")
    lines = [f'quick search: item={report.item_id} query="{query_text}"']
    if report.capped:
        lines.append(f"daily cap reached ({DAILY_CAP}/day); nothing fetched")
        return "\n".join(lines)
    for s in report.sources:
        if not s.searched:
            lines.append(f"source={s.slug} searched=no reason=crawl-only")
            continue
        line = f"source={s.slug} searched=yes"
        if s.failure is None:
            line += (
                f" fetched={s.fetched} inserted={s.inserted}"
                f" match={s.matches} low_confidence={s.low_confidence} status=ok"
            )
        else:
            error_text = s.failure.replace('"', "'")
            line += f' status=failed error="{error_text}"'
        lines.append(line)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point — an internal team tool; the client uses the dashboard."""
    parser = argparse.ArgumentParser(
        prog="python -m repuestos_radar.dashboard.quicksearch",
        description="Search every search-capable source for one tracked item, now.",
    )
    parser.add_argument("item_id", type=int, help="tracked item id (see the tracked CLI)")
    args = parser.parse_args(argv)
    try:
        sources = load_sources()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"quick search aborted (config error): {' '.join(str(exc).split())}")
        return 1
    try:
        engine = get_engine()
        init_db(engine)
        with get_session_factory(engine)() as session:
            item = session.get(TrackedItem, args.item_id)
            if item is None:
                print(f"error: no tracked item with id {args.item_id}")
                return 1
            report = quick_search(session, item, sources)
    except QuickSearchBusy:
        print("error: a quick search is already running")
        return 1
    except SQLAlchemyError as exc:
        print(f"quick search aborted (database error): {' '.join(str(exc).split())}")
        return 1
    print(format_report(report))
    if report.capped:
        return 1
    return 0 if any(s.searched and s.failure is None for s in report.sources) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full suite and linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS

- [ ] **Step 5: Commit, push, open PR**

```bash
git add src/repuestos_radar/dashboard/quicksearch.py tests/test_quicksearch.py
git commit -m "feat: quick-search CLI entry point"
git push -u origin feat/quick-search
gh pr create --title "M4 PR1: quick search engine" --body "..."
```

PR body: what it does, the courtesy analysis (parallel across hosts, sequential per host, cap 10/day ART, tiendanube skipped because /search/ is robots-disallowed), and a live-test transcript: run `uv run python -m repuestos_radar.dashboard.quicksearch 1` ONCE against the real DB and paste the report (this spends 1 of today's 15 testing runs — note it in the PR).

---

# PR 2 — Distance

Branch: `feat/distance`

### Task 4: coordinates in the source registry

**Files:**
- Modify: `src/repuestos_radar/sources.py`
- Modify: `sources.yaml`
- Test: `tests/test_sources.py` (append)

**Interfaces:**
- Produces: `Source.lat: float | None`, `Source.lon: float | None` (both present or both absent; validated ranges). Task 5's page code builds `{slug: (lat, lon)}` from these.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_sources.py`, following its existing tmp-yaml fixture style)

```python
def test_source_coordinates_parsed(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        BASE_ENTRY + "    lat: -32.9526\n    lon: -60.6310\n", encoding="utf-8"
    )  # BASE_ENTRY: reuse/define a minimal valid single-source yaml string local to the test file
    (source,) = load_sources(registry)
    assert source.lat == pytest.approx(-32.9526)
    assert source.lon == pytest.approx(-60.6310)


def test_source_coordinates_default_none(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(BASE_ENTRY, encoding="utf-8")
    (source,) = load_sources(registry)
    assert source.lat is None and source.lon is None


@pytest.mark.parametrize(
    "extra",
    [
        "    lat: -32.9526\n",  # lat without lon
        "    lon: -60.6310\n",  # lon without lat
        "    lat: -95.0\n    lon: -60.0\n",  # lat out of range
        "    lat: -32.0\n    lon: 190.0\n",  # lon out of range
        "    lat: south\n    lon: -60.0\n",  # not a number
    ],
)
def test_bad_coordinates_rejected(tmp_path, extra):
    registry = tmp_path / "sources.yaml"
    registry.write_text(BASE_ENTRY + extra, encoding="utf-8")
    with pytest.raises(ValueError):
        load_sources(registry)


def test_real_registry_rosario_sources_have_coordinates():
    by_slug = {source.slug: source for source in load_sources()}
    for slug in ("novocell", "tienda-movil", "evophone", "celuphone", "litoral-accesorios"):
        assert by_slug[slug].lat is not None, slug
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -q`
Expected: FAIL (`lat` is not a `Source` field / real registry has no coordinates)

- [ ] **Step 3: Implement.** In `sources.py`, add to `Source`:

```python
    lat: float | None = None
    """Store latitude, hand-entered (see sources.yaml). Both lat and lon or neither."""
    lon: float | None = None
```

Add the parser (bool excluded like `_parse_max_catalog_pages`; int accepted and cast):

```python
def _parse_coordinate(index: int, name: str, value: object, limit: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"source #{index}: '{name}' must be a number when present")
    if not -limit <= value <= limit:
        raise ValueError(f"source #{index}: '{name}' must be within ±{limit}")
    return float(value)
```

In `_parse_entry`, before constructing `Source`:

```python
    lat = _parse_coordinate(index, "lat", entry.get("lat"), 90.0)
    lon = _parse_coordinate(index, "lon", entry.get("lon"), 180.0)
    if (lat is None) != (lon is None):
        raise ValueError(f"source #{index}: 'lat' and 'lon' must be given together")
```

and pass `lat=lat, lon=lon`.

In `sources.yaml`, add under each source (with this comment block above the first one):

```yaml
    # Hand-entered store position for the dashboard's straight-line distance
    # column (M4). Derived from the street address; verify against the address
    # on a map before trusting it to 3 decimals.
```

| source | lines to add |
|---|---|
| novocell | `lat: -32.9527` / `lon: -60.6293` |
| tienda-movil | `lat: -32.9437` / `lon: -60.6444` |
| evophone | `lat: -32.9516` / `lon: -60.6789` |
| celuphone | `lat: -32.9386` / `lon: -60.6801` |
| litoral-accesorios | `lat: -32.9500` / `lon: -60.6355` |
| mdrepuestos | `lat: -34.5290` / `lon: -58.5312` |
| gofix | `lat: -34.5051` / `lon: -58.5659` |
| onestore | `lat: -32.8896` / `lon: -68.8442` |

These were derived from each registry entry's street address; the reviewer must sanity-check each pair against the address on a map (they only need to be right to a few hundred meters — the column shows straight-line distance, not directions).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/sources.py sources.yaml tests/test_sources.py
git commit -m "feat: optional store coordinates in the source registry"
```

### Task 5: distance math and formatting

**Files:**
- Create: `src/repuestos_radar/dashboard/distance.py`
- Test: `tests/test_distance.py`

**Interfaces:**
- Produces: `haversine_km(lat1, lon1, lat2, lon2) -> float`; `format_distance_km(km: float) -> str` ("850 m" / "2,1 km" / "12 km" / "300 km"); `shop_location() -> tuple[float, float] | None` from env `SHOP_LAT`/`SHOP_LON` (honoring `.env` via dotenv, like `db.get_engine`).

- [ ] **Step 1: Write the failing tests**

```python
"""Straight-line distance for the dashboard (no routing, no maps API)."""

import pytest

from repuestos_radar.dashboard.distance import format_distance_km, haversine_km, shop_location


def test_haversine_zero_for_same_point():
    assert haversine_km(-32.95, -60.65, -32.95, -60.65) == pytest.approx(0.0)


def test_haversine_known_pair():
    # Rosario Monumento a la Bandera -> Buenos Aires Obelisco, ~278 km straight line.
    km = haversine_km(-32.9478, -60.6305, -34.6037, -58.3816)
    assert km == pytest.approx(278, rel=0.02)


def test_haversine_short_city_hop():
    # ~1 degree of longitude at this latitude is ~93 km; 0.01 deg ~ 0.93 km.
    km = haversine_km(-32.95, -60.65, -32.95, -60.64)
    assert km == pytest.approx(0.93, rel=0.05)


@pytest.mark.parametrize(
    ("km", "expected"),
    [
        (0.85, "850 m"),
        (0.9999, "1,0 km"),  # rounds to 1000 m -> promoted to km
        (0.049, "50 m"),
        (2.14, "2,1 km"),
        (9.96, "10 km"),  # rounds to 10.0 -> promoted to integer km
        (12.4, "12 km"),
        (278.6, "279 km"),
    ],
)
def test_format_distance(km, expected):
    assert format_distance_km(km) == expected


def test_shop_location_reads_env(monkeypatch):
    monkeypatch.setenv("SHOP_LAT", "-32.95")
    monkeypatch.setenv("SHOP_LON", "-60.65")
    assert shop_location() == (-32.95, -60.65)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(None, None), ("-32.95", None), ("abc", "-60.65"), ("-95", "-60.65")],
)
def test_shop_location_missing_or_invalid_is_none(monkeypatch, lat, lon):
    for name, value in (("SHOP_LAT", lat), ("SHOP_LON", lon)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert shop_location() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_distance.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `distance.py`**

```python
"""Straight-line distance: haversine + Argentine display formatting.

Deliberately simple (approved design): no routing, no maps API, no "best
option" scoring. The reference point defaults to the Activcelu shop, whose
coordinates live in the environment (SHOP_LAT / SHOP_LON — Streamlit secrets
in the cloud, .env locally) rather than this public repo.
"""

import math
import os

from dotenv import load_dotenv

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def format_distance_km(km: float) -> str:
    """ "850 m" under 1 km, "2,1 km" under 10, whole km from there up."""
    meters = round(km * 1000)
    if meters < 1000:
        # Round short hops to 10 m — fake precision helps nobody.
        return f"{round(meters, -1)} m"
    if km < 9.95:  # under this, one decimal still rounds below 10,0
        return f"{km:.1f}".replace(".", ",") + " km"
    return f"{round(km)} km"


def shop_location() -> tuple[float, float] | None:
    """The Activcelu shop's position from the environment, or None when unset."""
    load_dotenv()
    raw_lat, raw_lon = os.environ.get("SHOP_LAT"), os.environ.get("SHOP_LON")
    if not raw_lat or not raw_lon:
        return None
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon
```

Note for the `format_distance_km` edge cases: `0.9999 km` → 1000 m → falls through to the km branch → "1,0 km"; `9.96` → `f"{9.96:.1f}"` would be "10,0", so the `km < 9.95` guard routes it to "10 km". The tests above pin both.

- [ ] **Step 4: Run tests, full suite, linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 5: Commit, push, open PR**

```bash
git add src/repuestos_radar/dashboard/distance.py tests/test_distance.py
git commit -m "feat: haversine distance with Argentine formatting and shop reference point"
git push -u origin feat/distance
gh pr create --title "M4 PR2: distance module + store coordinates" --body "..."
```

PR body must ask the reviewer to verify the 8 coordinate pairs against the addresses.

---

# PR 3 — Dashboard core: login, home, part detail

Branch: `feat/dashboard-core`

### Task 6: dependencies and app entry point

**Files:**
- Modify: `pyproject.toml`
- Create: `requirements.txt` (repo root, for Streamlit Cloud)
- Create: `streamlit_app.py` (repo root)

**Interfaces:**
- Produces: `streamlit` importable in dev and CI; `streamlit_app.py` calls `repuestos_radar.dashboard.app.main()` (defined in Task 8).

- [ ] **Step 1: Edit `pyproject.toml`.** Add a `dashboard` optional-dependency group and extend `dev` (AppTest needs streamlit installed in CI):

```toml
[project.optional-dependencies]
dashboard = [
    "streamlit>=1.45",
    "streamlit-cookies-controller>=0.0.4",
    "streamlit-geolocation>=0.0.10",
]
dev = [
    "pytest>=9,<10",
    "ruff>=0.16,<0.17",
    "streamlit>=1.45",
    "streamlit-cookies-controller>=0.0.4",
    "streamlit-geolocation>=0.0.10",
]
```

- [ ] **Step 2: Create `requirements.txt`** (Streamlit Cloud installs from this):

```
.[dashboard]
```

- [ ] **Step 3: Create `streamlit_app.py`:**

```python
"""Streamlit Cloud entry point. All real code lives in repuestos_radar.dashboard."""

from repuestos_radar.dashboard.app import main

main()
```

- [ ] **Step 4: Sync the environment and check imports**

Run: `uv sync --extra dev && uv run python -c "import streamlit; print(streamlit.__version__)"`
Expected: a version >= 1.45 prints. (`streamlit_app.py` will fail to import until Task 8 defines `app.main` — that's expected; do NOT run it yet.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt streamlit_app.py uv.lock
git commit -m "build: streamlit dashboard dependencies and cloud entry point"
```

### Task 7: auth tokens (pure logic)

**Files:**
- Create: `src/repuestos_radar/dashboard/auth.py`
- Test: `tests/test_dashboard_auth.py`

**Interfaces:**
- Produces: `TOKEN_TTL_SECONDS = 30*24*3600`; `check_password(entered, expected) -> bool`; `make_token(password, now=None) -> str`; `token_valid(password, token, now=None) -> bool`. Task 8 stores `make_token` output in a cookie and validates it with `token_valid`.

- [ ] **Step 1: Write the failing tests**

```python
"""Login-token logic: pure, time-injectable, no Streamlit imports."""

from repuestos_radar.dashboard.auth import (
    TOKEN_TTL_SECONDS,
    check_password,
    make_token,
    token_valid,
)


def test_check_password_exact_match_only():
    assert check_password("clave", "clave")
    assert not check_password("clave ", "clave")
    assert not check_password("", "clave")


def test_token_roundtrip():
    token = make_token("clave", now=1_000_000.0)
    assert token_valid("clave", token, now=1_000_000.0)
    assert token_valid("clave", token, now=1_000_000.0 + TOKEN_TTL_SECONDS - 1)


def test_token_expires():
    token = make_token("clave", now=1_000_000.0)
    assert not token_valid("clave", token, now=1_000_000.0 + TOKEN_TTL_SECONDS + 1)


def test_token_bound_to_password():
    token = make_token("clave", now=1_000_000.0)
    assert not token_valid("otra", token, now=1_000_000.0)


def test_garbage_tokens_rejected():
    for garbage in ("", "no-dot", "123", "abc.def", "999999999999999999999999.x"):
        assert not token_valid("clave", garbage, now=1_000_000.0)


def test_tampered_expiry_rejected():
    token = make_token("clave", now=1_000_000.0)
    expires, signature = token.split(".", 1)
    tampered = f"{int(expires) + 999999}.{signature}"
    assert not token_valid("clave", tampered, now=1_000_000.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_auth.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `auth.py`**

```python
"""Shared-password auth with a signed remember-me token.

One password for the whole app (margins are business-sensitive). The cookie
holds "<expiry-unix>.<hmac>" — signed with the password itself, so changing
the password invalidates every outstanding token. Pure functions: the
Streamlit cookie glue lives in app.py.
"""

import hashlib
import hmac
import time

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # ~30 days; the spec's "rarely re-asks"


def check_password(entered: str, expected: str) -> bool:
    """Constant-time comparison; never `==` on secrets."""
    return hmac.compare_digest(entered.encode(), expected.encode())


def _sign(password: str, message: str) -> str:
    return hmac.new(password.encode(), message.encode(), hashlib.sha256).hexdigest()


def make_token(password: str, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + TOKEN_TTL_SECONDS)
    return f"{expires}.{_sign(password, str(expires))}"


def token_valid(password: str, token: str, now: float | None = None) -> bool:
    expires_text, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if (time.time() if now is None else now) > expires:
        return False
    return hmac.compare_digest(_sign(password, expires_text), signature)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/dashboard/auth.py tests/test_dashboard_auth.py
git commit -m "feat: signed remember-me login tokens"
```

### Task 8: Spanish strings, data helpers, app shell with login gate

**Files:**
- Create: `src/repuestos_radar/dashboard/text_es.py`
- Create: `src/repuestos_radar/dashboard/data.py`
- Create: `src/repuestos_radar/dashboard/app.py`
- Create: `src/repuestos_radar/dashboard/home.py` (stub this task, full render next task)
- Create: `src/repuestos_radar/dashboard/detail.py` (stub)
- Create: `src/repuestos_radar/dashboard/admin.py` (stub)
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `auth` (Task 7), `get_engine`/`init_db`/`get_session_factory` from `db.py`.
- Produces:
  - `text_es.py`: module-level `str` constants; every page imports from here.
  - `data.py`: `cached_engine() -> Engine` (st.cache_resource, calls `init_db` once); `open_session() -> Session`; `overall_latest_day(session) -> date | None`; `fair_price_series(session, item_id, tier, end_day, days=30) -> list[tuple[date, Decimal]]`.
  - `app.py`: `main() -> None` (login gate + `st.navigation` + freshness footer); `PAGES` dict `{"home": st.Page, "detail": st.Page, "admin": st.Page}` so `home.py` can `st.switch_page(PAGES["detail"])`.

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard_app.py` — AppTest drives the real entry script against a temp SQLite file. Custom components (cookies, geolocation) render as no-ops under AppTest; the code must treat their `None` returns as "no cookie / no location".

```python
"""App-shell tests: login gate and page navigation via streamlit AppTest."""

from datetime import date
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/dash.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_PASSWORD", "clave-test")
    engine = get_engine(url)
    init_db(engine)
    with get_session_factory(engine)() as session:
        item = TrackedItem(query="modulo a32")
        session.add(item)
        session.flush()
        session.add(
            Listing(
                tracked_item_id=item.id,
                source_slug="celuphone",
                external_id="1",
                title="Modulo Samsung A32 incell",
                price=Decimal("20700"),
                currency="ARS",
                condition="unknown",
                url="https://celuphone.com.ar/p/1",
                fetched_date=date(2026, 9, 1),
                relevance="match",
                relevance_score=0.9,
            )
        )
        session.add(
            ServicePrice(
                tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("85000")
            )
        )
        session.commit()
    return url


def _app(seeded_db) -> AppTest:
    at = AppTest.from_file("streamlit_app.py", default_timeout=10)
    return at


def test_login_gate_blocks_without_password(seeded_db):
    at = _app(seeded_db).run()
    assert at.text_input  # the password field is shown
    assert "authed" not in at.session_state or not at.session_state["authed"]


def test_wrong_password_rejected(seeded_db):
    at = _app(seeded_db).run()
    at.text_input[0].set_value("wrong").run()
    at.button[0].set_value(True).run()
    assert "authed" not in at.session_state or not at.session_state["authed"]


def test_right_password_enters_and_home_lists_items(seeded_db):
    at = _app(seeded_db).run()
    at.text_input[0].set_value("clave-test").run()
    at.button[0].set_value(True).run()
    assert at.session_state["authed"] is True
    body = " ".join(str(fragment) for fragment in at.markdown)
    assert "modulo a32" in body.lower()
    assert "$20.700" in body


def test_missing_app_password_is_a_visible_config_error(seeded_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD")
    at = _app(seeded_db).run()
    assert at.error
```

(If `at.button[0]` turns out to be a form submit under a different accessor in the installed Streamlit version, use `at.get("form_submit_button")[0]` — pin whichever works, the behavior asserted stays the same.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_app.py -q`
Expected: FAIL (no `app.main` yet)

- [ ] **Step 3: Implement.**

`text_es.py` (all on-screen words; Mo reviews this file):

```python
"""Every user-visible string of the dashboard, in Rioplatense Spanish.

One module so (a) Mo reviews Spanish in one place, (b) an English mode later
is a translation of this file, not a hunt through page code.
"""

APP_TITLE = "Repuestos Radar"

# Login
PASSWORD_LABEL = "Contraseña"
LOGIN_BUTTON = "Entrar"
WRONG_PASSWORD = "Contraseña incorrecta."
NO_PASSWORD_CONFIGURED = (
    "Falta configurar la contraseña de la app (APP_PASSWORD). Avisale al equipo."
)

# Navigation
NAV_PRICES = "Precios"
NAV_DETAIL = "Detalle"
NAV_SETTINGS = "Ajustes"

# Freshness footer
UPDATED_PREFIX = "Actualizado:"
NO_DATA_AT_ALL = "Todavía no hay datos guardados."

# Home
BEST_PREFIX = "Mejor:"
MARGIN_GAIN = "Ganás {amount}"
MARGIN_LOSS = "Perdés {amount}"
NEEDS_REVIEW_DOT = "⚠ hay precios para revisar"
NO_DATA_TODAY = "sin datos de hoy"
SEE_DETAIL = "Ver detalle"

# Detail
PICK_ITEM = "Elegí un repuesto"
SORT_LABEL = "Ordenar por"
SORT_PRICE = "Precio"
SORT_DISTANCE = "Distancia"
FAIR_PRICE_PREFIX = "Precio justo:"
FAIR_PRICE_RANGE = "entre {low} y {high} ({count} locales)"
SINGLE_STORE_NOTE = "un solo local con este repuesto — no hay precio de mercado"
OUTLIER_WARNING = "precio muy bajo o muy alto para el grupo — revisar: puede ser error, calidad mal etiquetada o una oferta real"
LOW_CONFIDENCE_WARNING = "revisar: puede ser otro modelo"
MARGIN_HEADER = "Márgenes por reparación"
MARGIN_LINE = "{label} ({service}): {verb} {amount} con el repuesto de {store} ({tier})"
MARGIN_VERB_GAIN = "ganás"
MARGIN_VERB_LOSS = "perdés"
TREND_VS = "vs hace {days} días"
TREND_CHART_LABEL = "Historial de precio justo (30 días)"
NO_TREND = "sin historial para comparar"

# Distance (wired in PR 4)
FROM_SHOP = "Desde: Local Activcelu"
FROM_MY_LOCATION = "Desde: tu ubicación"
USE_MY_LOCATION = "Usar mi ubicación"
BACK_TO_SHOP = "Volver al local"
LOCATION_DENIED = (
    "No pudimos leer tu ubicación (permiso denegado). Seguimos midiendo desde el local."
)
NO_SHOP_LOCATION = "Falta configurar la ubicación del local (SHOP_LAT/SHOP_LON)."
NO_STORE_LOCATION = "—"
SHIPS_ONLY_NOTE = "solo envío"
```

(Plus the admin/quick-search strings — added in PR 4's tasks; keep this file append-only.)

`data.py`:

```python
"""Session plumbing and small query helpers for the dashboard pages."""

from datetime import date, timedelta
from decimal import Decimal

import streamlit as st
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from repuestos_radar.analysis import analyze_item, listings_for_day
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing


@st.cache_resource
def cached_engine() -> Engine:
    engine = get_engine()
    init_db(engine)
    return engine


def open_session() -> Session:
    return get_session_factory(cached_engine())()


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
```

`app.py`:

```python
"""App shell: page config, login gate, navigation, freshness footer."""

import os

import streamlit as st

from repuestos_radar.dashboard import admin, auth, data, detail, home, text_es
from repuestos_radar.report import _format_day  # dd/mm/yyyy, one formatter for the project

_COOKIE_NAME = "repuestos_radar_session"


def _expected_password() -> str | None:
    # Streamlit secrets first (cloud), environment second (local/.env, tests).
    try:
        if "APP_PASSWORD" in st.secrets:
            return st.secrets["APP_PASSWORD"]
    except Exception:  # no secrets.toml configured — normal outside the cloud
        pass
    return os.environ.get("APP_PASSWORD")


def _cookie_controller():
    """The cookie component, or None when unavailable (AppTest, import failure)."""
    try:
        from streamlit_cookies_controller import CookieController

        return CookieController()
    except Exception:
        return None


def _require_login() -> None:
    password = _expected_password()
    if not password:
        st.error(text_es.NO_PASSWORD_CONFIGURED)
        st.stop()
    if st.session_state.get("authed"):
        return
    controller = _cookie_controller()
    token = controller.get(_COOKIE_NAME) if controller else None
    if isinstance(token, str) and auth.token_valid(password, token):
        st.session_state["authed"] = True
        return
    st.title(text_es.APP_TITLE)
    with st.form("login"):
        entered = st.text_input(text_es.PASSWORD_LABEL, type="password")
        submitted = st.form_submit_button(text_es.LOGIN_BUTTON, use_container_width=True)
    if submitted:
        if auth.check_password(entered, password):
            st.session_state["authed"] = True
            if controller:
                controller.set(
                    _COOKIE_NAME, auth.make_token(password), max_age=auth.TOKEN_TTL_SECONDS
                )
            st.rerun()
        else:
            st.error(text_es.WRONG_PASSWORD)
    st.stop()


PAGES: dict[str, st.Page] = {}


def _build_pages() -> list[st.Page]:
    PAGES.clear()
    PAGES["home"] = st.Page(home.render, title=text_es.NAV_PRICES, icon="📱", default=True)
    PAGES["detail"] = st.Page(detail.render, title=text_es.NAV_DETAIL, icon="🔎")
    PAGES["admin"] = st.Page(admin.render, title=text_es.NAV_SETTINGS, icon="🛠")
    return list(PAGES.values())


def _freshness_footer() -> None:
    with data.open_session() as session:
        day = data.overall_latest_day(session)
    if day is None:
        st.caption(text_es.NO_DATA_AT_ALL)
    else:
        st.caption(f"{text_es.UPDATED_PREFIX} {_format_day(day)}")


def main() -> None:
    st.set_page_config(page_title=text_es.APP_TITLE, page_icon="📱", layout="centered")
    _require_login()
    st.navigation(_build_pages()).run()
    _freshness_footer()
```

(`report._format_day` is private-by-underscore; promote it: rename to `format_day` in `report.py`, keep a `_format_day = format_day` alias line so existing report code and tests stay untouched, and import `format_day` here. Do that rename as part of this step.)

Stubs so navigation works this task — `home.py`:

```python
"""Precios: one card per tracked part. Full render in the next task."""

import streamlit as st

from repuestos_radar.dashboard import text_es


def render() -> None:
    st.title(text_es.NAV_PRICES)
```

`detail.py` and `admin.py`: same shape (`st.title(text_es.NAV_DETAIL)` / `st.title(text_es.NAV_SETTINGS)`, module docstrings saying which PR fills them).

- [ ] **Step 4: Run the app-shell tests** — the two login-gate tests and the config-error test must pass; `test_right_password_enters_and_home_lists_items` still fails on the `$20.700` assertion (home is a stub). That's the next task's failing test — leave it red.

Run: `uv run pytest tests/test_dashboard_app.py -q`
Expected: 3 pass, 1 fail (`test_right_password_enters_and_home_lists_items`)

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/dashboard/ src/repuestos_radar/report.py tests/test_dashboard_app.py
git commit -m "feat: dashboard shell — Spanish strings, login gate, navigation, freshness footer"
```

### Task 9: home page (cards)

**Files:**
- Modify: `src/repuestos_radar/dashboard/home.py`
- Test: `tests/test_dashboard_app.py` (Task 8's red test goes green; append card-content tests)

**Interfaces:**
- Consumes: `analyze_item`, `latest_day`, `listings_for_day` (analysis), `margins_for` (margin), `TIER_LABELS_ES`, `format_ars` (report), `PAGES` (app), `text_es`, `data`.
- Produces: `render() -> None`; sets `st.session_state["selected_item_id"]` and switches to the detail page on card tap. Card rules (pin in tests): best offer = cheapest non-outlier offer across every tier; margin shown = the item's best (largest) `TierMargin.margin` across its service prices, green via `:green[...]` / red via `:red[...]`; warning line when any offer is an outlier or low-confidence.

- [ ] **Step 1: Append the failing tests**

```python
def _login(at):
    at.text_input[0].set_value("clave-test").run()
    at.button[0].set_value(True).run()
    return at


def test_home_card_shows_margin_and_no_warning_for_clean_data(seeded_db):
    at = _login(_app(seeded_db).run())
    body = " ".join(str(fragment) for fragment in at.markdown)
    assert "$64.300" in body  # 85000 - 20700
    assert "revisar" not in body


def test_home_card_says_no_data_today_for_empty_item(seeded_db, monkeypatch):
    engine = get_engine(seeded_db)
    with get_session_factory(engine)() as session:
        session.add(TrackedItem(query="bateria iphone 11"))
        session.commit()
    at = _login(_app(seeded_db).run())
    body = " ".join(str(fragment) for fragment in at.markdown)
    assert "sin datos de hoy" in body
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_dashboard_app.py -q`
Expected: the two new tests and Task 8's leftover FAIL

- [ ] **Step 3: Implement `home.py`**

```python
"""Precios: one card per tracked part — best price, margin, warnings at a glance."""

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from repuestos_radar.analysis import TierAnalysis, analyze_item, latest_day, listings_for_day
from repuestos_radar.dashboard import data, text_es
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
                    st.markdown(
                        f"{text_es.BEST_PREFIX} **{format_ars(best.price)}** — "
                        f"{best.source_slug} ({tier_label})"
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
```

(Store display names, not slugs, would be nicer — that arrives with the detail page's source-name map in Task 10; home upgrades to names there too. Keep the slug here for now so this task stays green without touching `sources.yaml` loading.)

- [ ] **Step 4: Run the whole dashboard test file**

Run: `uv run pytest tests/test_dashboard_app.py -q`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/dashboard/home.py tests/test_dashboard_app.py
git commit -m "feat: home page — part cards with best price, margin, and review flags"
```

### Task 10: part detail page

**Files:**
- Modify: `src/repuestos_radar/dashboard/detail.py`
- Modify: `src/repuestos_radar/dashboard/home.py` (swap slug → store display name)
- Test: `tests/test_dashboard_detail.py`

**Interfaces:**
- Consumes: everything home consumes plus `tier_trends`, `data.fair_price_series`, `load_sources` (for `{slug: name}`), `TrendPoint`.
- Produces: `render() -> None` reading `st.session_state["selected_item_id"]` (falls back to a selectbox when unset); `source_names() -> dict[str, str]` (module function, cached with `@st.cache_data`, also imported by `home.py`). Section order and copy pinned by tests: tier blocks (offers with warnings, fair price with range) → margins → trends + collapsed chart.

- [ ] **Step 1: Write the failing tests** — same fixture pattern as `test_dashboard_app.py` (copy the `seeded_db`/`_app`/`_login` helpers locally; small duplication beats cross-test-file imports, per project convention). Seed a second store so fair price exists: add a `Listing` from `evophone` external_id "2", title "Modulo Samsung A32 incell", price 23500, relevance "match", plus one low-confidence row from `novocell` external_id "3", title "Modulo A32 quizas", price 19000, relevance "low_confidence".

```python
def test_detail_shows_tier_block_offers_and_fair_price(seeded_detail_db):
    at = _login(_app(seeded_detail_db).run())
    at.session_state["selected_item_id"] = 1
    at.switch_page(...)  # AppTest: run the detail page directly instead — see note below
```

**AppTest note:** `st.switch_page` doesn't navigate inside AppTest reliably. Test the detail page by running the entry file with `at.session_state["selected_item_id"] = 1` set BEFORE `.run()`, then selecting the detail page via `at.navigation` if the installed version exposes it — and if it does not, restructure the test to call the underlying pure pieces: assert on `detail._offer_line(...)` and `detail._fair_price_line(...)` outputs (make those two helpers module-level pure functions returning strings exactly so they are testable without navigation). Pin AT MINIMUM these behaviors as pure-function tests:

```python
from repuestos_radar.dashboard import detail


def test_offer_line_plain_match():
    line = detail._offer_line(
        offer=StoreOffer(
            source_slug="celuphone",
            title="Modulo A32 incell",
            price=Decimal("20700"),
            url="https://celuphone.com.ar/p/1",
            relevance="match",
            tier="incell",
        ),
        names={"celuphone": "Celuphone"},
        distance_text=None,
    )
    assert "[Celuphone](https://celuphone.com.ar/p/1)" in line
    assert "$20.700" in line
    assert "revisar" not in line


def test_offer_line_low_confidence_and_outlier_warn():
    offer = StoreOffer(
        source_slug="novocell",
        title="x",
        price=Decimal("9000"),
        url="https://n",
        relevance="low_confidence",
        tier="incell",
        outlier=True,
    )
    line = detail._offer_line(offer, names={}, distance_text=None)
    assert "revisar" in line and "novocell" in line


def test_fair_price_line_small_sample_shows_range():
    analysis = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=Decimal("22100"),
        price_min=Decimal("20700"),
        price_max=Decimal("23500"),
        store_count=2,
        basis=BASIS_MEDIAN,
    )
    line = detail._fair_price_line(analysis)
    assert "$22.100" in line and "entre $20.700 y $23.500" in line


def test_fair_price_line_single_store_is_honest():
    analysis = TierAnalysis(
        tier="incell",
        offers=(),
        fair_price=None,
        price_min=Decimal("20700"),
        price_max=Decimal("20700"),
        store_count=1,
        basis=BASIS_SINGLE_STORE,
    )
    assert "un solo local" in detail._fair_price_line(analysis)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_detail.py -q`
Expected: FAIL

- [ ] **Step 3: Implement `detail.py`**

```python
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
                            "día": [d for d, _ in series],
                            "precio justo": [float(p) for _, p in series],
                        }
                    ).set_index("día")
                    st.line_chart(frame)
                else:
                    st.markdown(f"*{text_es.NO_TREND}*")
```

In `home.py`, replace the slug in the best-price line with `detail.source_names().get(best.source_slug, best.source_slug)` (import `from repuestos_radar.dashboard.detail import source_names`).

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 5: Manual phone check + commit + PR**

Run locally: `DATABASE_URL=<real or copy> APP_PASSWORD=test uv run streamlit run streamlit_app.py`, open `http://localhost:8501` in a phone-sized browser window (devtools mobile view), confirm the three pages render single-column and the cards are tappable. Note findings in the PR body.

```bash
git add src/repuestos_radar/dashboard/ tests/test_dashboard_detail.py tests/test_dashboard_app.py
git commit -m "feat: part detail page — tiers, fair price, margins, trends"
git push -u origin feat/dashboard-core
gh pr create --title "M4 PR3: dashboard core (login, home, detail)" --body "..."
```

Mo reviews `text_es.py` in this PR.

---

# PR 4 — Admin page, distance on screen, quick-search button, README

Branch: `feat/dashboard-admin`

### Task 11: `parse_price` extraction (shared validation)

**Files:**
- Modify: `src/repuestos_radar/services.py`
- Test: `tests/test_margin.py` or wherever `_parse_price` is currently tested — extend there (find with `grep -rn "_parse_price" tests/`)

**Interfaces:**
- Produces: `services.parse_price(raw: str) -> tuple[Decimal | None, str | None]` — `(price, None)` on success (quantized to centavos, ROUND_HALF_UP), `(None, "<english reason>")` on failure (reasons: `not a number`, `not positive`). `_parse_price` becomes a thin wrapper that prints its existing messages and returns the Decimal or None — CLI behavior unchanged. The admin page maps reasons to Spanish.

- [ ] **Step 1: Write the failing tests** (append to the file that tests services)

```python
from repuestos_radar.services import parse_price


@pytest.mark.parametrize(
    ("raw", "price", "reason"),
    [
        ("85000", Decimal("85000.00"), None),
        ("85000.555", Decimal("85000.56"), None),
        ("nan", None, "not a number"),
        ("inf", None, "not a number"),
        ("abc", None, "not a number"),
        ("-5", None, "not positive"),
        ("0", None, "not positive"),
    ],
)
def test_parse_price(raw, price, reason):
    assert parse_price(raw) == (price, reason)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/ -q -k parse_price`; Expected: FAIL (ImportError)

- [ ] **Step 3: Implement.** In `services.py`:

```python
def parse_price(raw: str) -> tuple[Decimal | None, str | None]:
    """A positive Decimal in whole centavos, or a machine-checkable reason.

    Decimal happily parses "nan" and "inf", so finiteness is checked before
    the sign (comparing NaN raises InvalidOperation). Quantizing here makes
    the value match what Numeric(12, 2) will store. The dashboard admin page
    shares this exact validation with the CLI.
    """
    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None, "not a number"
    if not price.is_finite():
        return None, "not a number"
    if price <= 0:
        return None, "not positive"
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None
```

Rewrite `_parse_price` to call it (same printed messages as today):

```python
def _parse_price(raw: str) -> Decimal | None:
    price, reason = parse_price(raw)
    if reason == "not a number":
        print(f'error: price must be a number, got "{raw}"')
    elif reason == "not positive":
        print("error: price must be positive")
    return price
```

- [ ] **Step 4: Run the suite** — `uv run pytest -q`; Expected: PASS (existing CLI tests prove behavior unchanged)

- [ ] **Step 5: Commit** — `git commit -am "refactor: extract shared parse_price validation"`

### Task 12: admin page (repair prices + tracked parts)

**Files:**
- Modify: `src/repuestos_radar/dashboard/admin.py`
- Modify: `src/repuestos_radar/dashboard/text_es.py` (append admin strings)
- Test: `tests/test_dashboard_admin.py`

**Interfaces:**
- Consumes: `services.parse_price/add_service/set_price/remove_service/list_services`, `tracked.add_item/set_active/list_items` — plus their status constants; `quicksearch` (next task wires the button; this task renders the page without it).
- Produces: `render() -> None`. Two-step confirm pattern for destructive actions via `st.session_state[f"confirm-{kind}-{id}"]` flags.

- [ ] **Step 1: Append to `text_es.py`:**

```python
# Admin — repair prices
SERVICES_HEADER = "Precios de reparaciones"
SERVICE_EDIT = "Editar"
SERVICE_SAVE = "Guardar"
SERVICE_REMOVE = "Borrar"
SERVICE_CONFIRM = "¿Seguro?"
SERVICE_CONFIRM_YES = "Sí, borrar"
SERVICE_CONFIRM_NO = "No"
SERVICE_ADD_HEADER = "Agregar reparación"
SERVICE_LABEL_FIELD = "Nombre de la reparación"
SERVICE_ITEM_FIELD = "Repuesto que usa"
SERVICE_PRICE_FIELD = "Precio al cliente (ARS)"
SERVICE_ADD_BUTTON = "Agregar"
SERVICE_SAVED = "Guardado."
SERVICE_REMOVED = "Borrado."
PRICE_NOT_A_NUMBER = "El precio tiene que ser un número."
PRICE_NOT_POSITIVE = "El precio tiene que ser mayor que cero."
LABEL_EMPTY = "El nombre no puede estar vacío."

# Admin — tracked parts
TRACKED_HEADER = "Repuestos vigilados"
TRACKED_ADD_HEADER = "Agregar repuesto"
TRACKED_QUERY_FIELD = "Palabras de búsqueda"
TRACKED_QUERY_HINT = (
    "Las palabras con las que se busca en las tiendas, como en el buscador de "
    'una página. Ejemplo: "modulo samsung a32".'
)
TRACKED_ADD_BUTTON = "Agregar"
TRACKED_ADDED = "Se agregó. Los precios aparecen después de una búsqueda."
TRACKED_ALREADY = "Ese repuesto ya está en la lista."
TRACKED_STOP = "Dejar de vigilar"
TRACKED_STOP_WARNING = "El historial de precios se guarda, pero el radar deja de buscarlo cada día."
TRACKED_STOPPED = "Listo — ya no se vigila."
```

- [ ] **Step 2: Write the failing tests** (`tests/test_dashboard_admin.py`) — pure-logic level, same rationale as detail: test the small helpers, not Streamlit widgets.

```python
from repuestos_radar.dashboard import admin


def test_price_error_text_maps_reasons():
    assert admin._price_error("not a number") == text_es.PRICE_NOT_A_NUMBER
    assert admin._price_error("not positive") == text_es.PRICE_NOT_POSITIVE


def test_admin_add_service_roundtrip(session):  # session fixture: same style as test_quicksearch
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
```

- [ ] **Step 3: Run to verify they fail**, then implement `admin.py`:

```python
"""Ajustes: repair price list and tracked-parts management, phone-easy.

Same write helpers as the team CLIs (services.py / tracked.py) — the admin
page is another caller, not another implementation.
"""

import streamlit as st
from sqlalchemy.orm import Session

from repuestos_radar import services, tracked
from repuestos_radar.dashboard import data, text_es
from repuestos_radar.report import format_ars


def _price_error(reason: str | None) -> str | None:
    if reason == "not a number":
        return text_es.PRICE_NOT_A_NUMBER
    if reason == "not positive":
        return text_es.PRICE_NOT_POSITIVE
    return None


def _add_service(session: Session, label: str, item_id: int, raw_price: str) -> str | None:
    """Validate and upsert; returns a Spanish error, or None on success."""
    label = label.strip()
    if not label:
        return text_es.LABEL_EMPTY
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason)
    services.add_service(session, label, item_id, price)
    session.commit()
    return None


def _set_service_price(session: Session, service_id: int, raw_price: str) -> str | None:
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason)
    services.set_price(session, service_id, price)
    session.commit()
    return None


def _render_services(session: Session) -> None:
    st.subheader(text_es.SERVICES_HEADER)
    items = {item.id: item.query for item in tracked.list_items(session)}
    for service in services.list_services(session):
        with st.container(border=True):
            st.markdown(f"**{service.label}** — {format_ars(service.price_ars)}")
            with st.expander(text_es.SERVICE_EDIT):
                raw = st.text_input(
                    text_es.SERVICE_PRICE_FIELD,
                    value=str(service.price_ars),
                    key=f"price-{service.id}",
                )
                if st.button(text_es.SERVICE_SAVE, key=f"save-{service.id}"):
                    error = _set_service_price(session, service.id, raw)
                    if error:
                        st.error(error)
                    else:
                        st.success(text_es.SERVICE_SAVED)
                        st.rerun()
                confirm_key = f"confirm-service-{service.id}"
                if st.session_state.get(confirm_key):
                    st.warning(text_es.SERVICE_CONFIRM)
                    yes, no = st.columns(2)
                    if yes.button(text_es.SERVICE_CONFIRM_YES, key=f"yes-{service.id}"):
                        services.remove_service(session, service.id)
                        session.commit()
                        st.session_state.pop(confirm_key)
                        st.success(text_es.SERVICE_REMOVED)
                        st.rerun()
                    if no.button(text_es.SERVICE_CONFIRM_NO, key=f"no-{service.id}"):
                        st.session_state.pop(confirm_key)
                        st.rerun()
                elif st.button(text_es.SERVICE_REMOVE, key=f"rm-{service.id}"):
                    st.session_state[confirm_key] = True
                    st.rerun()

    with st.form("add-service", clear_on_submit=True):
        st.markdown(f"**{text_es.SERVICE_ADD_HEADER}**")
        label = st.text_input(text_es.SERVICE_LABEL_FIELD)
        item_id = (
            st.selectbox(text_es.SERVICE_ITEM_FIELD, list(items), format_func=items.get)
            if items
            else None
        )
        raw_price = st.text_input(text_es.SERVICE_PRICE_FIELD)
        if st.form_submit_button(text_es.SERVICE_ADD_BUTTON) and item_id is not None:
            error = _add_service(session, label, item_id, raw_price)
            if error:
                st.error(error)
            else:
                st.success(text_es.SERVICE_SAVED)
                st.rerun()


def _render_tracked(session: Session) -> None:
    st.subheader(text_es.TRACKED_HEADER)
    for item in tracked.list_items(session):
        if not item.active:
            continue
        with st.container(border=True):
            st.markdown(f"**{item.query}**")
            confirm_key = f"confirm-tracked-{item.id}"
            if st.session_state.get(confirm_key):
                st.warning(text_es.TRACKED_STOP_WARNING)
                yes, no = st.columns(2)
                if yes.button(text_es.SERVICE_CONFIRM_YES, key=f"tyes-{item.id}"):
                    tracked.set_active(session, item.id, False)
                    session.commit()
                    st.session_state.pop(confirm_key)
                    st.success(text_es.TRACKED_STOPPED)
                    st.rerun()
                if no.button(text_es.SERVICE_CONFIRM_NO, key=f"tno-{item.id}"):
                    st.session_state.pop(confirm_key)
                    st.rerun()
            elif st.button(text_es.TRACKED_STOP, key=f"tstop-{item.id}"):
                st.session_state[confirm_key] = True
                st.rerun()

    with st.form("add-tracked", clear_on_submit=True):
        st.markdown(f"**{text_es.TRACKED_ADD_HEADER}**")
        query = st.text_input(text_es.TRACKED_QUERY_FIELD, help=text_es.TRACKED_QUERY_HINT)
        if st.form_submit_button(text_es.TRACKED_ADD_BUTTON):
            query = query.strip()
            if query:
                item, status = tracked.add_item(session, query)
                session.commit()
                if status == tracked.ALREADY_ACTIVE:
                    st.info(text_es.TRACKED_ALREADY)
                else:
                    st.success(text_es.TRACKED_ADDED)
                    st.session_state["quick-search-item"] = item.id
                st.rerun()


def render() -> None:
    st.title(text_es.NAV_SETTINGS)
    with data.open_session() as session:
        _render_services(session)
        st.divider()
        _render_tracked(session)
```

- [ ] **Step 4: Run everything** — `uv run pytest -q && uv run ruff check .`; Expected: PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: admin page — repair prices and tracked parts"`

### Task 13: quick-search button in Ajustes

**Files:**
- Modify: `src/repuestos_radar/dashboard/admin.py`
- Modify: `src/repuestos_radar/dashboard/text_es.py` (append)
- Test: `tests/test_dashboard_admin.py` (append)

**Interfaces:**
- Consumes: `quicksearch.quick_search/runs_today/DAILY_CAP/QuickSearchBusy`, `load_sources`.
- Produces: a `_render_quick_search(session)` block at the top of Ajustes; the offer after adding a part uses `st.session_state["quick-search-item"]` (set in Task 12) to preselect the new item.

- [ ] **Step 1: Append strings:**

```python
# Admin — quick search
QUICK_SEARCH_HEADER = "Buscar precios ahora"
QUICK_SEARCH_ITEM_FIELD = "¿Qué repuesto buscar?"
QUICK_SEARCH_BUTTON = "Buscar precios ahora"
QUICK_SEARCH_RUNNING = "Buscando… tarda alrededor de un minuto"
QUICK_SEARCH_PROGRESS = "Consultando {name}…"
QUICK_SEARCH_DONE = "Listo — precios actualizados."
QUICK_SEARCH_CAP = "Se usaron las {cap} búsquedas de hoy. Mañana hay más."
QUICK_SEARCH_USED = "Búsquedas de hoy: {used} de {cap}"
QUICK_SEARCH_BUSY = "Ya hay una búsqueda en curso — esperá a que termine."
QUICK_SEARCH_SKIPPED_NOTE = "{names}: solo búsqueda diaria (no tienen buscador propio)."
QUICK_SEARCH_SOURCE_FAILED = "No pudimos consultar {name} esta vez."
```

- [ ] **Step 2: Failing test** (append; pure helper):

```python
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
```

- [ ] **Step 3: Implement.** In `admin.py`:

```python
def _skipped_note(report: quicksearch.QuickSearchReport) -> str | None:
    names = [s.name for s in report.sources if not s.searched]
    if not names:
        return None
    return text_es.QUICK_SEARCH_SKIPPED_NOTE.format(names=", ".join(names))


def _render_quick_search(session: Session) -> None:
    st.subheader(text_es.QUICK_SEARCH_HEADER)
    used = quicksearch.runs_today(session)
    st.caption(text_es.QUICK_SEARCH_USED.format(used=used, cap=quicksearch.DAILY_CAP))
    items = {item.id: item.query for item in tracked.list_items(session) if item.active}
    if not items:
        return
    preselect = st.session_state.get("quick-search-item")
    ids = list(items)
    index = ids.index(preselect) if preselect in items else 0
    item_id = st.selectbox(text_es.QUICK_SEARCH_ITEM_FIELD, ids, index=index, format_func=items.get)
    capped = used >= quicksearch.DAILY_CAP
    if capped:
        st.info(text_es.QUICK_SEARCH_CAP.format(cap=quicksearch.DAILY_CAP))
    if st.button(text_es.QUICK_SEARCH_BUTTON, disabled=capped, use_container_width=True):
        item = session.get(TrackedItem, item_id)
        with st.status(text_es.QUICK_SEARCH_RUNNING, expanded=True) as status:
            try:
                report = quicksearch.quick_search(
                    session,
                    item,
                    load_sources(),
                    progress=lambda name: status.write(
                        text_es.QUICK_SEARCH_PROGRESS.format(name=name)
                    ),
                )
            except quicksearch.QuickSearchBusy:
                status.update(label=text_es.QUICK_SEARCH_BUSY, state="error")
                return
        if report.capped:
            st.info(text_es.QUICK_SEARCH_CAP.format(cap=quicksearch.DAILY_CAP))
            return
        for source_report in report.sources:
            if source_report.searched and source_report.failure is not None:
                st.warning(text_es.QUICK_SEARCH_SOURCE_FAILED.format(name=source_report.name))
        note = _skipped_note(report)
        if note:
            st.caption(note)
        st.success(text_es.QUICK_SEARCH_DONE)
```

Add the imports (`from repuestos_radar.dashboard import quicksearch`, `from repuestos_radar.models import TrackedItem`, `from repuestos_radar.sources import load_sources`) and call `_render_quick_search(session)` first inside `render()`'s session block, followed by `st.divider()`.

- [ ] **Step 4: Run everything** — `uv run pytest -q && uv run ruff check .`; Expected: PASS

- [ ] **Step 5: Commit** — `git commit -am "feat: quick-search button with progress and daily-cap display"`

### Task 14: distance on the detail page

**Files:**
- Modify: `src/repuestos_radar/dashboard/detail.py`
- Test: `tests/test_dashboard_detail.py` (append)

**Interfaces:**
- Consumes: `distance.haversine_km/format_distance_km/shop_location`, `load_sources` (lat/lon now on `Source`), `streamlit_geolocation`.
- Produces: `_distance_for(offer_slug, reference, coords) -> str | None` (pure, tested); the reference-point line and the Precio|Distancia sort toggle; `_sorted_offers(offers, sort_key, reference, coords)` (pure, tested).

- [ ] **Step 1: Failing tests** (append):

```python
def test_distance_for_known_store():
    coords = {"celuphone": (-32.9386, -60.6801)}
    text = detail._distance_for("celuphone", (-32.9386, -60.6801), coords)
    assert text == "0 m"
    assert detail._distance_for("nowhere", (-32.9386, -60.6801), coords) is None
    assert detail._distance_for("celuphone", None, coords) is None


def test_sorted_offers_by_distance_puts_unknown_last():
    coords = {"near": (-32.95, -60.65), "far": (-34.60, -58.38)}
    near = StoreOffer(
        source_slug="near",
        title="a",
        price=Decimal("30000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    far = StoreOffer(
        source_slug="far",
        title="b",
        price=Decimal("10000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    unknown = StoreOffer(
        source_slug="web",
        title="c",
        price=Decimal("20000"),
        url="u",
        relevance="match",
        tier="incell",
    )
    result = detail._sorted_offers((far, unknown, near), "distancia", (-32.95, -60.65), coords)
    assert [o.source_slug for o in result] == ["near", "far", "web"]
    by_price = detail._sorted_offers((far, unknown, near), "precio", (-32.95, -60.65), coords)
    assert [o.source_slug for o in by_price] == ["far", "web", "near"]
```

- [ ] **Step 2: Run to verify they fail**, then implement in `detail.py`:

```python
from repuestos_radar.dashboard import distance


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


def _sorted_offers(offers, sort_key, reference, coords):
    if sort_key != "distancia" or reference is None:
        return tuple(sorted(offers, key=lambda o: o.price))

    def sort_value(offer):
        if offer.source_slug not in coords:
            return (1, float(offer.price))  # unknown position: last, then by price
        lat, lon = coords[offer.source_slug]
        return (0, distance.haversine_km(reference[0], reference[1], lat, lon))

    return tuple(sorted(offers, key=sort_value))


def _reference_point() -> tuple[float, float] | None:
    """The shop by default; the visitor's position while they opt in this visit."""
    from streamlit_geolocation import streamlit_geolocation

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
        location = streamlit_geolocation()  # renders the permission button
        if location and location.get("latitude") is not None:
            point = (location["latitude"], location["longitude"])
            if point != current:
                st.session_state["reference_point"] = point
                st.rerun()
    return current or shop
```

Wire into `render()`: after selecting the item, call `reference = _reference_point()` and `coords = _store_coords()`; add the sort toggle `sort = st.radio(text_es.SORT_LABEL, [text_es.SORT_PRICE, text_es.SORT_DISTANCE], horizontal=True)`; map it to `"precio"`/`"distancia"`; in each tier block iterate `_sorted_offers(analysis.offers, sort_key, reference, coords)` and pass `_distance_for(offer.source_slug, reference, coords)` as `distance_text` to `_offer_line`. (Guard the `streamlit_geolocation` import in try/except → on failure render without the button; AppTest and any component breakage degrade to shop-only.)

- [ ] **Step 3: Run everything** — `uv run pytest -q && uv run ruff check .`; Expected: PASS

- [ ] **Step 4: Commit** — `git commit -am "feat: distance column, location button, and distance sort on detail"`

### Task 15: README + docs

**Files:**
- Modify: `README.md`
- Test: none (docs)

- [ ] **Step 1: Update `README.md`:**
  - Roadmap M4 line becomes: `**M4 — Dashboard + admin page**: phone-first Streamlit app in Spanish behind a shared password — part cards, per-tier store ranking with straight-line distances, fair prices, margins, quick search on demand, and an admin page for repair prices and tracked parts.` Add after the M5 line: `- **Post-M4 — Public demo**: a portfolio-friendly deployment with sample data, no password, and an ES/EN toggle.`
  - New `## Dashboard (M4)` section after the report-CLI docs: how to run locally (`uv sync --extra dashboard && DATABASE_URL=... APP_PASSWORD=... uv run streamlit run streamlit_app.py`), the secrets table (`DATABASE_URL`, `APP_PASSWORD`, `SHOP_LAT`, `SHOP_LON` — what each is, all set in Streamlit Cloud's secrets UI, never committed), the quick-search cap and why Tiendanube sources are daily-only (robots.txt), and a `### Screenshots` subsection with the sentence "Screenshots of the deployed app:" followed by image links `docs/images/dashboard-home.png`, `docs/images/dashboard-detail.png`, `docs/images/dashboard-admin.png` (the PNGs are captured from the deployed app and committed as the deploy step's final action — see PR body).
  - Update the "Until the dashboard's admin page exists (M4)" sentences (README lines ~183 and ~200): the admin page now exists; the CLIs remain as internal team tools.

- [ ] **Step 2: Check rendering** — `uv run python -c "print(open('README.md').read().count('M4'))"` just to confirm the file saved; read the diff.

- [ ] **Step 3: Full check + commit + push + PR**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
git add README.md
git commit -m "docs: dashboard usage, secrets, and roadmap update"
git push -u origin feat/dashboard-admin
gh pr create --title "M4 PR4: admin page, distance, quick search button, docs" --body "..."
```

Mo reviews all `text_es.py` additions in this PR.

---

## Post-merge deploy checklist (Zahir + Claude together, not a code task)

1. On https://share.streamlit.io: New app → repo `ZahirJacob/repuestos-radar`, branch `main`, main file `streamlit_app.py`, Python 3.12.
2. Secrets (app settings → Secrets): `DATABASE_URL`, `APP_PASSWORD` (choose a good one), `SHOP_LAT`, `SHOP_LON` (the Activcelu shop — kept out of the public repo on purpose).
3. Open the URL on Zahir's phone and his dad's phone; add to home screen.
4. Take the three screenshots (home, detail, admin) from the deployed app, commit them to `docs/images/`.
5. Watch the first day: quick search from the phone, cookie survives a restart, distances sane.

## Spec self-review notes (done at planning time)

- Spec §5 said "each adapter gains a search-mode entry point": reality is better — Woo/Wix adapters are already search-based (`fetch(query)`), and Tiendanube cannot get one (robots-disallowed `/search/`), which the spec's skip clause already covers. The spec was amended to match before this plan (see spec changelog).
- Spec coverage: §1 app shape → Task 8; §2 home → Task 9; §3 detail → Task 10; §4 distance → Tasks 4, 5, 14; §5 quick search → Tasks 1–3, 13; §6 admin → Tasks 11–12; §7 auth/hosting → Tasks 6–7 + deploy checklist; §8 portfolio → Task 15 (+ post-M4 demo stays out of scope).
