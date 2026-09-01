"""Compute-on-demand analysis over stored listings.

Pure functions: rows in, dataclasses out. No printing (report.py owns all
Spanish text), no HTTP, nothing precomputed or stored. The M4 dashboard
imports these same functions.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repuestos_radar.models import Listing
from repuestos_radar.quality import (
    DEVICE_NEW,
    DEVICE_REFURBISHED,
    TIER_INCELL,
    TIER_OLED,
    TIER_ORIGINAL,
    TIER_UNLABELED,
    label_tier,
)

BASIS_MEDIAN = "median"
BASIS_SINGLE_STORE = "single-store"

MIN_STORES_FOR_OUTLIERS = 4
"""Below this many stores in a tier group, nothing is ever flagged as weird."""

OUTLIER_LOW_FACTOR = Decimal("0.5")
OUTLIER_HIGH_FACTOR = Decimal("2")

TIER_DISPLAY_ORDER = (
    TIER_ORIGINAL,
    TIER_OLED,
    TIER_INCELL,
    DEVICE_NEW,
    DEVICE_REFURBISHED,
    TIER_UNLABELED,
)


@dataclass(frozen=True, slots=True)
class StoreOffer:
    """One store's best (cheapest) offer for an item in one tier."""

    source_slug: str
    title: str
    price: Decimal
    url: str
    relevance: str  # "match" | "low_confidence"
    tier: str
    outlier: bool = False


@dataclass(frozen=True, slots=True)
class TierAnalysis:
    """Everything the UI needs about one (item, tier) group for one day."""

    tier: str
    offers: tuple[StoreOffer, ...]  # cheapest first; outliers included, flagged
    fair_price: Decimal | None  # None when basis is single-store
    price_min: Decimal | None
    price_max: Decimal | None
    store_count: int  # stores contributing to fair price (non-outlier)
    basis: str  # BASIS_MEDIAN | BASIS_SINGLE_STORE


def analyze_item(listings: Sequence) -> list[TierAnalysis]:
    """Tier analyses for one tracked item's relevant listings on one day.

    Input rows need .source_slug/.title/.price/.url/.relevance and must
    already be filtered to relevance match/low_confidence (listings_for_day
    does that filtering for DB rows).
    """
    by_tier: dict[str, dict[str, StoreOffer]] = {}
    for listing in listings:
        tier = label_tier(listing.title)
        offer = StoreOffer(
            source_slug=listing.source_slug,
            title=listing.title,
            price=listing.price,
            url=listing.url,
            relevance=listing.relevance,
            tier=tier,
        )
        per_store = by_tier.setdefault(tier, {})
        current = per_store.get(offer.source_slug)
        if current is None or offer.price < current.price:
            per_store[offer.source_slug] = offer

    analyses = []
    for tier in TIER_DISPLAY_ORDER:
        if tier not in by_tier:
            continue
        offers = tuple(sorted(by_tier[tier].values(), key=lambda o: o.price))
        analyses.append(_analyze_tier(tier, offers))
    return analyses


def _analyze_tier(tier: str, offers: tuple[StoreOffer, ...]) -> TierAnalysis:
    offers = _flag_outliers(offers)
    contributing = [offer.price for offer in offers if not offer.outlier]
    if len(contributing) <= 1:
        return TierAnalysis(
            tier=tier,
            offers=offers,
            fair_price=None,
            price_min=contributing[0] if contributing else None,
            price_max=contributing[0] if contributing else None,
            store_count=len(contributing),
            basis=BASIS_SINGLE_STORE,
        )
    return TierAnalysis(
        tier=tier,
        offers=offers,
        fair_price=median(contributing),
        price_min=min(contributing),
        price_max=max(contributing),
        store_count=len(contributing),
        basis=BASIS_MEDIAN,
    )


def _flag_outliers(offers: tuple[StoreOffer, ...]) -> tuple[StoreOffer, ...]:
    """Flag prices under half / over double the group median.

    Deliberately conservative: groups smaller than MIN_STORES_FOR_OUTLIERS are
    never flagged (too little data to call anything weird), and the median is
    taken over the whole group, candidate included — with groups this small,
    leave-one-out schemes overfit.
    """
    if len(offers) < MIN_STORES_FOR_OUTLIERS:
        return offers
    group_median = median(offer.price for offer in offers)
    return tuple(
        replace(
            offer,
            outlier=(
                offer.price < group_median * OUTLIER_LOW_FACTOR
                or offer.price > group_median * OUTLIER_HIGH_FACTOR
            ),
        )
        for offer in offers
    )


RELEVANT = ("match", "low_confidence")


def latest_day(session: Session, tracked_item_id: int) -> date | None:
    """Most recent day this item has any stored listing, or None."""
    return session.scalar(
        select(func.max(Listing.fetched_date)).where(Listing.tracked_item_id == tracked_item_id)
    )


def listings_for_day(session: Session, tracked_item_id: int, day: date) -> list[Listing]:
    """One day's relevant listings for one item (reject/unclassified excluded)."""
    return list(
        session.scalars(
            select(Listing).where(
                Listing.tracked_item_id == tracked_item_id,
                Listing.fetched_date == day,
                Listing.relevance.in_(RELEVANT),
            )
        )
    )


TREND_WINDOWS = (7, 30)
_TREND_TOLERANCE_DAYS = 2
_FLAT_THRESHOLD_PCT = Decimal("1")


@dataclass(frozen=True, slots=True)
class TrendPoint:
    """Fair price today vs. ~N days ago. Empty direction = nothing to compare."""

    days_back: int
    compared_date: date | None
    direction: str  # "↑" | "↓" | "=" | ""
    pct_change: Decimal | None


def tier_trends(session: Session, tracked_item_id: int, tier: str, today: date) -> list[TrendPoint]:
    """Trend points for the standard windows, tolerant of missing days.

    Daily runs can fail; each window compares against the nearest stored day
    within +-_TREND_TOLERANCE_DAYS of the target, or reports "no data".
    """
    today_fair = _fair_price_on(session, tracked_item_id, tier, today)
    points = []
    for days_back in TREND_WINDOWS:
        target = today - timedelta(days=days_back)
        compared_date, past_fair = _nearest_fair_price(session, tracked_item_id, tier, target)
        if today_fair is None or past_fair is None:
            points.append(TrendPoint(days_back, None, "", None))
            continue
        pct = ((today_fair - past_fair) / past_fair * 100).quantize(Decimal("0.1"))
        if abs(pct) < _FLAT_THRESHOLD_PCT:
            direction = "="
        elif pct > 0:
            direction = "↑"
        else:
            direction = "↓"
        points.append(TrendPoint(days_back, compared_date, direction, pct))
    return points


def _fair_price_on(session: Session, tracked_item_id: int, tier: str, day: date) -> Decimal | None:
    for analysis in analyze_item(listings_for_day(session, tracked_item_id, day)):
        if analysis.tier == tier:
            return analysis.fair_price
    return None


def _nearest_fair_price(
    session: Session, tracked_item_id: int, tier: str, target: date
) -> tuple[date | None, Decimal | None]:
    stored_days = session.scalars(
        select(Listing.fetched_date)
        .where(
            Listing.tracked_item_id == tracked_item_id,
            Listing.fetched_date.between(
                target - timedelta(days=_TREND_TOLERANCE_DAYS),
                target + timedelta(days=_TREND_TOLERANCE_DAYS),
            ),
        )
        .distinct()
    ).all()
    for day in sorted(stored_days, key=lambda d: (abs((d - target).days), d)):
        fair = _fair_price_on(session, tracked_item_id, tier, day)
        if fair is not None:
            return day, fair
    return None, None
