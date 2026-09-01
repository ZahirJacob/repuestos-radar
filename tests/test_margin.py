"""Tests for the service price list and margin math."""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from repuestos_radar.analysis import analyze_item
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.margin import margins_for
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


# Local copy of the fake-row helper (tests are standalone files, not a
# package — never import from a sibling test module).
@dataclass(frozen=True)
class Row:
    source_slug: str
    title: str
    price: Decimal
    url: str = "https://example.test/p"
    relevance: str = "match"


def row(source: str, title: str, price: str, relevance: str = "match") -> Row:
    return Row(source_slug=source, title=title, price=Decimal(price), relevance=relevance)


def test_margin_per_tier_uses_cheapest_non_outlier_part():
    analyses = analyze_item(
        [
            row("novocell", "Modulo A32 Incell", "20700"),
            row("tienda-movil", "Modulo A32 TFT", "24500"),
            row("novocell", "Modulo A32 OLED", "45000"),
            row("celuphone", "Pantalla A32 AMOLED", "41000"),
        ]
    )
    margins = margins_for(Decimal("75000"), analyses)
    by_tier = {m.tier: m for m in margins}
    assert by_tier["incell"].margin == Decimal("54300")
    assert by_tier["incell"].part_source == "novocell"
    assert by_tier["oled"].margin == Decimal("34000")


def test_all_outlier_tier_is_skipped():
    analyses = analyze_item(
        [
            row("novocell", "Modulo A32 OLED", "45000"),
            row("celuphone", "Modulo A32 OLED", "44000"),
            row("tienda-movil", "Modulo A32 OLED", "46000"),
            row("gofix", "Modulo A32 OLED", "9000"),  # flagged outlier
        ]
    )
    margins = margins_for(Decimal("75000"), analyses)
    (oled,) = margins
    assert oled.part_price == Decimal("44000")  # outlier never the margin basis
