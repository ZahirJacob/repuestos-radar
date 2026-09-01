"""Tests for the compute-on-demand analysis layer."""

from dataclasses import dataclass
from decimal import Decimal

from repuestos_radar.analysis import BASIS_MEDIAN, BASIS_SINGLE_STORE, analyze_item
from repuestos_radar.quality import TIER_OLED, TIER_UNLABELED


@dataclass(frozen=True)
class Row:
    """Duck-typed stand-in for a Listing ORM row."""

    source_slug: str
    title: str
    price: Decimal
    url: str = "https://example.test/p"
    relevance: str = "match"


def row(source: str, title: str, price: str, relevance: str = "match") -> Row:
    return Row(source_slug=source, title=title, price=Decimal(price), relevance=relevance)


def test_groups_by_tier_and_keeps_cheapest_per_store():
    listings = [
        row("novocell", "Modulo A32 OLED", "45000"),
        row("novocell", "Modulo A32 OLED premium", "52000"),  # same store, pricier
        row("celuphone", "Pantalla A32 AMOLED", "41000"),
        row("novocell", "Modulo A32 4G", "21000"),  # unlabeled tier
    ]
    analyses = {a.tier: a for a in analyze_item(listings)}
    oled = analyses[TIER_OLED]
    assert [o.source_slug for o in oled.offers] == ["celuphone", "novocell"]  # cheapest first
    assert oled.offers[1].price == Decimal("45000")  # per store, only its cheapest competes
    assert analyses[TIER_UNLABELED].offers[0].price == Decimal("21000")


def test_low_confidence_flag_travels_with_the_offer():
    listings = [row("gofix", "Modulo A32 OLED", "40000", relevance="low_confidence")]
    (oled,) = analyze_item(listings)
    assert oled.offers[0].relevance == "low_confidence"


def test_single_store_has_no_fair_price():
    (only,) = analyze_item([row("novocell", "Modulo A32 OLED", "45000")])
    assert only.fair_price is None
    assert only.store_count == 1
    assert only.basis == BASIS_SINGLE_STORE


def test_fair_price_is_the_median_across_stores():
    listings = [
        row("novocell", "Modulo A32 OLED", "45000"),
        row("celuphone", "Modulo A32 OLED", "41000"),
        row("tienda-movil", "Modulo A32 OLED", "48000"),
    ]
    (oled,) = analyze_item(listings)
    assert oled.fair_price == Decimal("45000")
    assert (oled.price_min, oled.price_max) == (Decimal("41000"), Decimal("48000"))
    assert oled.store_count == 3
    assert oled.basis == BASIS_MEDIAN


def test_empty_input_yields_no_groups():
    assert analyze_item([]) == []
