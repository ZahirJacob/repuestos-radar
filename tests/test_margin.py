"""Tests for the service price list and margin math."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import ServicePrice, TrackedItem


@pytest.fixture()
def session():
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


@pytest.fixture()
def item(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    return item


def test_service_price_round_trip(session, item):
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("75000"))
    )
    session.commit()
    stored = session.query(ServicePrice).one()
    assert stored.price_ars == Decimal("75000")
    assert stored.updated_at is not None


def test_service_price_label_is_unique(session, item):
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("75000"))
    )
    session.commit()
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("80000"))
    )
    with pytest.raises(IntegrityError):
        session.commit()
