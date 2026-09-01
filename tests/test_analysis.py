"""Tests for the compute-on-demand analysis layer."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.analysis import (
    BASIS_MEDIAN,
    BASIS_SINGLE_STORE,
    analyze_item,
    latest_day,
    listings_for_day,
    tier_trends,
)
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, TrackedItem
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


def test_outlier_excluded_from_fair_price_but_still_shown():
    listings = [
        row("novocell", "Modulo A32 OLED", "45000"),
        row("celuphone", "Modulo A32 OLED", "41000"),
        row("tienda-movil", "Modulo A32 OLED", "48000"),
        row("gofix", "Modulo A32 OLED", "9000"),  # < 0.5x median -> weird
    ]
    (oled,) = analyze_item(listings)
    flagged = [o for o in oled.offers if o.outlier]
    assert [o.source_slug for o in flagged] == ["gofix"]
    assert len(oled.offers) == 4  # nothing hidden
    assert oled.store_count == 3  # but only 3 contribute
    assert oled.fair_price == Decimal("45000")


def test_small_groups_are_never_flagged():
    listings = [
        row("novocell", "Modulo A32 OLED", "45000"),
        row("celuphone", "Modulo A32 OLED", "41000"),
        row("gofix", "Modulo A32 OLED", "9000"),  # only 3 stores: not flagged
    ]
    (oled,) = analyze_item(listings)
    assert not any(o.outlier for o in oled.offers)
    assert oled.fair_price == Decimal("41000")


def test_all_but_one_flagged_degrades_to_single_store():
    # Wild spread: median is 100, so <50 and >200 are flagged — everything
    # but the median offer. The basis must degrade honestly to single-store.
    listings = [
        row("novocell", "Modulo A32 OLED", "1"),
        row("celuphone", "Modulo A32 OLED", "10"),
        row("tienda-movil", "Modulo A32 OLED", "100"),
        row("gofix", "Modulo A32 OLED", "1000"),
        row("mdrepuestos", "Modulo A32 OLED", "10000"),
    ]
    (oled,) = analyze_item(listings)
    assert len(oled.offers) == 5  # nothing hidden
    assert [o.source_slug for o in oled.offers if not o.outlier] == ["tienda-movil"]
    assert oled.store_count == 1
    assert oled.fair_price is None
    assert oled.basis == BASIS_SINGLE_STORE


def test_prices_exactly_at_outlier_boundaries_are_not_flagged():
    # Median is 40000; the thresholds are strict (<0.5x, >2x), so exactly
    # 0.5x (20000) and exactly 2x (80000) stay unflagged and contribute.
    listings = [
        row("novocell", "Modulo A32 OLED", "20000"),
        row("celuphone", "Modulo A32 OLED", "30000"),
        row("tienda-movil", "Modulo A32 OLED", "50000"),
        row("gofix", "Modulo A32 OLED", "80000"),
    ]
    (oled,) = analyze_item(listings)
    assert not any(o.outlier for o in oled.offers)
    assert oled.store_count == 4
    assert oled.fair_price == Decimal("40000")
    assert oled.basis == BASIS_MEDIAN


@pytest.fixture()
def session():
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


def _store_listing(
    session, item_id, source, price, day, relevance="match", title="Modulo A32 OLED"
):
    session.add(
        Listing(
            tracked_item_id=item_id,
            source_slug=source,
            external_id=f"{source}-{price}",
            title=title,
            price=Decimal(price),
            currency="ARS",
            condition="unknown",
            url="https://example.test/p",
            fetched_date=day,
            relevance=relevance,
            relevance_score=1.0,
        )
    )


def test_latest_day_and_day_filtering(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    _store_listing(session, item.id, "novocell", "45000", date(2026, 8, 31))
    _store_listing(session, item.id, "novocell", "46000", date(2026, 9, 1))
    _store_listing(session, item.id, "celuphone", "41000", date(2026, 9, 1))
    _store_listing(session, item.id, "gofix", "40000", date(2026, 9, 1), relevance="reject")
    session.commit()

    assert latest_day(session, item.id) == date(2026, 9, 1)
    rows = listings_for_day(session, item.id, date(2026, 9, 1))
    assert sorted(r.source_slug for r in rows) == ["celuphone", "novocell"]  # reject excluded


def test_latest_day_none_when_empty(session):
    item = TrackedItem(query="bateria iphone 11")
    session.add(item)
    session.commit()
    assert latest_day(session, item.id) is None


def test_a32_two_tier_spread_from_real_data():
    """The A32 modulo real-world case: copies and originals must not mix."""
    listings = [
        row("novocell", "Modulo Samsung A32 Incell", "20700"),
        row("tienda-movil", "Modulo A32 TFT sin marco", "24500"),
        row("novocell", "Modulo Samsung A32 OLED con marco", "45000"),
        row("celuphone", "Pantalla A32 AMOLED", "41000"),
        row("mdrepuestos", "Modulo Samsung A32 Original Service Pack", "58700"),
    ]
    analyses = analyze_item(listings)
    by_tier = {a.tier: a for a in analyses}
    assert by_tier["incell"].fair_price == Decimal("22600")  # median of 20700, 24500
    assert by_tier["oled"].offers[0].source_slug == "celuphone"
    assert by_tier["original"].basis == BASIS_SINGLE_STORE
    assert by_tier["original"].fair_price is None
    # Display order: better tiers first.
    assert [a.tier for a in analyses] == ["original", "oled", "incell"]


def test_trend_compares_against_nearest_stored_day(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    today = date(2026, 9, 1)
    # 8 days back (within +-2 of the 7-day target): OLED fair price 40000.
    for source, price in (("novocell", "40000"), ("celuphone", "40000")):
        _store_listing(session, item.id, source, price, date(2026, 8, 24), title="Modulo A32 OLED")
    # Today: OLED fair price 44000 -> +10% vs the 7-day point.
    for source, price in (("novocell", "44000"), ("celuphone", "44000")):
        _store_listing(session, item.id, source, price, today, title="Modulo A32 OLED")
    session.commit()

    week, month = tier_trends(session, item.id, "oled", today)
    assert (week.days_back, week.direction) == (7, "↑")
    assert week.compared_date == date(2026, 8, 24)
    assert week.pct_change == Decimal("10.0")
    assert (month.direction, month.pct_change) == ("", None)  # no data ~30 days back
