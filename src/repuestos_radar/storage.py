"""Persistence of normalized listings with daily-snapshot idempotency.

The ``listings`` table enforces one snapshot per (source_slug, external_id,
fetched_date); re-running an ingestion for the same day must be a no-op for
rows already stored, without failing the batch. Both SQLite and PostgreSQL
support ``INSERT ... ON CONFLICT DO NOTHING`` through their dialect-specific
``insert()`` constructs, so we pick the construct by dialect; any other
dialect falls back to per-row SAVEPOINTs.
"""

from sqlalchemy import insert as generic_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repuestos_radar.models import Listing
from repuestos_radar.schema import NormalizedListing

_SNAPSHOT_KEY = ["source_slug", "external_id", "fetched_date"]
_CONFLICT_INSERTS = {"sqlite": sqlite_insert, "postgresql": pg_insert}


def _to_row(tracked_item_id: int, listing: NormalizedListing) -> dict:
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
    }


def save_listings(session: Session, tracked_item_id: int, listings: list[NormalizedListing]) -> int:
    """Insert listings as rows for one tracked item; skip already-stored daily snapshots.

    Returns the number of rows actually inserted. The caller owns the commit.
    """
    if not listings:
        return 0

    rows = [_to_row(tracked_item_id, listing) for listing in listings]
    dialect = session.get_bind().dialect.name

    insert_fn = _CONFLICT_INSERTS.get(dialect)
    if insert_fn is not None:
        # Single multi-VALUES Core insert: rowcount is the number actually inserted.
        stmt = (
            insert_fn(Listing.__table__)
            .values(rows)
            .on_conflict_do_nothing(index_elements=_SNAPSHOT_KEY)
        )
        result = session.execute(stmt)
        return result.rowcount

    # Portable fallback: one SAVEPOINT per row, so a duplicate rolls back only itself.
    inserted = 0
    for row in rows:
        try:
            with session.begin_nested():
                session.execute(generic_insert(Listing.__table__).values(row))
            inserted += 1
        except IntegrityError:
            pass
    return inserted
