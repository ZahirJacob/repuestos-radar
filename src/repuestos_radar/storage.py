"""Persistence of normalized listings with daily-snapshot idempotency.

The ``listings`` table enforces one snapshot per (tracked_item_id,
source_slug, external_id, fetched_date); re-running an ingestion for the
same day must be a no-op for rows already stored, without failing the
batch. Both SQLite and PostgreSQL support ``INSERT ... ON CONFLICT DO
NOTHING`` through their dialect-specific ``insert()`` constructs, so we
pick the construct by dialect; any other dialect is unsupported.
"""

from itertools import batched

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from repuestos_radar.models import Listing
from repuestos_radar.relevance import ClassifiedListing
from repuestos_radar.schema import NormalizedListing

_SNAPSHOT_KEY = ["tracked_item_id", "source_slug", "external_id", "fetched_date"]
_CONFLICT_INSERTS = {"sqlite": sqlite_insert, "postgresql": pg_insert}
_CHUNK_SIZE = 500


def _to_row(
    tracked_item_id: int,
    listing: NormalizedListing,
    relevance: str | None = None,
    relevance_score: float | None = None,
) -> dict:
    return {
        "tracked_item_id": tracked_item_id,
        "source_slug": listing.source_slug,
        "external_id": listing.external_id,
        "title": listing.title,
        "price": listing.price,
        "currency": listing.currency,
        "condition": listing.condition.value,
        "url": listing.url,
        "fetched_date": listing.fetched_at,
        "relevance": relevance,
        "relevance_score": relevance_score,
    }


def _insert_rows(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    insert_fn = _CONFLICT_INSERTS.get(dialect)
    if insert_fn is None:
        raise NotImplementedError(
            f"save_listings supports the sqlite and postgresql dialects; got '{dialect}'"
        )
    inserted = 0
    # Chunked multi-VALUES Core inserts (bound-parameter limits). Rows actually
    # inserted are counted via RETURNING: with ON CONFLICT DO NOTHING, RETURNING
    # yields a row per insert that happened and nothing for skipped conflicts,
    # on both SQLite (3.35+) and PostgreSQL. Do NOT count via .rowcount here —
    # it is dialect-unreliable for this construct: psycopg3 reported -1
    # ("unknown") per batch in production while SQLite returned real counts,
    # so tests passed and the run report printed negative insert counts.
    for chunk in batched(rows, _CHUNK_SIZE):
        stmt = (
            insert_fn(Listing.__table__)
            .values(list(chunk))
            .on_conflict_do_nothing(index_elements=_SNAPSHOT_KEY)
            .returning(Listing.__table__.c.id)
        )
        inserted += len(session.execute(stmt).fetchall())
    return inserted


def save_listings(session: Session, tracked_item_id: int, listings: list[NormalizedListing]) -> int:
    """Insert unclassified listings; skip already-stored daily snapshots.

    Relevance columns are left NULL. Returns the number of rows actually
    inserted. The caller owns the commit.
    """
    rows = [_to_row(tracked_item_id, listing) for listing in listings]
    return _insert_rows(session, rows)


def save_classified_listings(
    session: Session, tracked_item_id: int, classified: list[ClassifiedListing]
) -> int:
    """Insert classified listings, persisting each relevance label and score.

    REJECT-labeled listings are stored too (the filter never drops rows); a
    later query decides what to surface. Returns the number of rows actually
    inserted; the caller owns the commit.

    Note: the daily snapshot is immutable — ON CONFLICT DO NOTHING means a
    same-day re-run does NOT re-label an already-stored (source, external_id,
    date) row, even if its relevance would now differ.
    """
    rows = [
        _to_row(
            tracked_item_id,
            item.listing,
            relevance=item.result.relevance.value,
            relevance_score=item.result.score,
        )
        for item in classified
    ]
    return _insert_rows(session, rows)
