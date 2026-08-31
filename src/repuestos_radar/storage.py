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
from repuestos_radar.schema import NormalizedListing

_SNAPSHOT_KEY = ["tracked_item_id", "source_slug", "external_id", "fetched_date"]
_CONFLICT_INSERTS = {"sqlite": sqlite_insert, "postgresql": pg_insert}
_CHUNK_SIZE = 500


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

    dialect = session.get_bind().dialect.name
    insert_fn = _CONFLICT_INSERTS.get(dialect)
    if insert_fn is None:
        raise NotImplementedError(
            f"save_listings supports the sqlite and postgresql dialects; got '{dialect}'"
        )

    rows = [_to_row(tracked_item_id, listing) for listing in listings]
    inserted = 0
    # Chunked multi-VALUES Core inserts (bound-parameter limits): per chunk,
    # rowcount is the number actually inserted after conflicts are skipped.
    for chunk in batched(rows, _CHUNK_SIZE):
        stmt = (
            insert_fn(Listing.__table__)
            .values(list(chunk))
            .on_conflict_do_nothing(index_elements=_SNAPSHOT_KEY)
        )
        inserted += session.execute(stmt).rowcount
    return inserted
