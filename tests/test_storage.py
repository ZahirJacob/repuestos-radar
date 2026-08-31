"""Tests for idempotent listing persistence, run against SQLite in-memory."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from repuestos_radar.models import Base, Listing, TrackedItem
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.storage import save_listings


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def tracked_item_id(session: Session) -> int:
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.commit()
    return item.id


def make_listing(**overrides) -> NormalizedListing:
    defaults = {
        "source_slug": "novocell",
        "external_id": "abc-123",
        "title": "Módulo Samsung A32",
        "price": Decimal("45000.00"),
        "currency": "ARS",
        "condition": Condition.NEW,
        "url": "https://novocell.com.ar/producto/modulo-a32",
        "fetched_at": date(2026, 8, 31),
    }
    defaults.update(overrides)
    return NormalizedListing(**defaults)


def test_saves_listings_as_rows(session: Session, tracked_item_id: int) -> None:
    inserted = save_listings(
        session,
        tracked_item_id,
        [make_listing(), make_listing(external_id="def-456", price=Decimal("47500.50"))],
    )
    session.commit()

    assert inserted == 2
    rows = session.scalars(select(Listing).order_by(Listing.external_id)).all()
    assert [row.external_id for row in rows] == ["abc-123", "def-456"]
    first = rows[0]
    assert first.tracked_item_id == tracked_item_id
    assert first.source_slug == "novocell"
    assert first.price == Decimal("45000.00")
    assert first.currency == "ARS"
    assert first.condition == "new"
    assert first.fetched_date == date(2026, 8, 31)


def test_same_snapshot_twice_is_idempotent(session: Session, tracked_item_id: int) -> None:
    assert save_listings(session, tracked_item_id, [make_listing()]) == 1
    session.commit()

    # Re-running the same day (even with a changed price) inserts nothing new.
    inserted = save_listings(session, tracked_item_id, [make_listing(price=Decimal("46000.00"))])
    session.commit()

    assert inserted == 0
    row = session.scalars(select(Listing)).one()
    assert row.price == Decimal("45000.00")  # first snapshot of the day wins


def test_duplicate_does_not_block_new_listings_in_same_batch(
    session: Session, tracked_item_id: int
) -> None:
    save_listings(session, tracked_item_id, [make_listing()])
    session.commit()

    inserted = save_listings(
        session,
        tracked_item_id,
        [make_listing(), make_listing(external_id="def-456")],
    )
    session.commit()

    assert inserted == 1
    assert session.scalars(select(Listing)).all() != []
    assert {row.external_id for row in session.scalars(select(Listing))} == {"abc-123", "def-456"}


def test_next_day_snapshot_is_inserted(session: Session, tracked_item_id: int) -> None:
    save_listings(session, tracked_item_id, [make_listing()])
    session.commit()

    inserted = save_listings(session, tracked_item_id, [make_listing(fetched_at=date(2026, 9, 1))])
    session.commit()

    assert inserted == 1
    assert len(session.scalars(select(Listing)).all()) == 2


def test_empty_batch_is_a_no_op(session: Session, tracked_item_id: int) -> None:
    assert save_listings(session, tracked_item_id, []) == 0
    session.commit()
    assert session.scalars(select(Listing)).all() == []
