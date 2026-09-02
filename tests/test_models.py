"""Tests for the SQLAlchemy models, run against SQLite in-memory."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repuestos_radar.models import Base, Listing, QuickSearchRun, TrackedItem


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_listing_row(tracked_item_id: int, **overrides) -> Listing:
    defaults = {
        "tracked_item_id": tracked_item_id,
        "source_slug": "novocell",
        "external_id": "abc-123",
        "title": "Módulo Samsung A32",
        "price": Decimal("45000.00"),
        "currency": "ARS",
        "condition": "new",
        "url": "https://novocell.com.ar/producto/modulo-a32",
        "fetched_date": date(2026, 8, 31),
    }
    defaults.update(overrides)
    return Listing(**defaults)


def test_tracked_item_roundtrip_and_defaults(session: Session) -> None:
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.commit()

    stored = session.scalars(select(TrackedItem)).one()
    assert stored.id is not None
    assert stored.query == "modulo samsung a32"
    assert stored.active is True
    assert stored.created_at is not None


def test_tracked_item_query_is_unique(session: Session) -> None:
    session.add(TrackedItem(query="modulo samsung a32"))
    session.commit()
    session.add(TrackedItem(query="modulo samsung a32"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_tracked_item_query_must_be_non_empty(session: Session) -> None:
    session.add(TrackedItem(query=""))
    with pytest.raises(IntegrityError):
        session.commit()


def test_listing_roundtrip(session: Session) -> None:
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.commit()

    session.add(make_listing_row(item.id))
    session.commit()

    stored = session.scalars(select(Listing)).one()
    assert stored.tracked_item_id == item.id
    assert stored.price == Decimal("45000.00")
    assert stored.fetched_date == date(2026, 8, 31)


def test_one_snapshot_per_listing_per_day(session: Session) -> None:
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.commit()

    session.add(make_listing_row(item.id))
    session.commit()
    # Same tracked item/source/external_id/date: violates the daily-snapshot constraint.
    session.add(make_listing_row(item.id, price=Decimal("46000.00")))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Same listing on another day is fine.
    session.add(make_listing_row(item.id, fetched_date=date(2026, 9, 1)))
    session.commit()


def test_same_listing_may_appear_under_two_tracked_items(session: Session) -> None:
    first = TrackedItem(query="modulo samsung a32")
    second = TrackedItem(query="pantalla samsung a32")
    session.add_all([first, second])
    session.commit()

    # Two searches surfacing the same listing on the same day is not a conflict.
    session.add_all([make_listing_row(first.id), make_listing_row(second.id)])
    session.commit()

    rows = session.scalars(select(Listing)).all()
    assert {row.tracked_item_id for row in rows} == {first.id, second.id}


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


def test_tracked_item_kind_defaults_to_part_and_stores_phone(session: Session) -> None:
    session.add(TrackedItem(query="modulo samsung a32"))
    session.add(TrackedItem(query="samsung s24 ultra", kind="phone"))
    session.commit()

    kinds = dict(session.execute(select(TrackedItem.query, TrackedItem.kind)).all())
    assert kinds == {"modulo samsung a32": "part", "samsung s24 ultra": "phone"}


def test_tracked_item_kind_is_checked_by_the_database(session: Session) -> None:
    session.add(TrackedItem(query="ipad", kind="tablet"))
    with pytest.raises(IntegrityError):
        session.commit()
