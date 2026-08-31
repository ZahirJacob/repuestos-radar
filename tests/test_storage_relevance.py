"""Tests for persisting relevance labels alongside listings. SQLite in-memory."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from repuestos_radar.models import Base, Listing, TrackedItem
from repuestos_radar.relevance import apply_relevance
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.storage import save_classified_listings, save_listings


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def tracked_item_id(session: Session) -> int:
    item = TrackedItem(query="modulo a34")
    session.add(item)
    session.commit()
    return item.id


def make_listing(title: str, external_id: str) -> NormalizedListing:
    return NormalizedListing(
        source_slug="celuphone",
        external_id=external_id,
        title=title,
        price=Decimal("10000"),
        currency="ARS",
        condition=Condition.UNKNOWN,
        url="https://celuphone.com.ar/producto/x",
        fetched_at=date(2026, 8, 31),
    )


def test_plain_save_leaves_relevance_null(session: Session, tracked_item_id: int) -> None:
    save_listings(session, tracked_item_id, [make_listing("Modulo Samsung A34", "1")])
    session.commit()
    row = session.scalars(select(Listing)).one()
    assert row.relevance is None
    assert row.relevance_score is None


def test_classified_save_persists_labels_and_scores(session: Session, tracked_item_id: int) -> None:
    listings = [
        make_listing("Modulo Samsung A34 Oled Con Marco", "1"),
        make_listing("Funda Samsung A34 Silicona", "2"),
        make_listing("Modulo Samsung A54 Oled", "3"),
    ]
    classified = apply_relevance("modulo a34", listings)
    inserted = save_classified_listings(session, tracked_item_id, classified)
    session.commit()

    assert inserted == 3  # REJECTs are stored too, nothing dropped
    rows = {r.external_id: r for r in session.scalars(select(Listing))}
    assert rows["1"].relevance == "match"
    assert rows["2"].relevance == "reject"
    assert rows["3"].relevance == "reject"
    assert isinstance(rows["1"].relevance_score, float)


def test_classified_save_is_idempotent(session: Session, tracked_item_id: int) -> None:
    classified = apply_relevance("modulo a34", [make_listing("Modulo Samsung A34", "1")])
    assert save_classified_listings(session, tracked_item_id, classified) == 1
    session.commit()
    assert save_classified_listings(session, tracked_item_id, classified) == 0
    session.commit()
    assert len(session.scalars(select(Listing)).all()) == 1


def test_classified_empty_batch_is_a_no_op(session: Session, tracked_item_id: int) -> None:
    assert save_classified_listings(session, tracked_item_id, []) == 0
