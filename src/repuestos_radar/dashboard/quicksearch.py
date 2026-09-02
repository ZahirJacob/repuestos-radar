"""On-demand quick search: one tracked item, all search-capable sources, ~1 minute.

The daily deep crawl is the source of complete history; this is the
counter-moment feature — a customer is waiting, so we query each store's own
search endpoint for ONE item and store the results through the exact same
classify-and-save path as the daily run.

Courtesy: sources run in parallel THREADS, but each source keeps its own
sequential PoliteHttpClient — per-host the 1-second delay is intact; no store
sees more load than a single polite visitor. Tiendanube storefronts are
skipped: the platform robots-disallows /search/, and their catalog crawl is
exactly the slow path this feature avoids (skip, don't work around). Sources
flagged cloud_blocked for the ``quick`` channel in the registry (they 403
Streamlit Cloud) are left out too, and reported separately so the UI can say
why; a store blocked only for the daily run is still searched here.

A hard daily cap (DAILY_CAP runs per Argentine calendar day, recorded in the
quick_search_runs table) keeps the feature honest even if the button gets
tapped enthusiastically.
"""

import argparse
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from repuestos_radar.adapters import Adapter, AdapterError
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.ingest import build_adapters
from repuestos_radar.models import QuickSearchRun, TrackedItem
from repuestos_radar.relevance import ClassifiedListing, Relevance, apply_relevance
from repuestos_radar.sources import Source, load_sources
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
    blocked: list[QuickSourceReport] = field(default_factory=list)
    """Sources cloud_blocked for the quick channel, left out of the search
    (registry order, never searched).

    Kept apart from ``sources`` so the UI can explain them with their own note
    instead of the crawl-only one.
    """


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


def _search_one(
    adapter: Adapter, query: str, kind: str
) -> tuple[list[ClassifiedListing], int, str | None]:
    """Fetch + classify in a worker thread; DB writes stay on the caller's thread."""
    try:
        listings = adapter.fetch(query)
    except AdapterError as exc:
        return [], adapter.skipped, " ".join(str(exc).split())
    except Exception as exc:  # unexpected: isolate like the ingest runner does
        return [], adapter.skipped, f"unexpected {type(exc).__name__}: {exc}"
    return apply_relevance(query, listings, kind=kind), adapter.skipped, None


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
    with searched=False (the UI explains them with QUICK_SEARCH_SKIPPED_NOTE,
    "el radar solo busca ahí una vez por día"). Sources
    flagged cloud_blocked for the quick channel are not searched either; they land in
    ``report.blocked`` so the UI can show a separate note. When the
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

        report.blocked = [
            QuickSourceReport(slug=s.slug, name=s.name, searched=False)
            for s in sources
            if s.is_blocked("quick")
        ]
        sources = [s for s in sources if not s.is_blocked("quick")]
        searchable = [s for s in sources if s.platform in SEARCHABLE_PLATFORMS]
        for source in sources:
            if source.platform not in SEARCHABLE_PLATFORMS:
                report.sources.append(
                    QuickSourceReport(slug=source.slug, name=source.name, searched=False)
                )
        # Built before the run is recorded: a config error here (unsupported
        # platform) must abort cleanly without spending a cap slot — matches
        # ingest.py, which resolves adapters before touching the database.
        if adapters is None:
            adapters = build_adapters(searchable)

        # Recorded up front: a run that fails halfway still visited the stores,
        # so it still spends cap.
        session.add(QuickSearchRun(tracked_item_id=item.id, ran_on=argentina_today()))
        session.commit()

        with ExitStack() as stack:
            for adapter in adapters:
                stack.enter_context(adapter)
            with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
                futures = {
                    pool.submit(_search_one, adapter, item.query, item.kind): adapter
                    for adapter in adapters
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
    for s in report.blocked:
        lines.append(f"source={s.slug} searched=no reason=cloud_blocked")
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
    except ValueError as exc:
        print(f"quick search aborted (config error): {' '.join(str(exc).split())}")
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
