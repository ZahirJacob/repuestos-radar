"""End-to-end ingestion runner: fetch, classify, persist, report.

For every active :class:`~repuestos_radar.models.TrackedItem`, every vetted
source is queried through its adapter, the listings are labeled by the
relevance filter, and the classified listings are stored as daily snapshots.
One adapter instance per source is reused across all tracked items so the
shared polite HTTP client enforces the courtesy delay per host.

Failure isolation is per source: a source that raises (network, robots,
parse, or anything unexpected) is given up for the rest of the run — its
failure is recorded in the report and the remaining sources continue. The
session is committed after each (tracked item, source) save, so partial
progress survives a crash; storage is idempotent per day, so re-runs are
safe.

Runnable as ``python -m repuestos_radar.ingest``. Exit code 0 when the run
completed and at least one source succeeded (a run with no active tracked
items is a successful no-op); 1 when every source failed or the run could
not proceed at all (bad config, unreachable database).
"""

import sys
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from repuestos_radar.adapters import Adapter, AdapterError, adapter_for
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import TrackedItem
from repuestos_radar.relevance import ClassifiedListing, Relevance, apply_relevance
from repuestos_radar.sources import Source, load_sources
from repuestos_radar.storage import save_classified_listings


@dataclass(slots=True)
class SourceReport:
    """What one source did during the run — counts plus a failure, if any."""

    slug: str
    items_queried: int = 0
    listings_fetched: int = 0
    malformed_skipped: int = 0
    inserted: int = 0
    already_stored: int = 0
    matches: int = 0
    low_confidence: int = 0
    rejects: int = 0
    failure: str | None = None


@dataclass(slots=True)
class RunReport:
    """Outcome of one ingestion run across all sources."""

    active_items: int
    sources: list[SourceReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the run counts as a success (see module docstring)."""
        if self.active_items == 0:
            return True
        return any(source.failure is None for source in self.sources)


def build_adapters(sources: Sequence[Source]) -> list[Adapter]:
    """One adapter per source. An unknown platform is a config error: raise
    ValueError before any fetching, closing whatever was already built."""
    adapters: list[Adapter] = []
    try:
        for source in sources:
            adapters.append(adapter_for(source))
    except ValueError:
        for adapter in adapters:
            adapter.close()
        raise
    return adapters


def _count_relevance(report: SourceReport, classified: list[ClassifiedListing]) -> None:
    for item in classified:
        if item.result.relevance is Relevance.MATCH:
            report.matches += 1
        elif item.result.relevance is Relevance.LOW_CONFIDENCE:
            report.low_confidence += 1
        else:
            report.rejects += 1


def run_ingestion(session: Session, adapters: Sequence[Adapter]) -> RunReport:
    """Fetch, classify, and persist every (source, active tracked item) pair.

    Commits after each save; on a source failure the session is rolled back,
    the source is abandoned for the rest of the run (courtesy: a failing host
    is not hammered once per item), and the remaining sources continue.
    """
    items = session.scalars(
        select(TrackedItem).where(TrackedItem.active).order_by(TrackedItem.id)
    ).all()
    report = RunReport(
        active_items=len(items),
        sources=[SourceReport(slug=adapter.source.slug) for adapter in adapters],
    )
    if not items:
        return report

    for adapter, source_report in zip(adapters, report.sources, strict=True):
        for item in items:
            try:
                listings = adapter.fetch(item.query)
                classified = apply_relevance(item.query, listings)
                inserted = save_classified_listings(session, item.id, classified)
                session.commit()
            except AdapterError as exc:
                session.rollback()
                source_report.failure = str(exc)
                break
            except Exception as exc:  # unexpected: isolate it like an AdapterError
                session.rollback()
                source_report.failure = f"unexpected {type(exc).__name__}: {exc}"
                break
            source_report.items_queried += 1
            source_report.listings_fetched += len(listings)
            source_report.malformed_skipped += adapter.skipped
            source_report.inserted += inserted
            source_report.already_stored += len(classified) - inserted
            _count_relevance(source_report, classified)
    return report


def format_report(report: RunReport) -> str:
    """Render the run report as grep-able key=value lines for the Actions log."""
    lines = [
        f"ingestion run: {report.active_items} active tracked item(s), "
        f"{len(report.sources)} source(s)"
    ]
    if report.active_items == 0:
        lines.append("no active tracked items; nothing to ingest (successful no-op)")
    for s in report.sources:
        line = (
            f"source={s.slug} items={s.items_queried} fetched={s.listings_fetched} "
            f"skipped={s.malformed_skipped} inserted={s.inserted} "
            f"already_stored={s.already_stored} match={s.matches} "
            f"low_confidence={s.low_confidence} reject={s.rejects}"
        )
        if s.failure is None:
            line += " status=ok"
        else:
            line += f' status=failed error="{s.failure}"'
        lines.append(line)
    ok_count = sum(1 for s in report.sources if s.failure is None)
    lines.append(
        f"summary: sources_ok={ok_count}/{len(report.sources)} "
        f"fetched={sum(s.listings_fetched for s in report.sources)} "
        f"inserted={sum(s.inserted for s in report.sources)} "
        f"already_stored={sum(s.already_stored for s in report.sources)} "
        f"result={'success' if report.ok else 'failure'}"
    )
    return "\n".join(lines)


def main() -> int:
    """CLI entry point: wire config, DB, and adapters around :func:`run_ingestion`."""
    try:
        sources = load_sources()
        adapters = build_adapters(sources)
    except (ValueError, OSError) as exc:
        print(f"ingestion aborted (config error): {exc}")
        return 1
    try:
        with ExitStack() as stack:
            for adapter in adapters:
                stack.enter_context(adapter)
            engine = get_engine()
            init_db(engine)
            with get_session_factory(engine)() as session:
                report = run_ingestion(session, adapters)
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"ingestion aborted (database error): {exc}")
        return 1
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
