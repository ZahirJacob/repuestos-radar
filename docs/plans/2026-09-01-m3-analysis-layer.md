# M3 Analysis Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn stored daily listings into best-place-to-buy, fair price, outlier flags, margins, and trends — plus an internal Spanish daily report CLI.

**Architecture:** Pure compute-on-demand functions over the existing `listings`/`tracked_items` tables (no precomputed stats, no HTTP anywhere). Quality tiers are computed from titles at analysis time, never stored. One schema addition: the `service_prices` table. `analysis.py` returns dataclasses; `report.py` owns all Spanish text; the M4 dashboard will import the same functions.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (declarative, `Session`), `statistics.median` over `Decimal`, argparse CLIs, pytest, ruff.

**Spec:** `docs/specs/2026-09-01-m3-analysis-layer-design.md` — read it first; this plan implements it PR by PR.

## Global Constraints

- Four PRs, in order: `feat/quality-tiers`, `feat/analysis-core`, `feat/service-prices`, `feat/history-report`. Each opened via `gh pr create`, NEVER merged by the implementer — Zahir merges.
- Conventional commits; **NO AI attribution anywhere** (no "Generated with", no Co-Authored-By trailers) — verify with `git log` before pushing.
- TDD: every step pair below is write-failing-test → implement. Run `uv run pytest -q` and `uv run ruff check` + `uv run ruff format --check` before every commit; full suite green before each PR (baseline: 215 tests).
- Code/comments/commits/PR text in English. Spanish appears ONLY in `report.py` output strings (Mo reviews those in PR 4).
- No network access in any test or any M3 module. DB tests use `get_engine("sqlite:///:memory:")` + `init_db`.
- Reuse `normalize()` from `repuestos_radar.relevance` for all title matching — never write a second normalizer.
- Prices are `Decimal` end to end; never floats.

## File Structure

```
src/repuestos_radar/quality.py    # NEW PR1: tier labeling (pure functions)
src/repuestos_radar/analysis.py   # NEW PR2: offers/fair price/outliers; PR4 adds trends
src/repuestos_radar/models.py     # PR3: add ServicePrice model
src/repuestos_radar/margin.py     # NEW PR3: margin math
src/repuestos_radar/services.py   # NEW PR3: price-list CLI (__main__ via python -m)
src/repuestos_radar/report.py     # NEW PR4: Spanish rendering + __main__
tests/test_quality.py             # NEW PR1
tests/test_analysis.py            # NEW PR2 (+PR4 trend tests)
tests/test_margin.py              # NEW PR3 (model + math + CLI)
tests/test_report.py              # NEW PR4
docs/specs/2026-09-01-m3-analysis-layer-design.md  # already written locally; committed in PR1
```

---

## PR 1 — Quality tiers (`feat/quality-tiers`)

### Task 1: Part-tier labeling

**Files:**
- Create: `src/repuestos_radar/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `normalize(text: str) -> str` from `repuestos_radar.relevance`.
- Produces: constants `TIER_INCELL = "incell"`, `TIER_OLED = "oled"`, `TIER_ORIGINAL = "original"`, `TIER_UNLABELED = "unlabeled"`, `PART_TIER_ORDER = (TIER_INCELL, TIER_OLED, TIER_ORIGINAL)`; function `label_part_tier(title: str) -> str`. Later tasks import these exact names.

- [ ] **Step 1: Branch off fresh main**

```bash
git checkout main && git pull && git checkout -b feat/quality-tiers
```

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for title-based quality-tier labeling."""

from repuestos_radar.quality import (
    TIER_INCELL,
    TIER_OLED,
    TIER_ORIGINAL,
    TIER_UNLABELED,
    label_part_tier,
)


def test_original_signals():
    assert label_part_tier("Modulo Samsung A32 Original") == TIER_ORIGINAL
    assert label_part_tier("Pantalla iPhone 11 Service Pack") == TIER_ORIGINAL


def test_oled_signals_including_amoled():
    assert label_part_tier("Módulo A32 OLED con marco") == TIER_OLED
    assert label_part_tier("Pantalla AMOLED Samsung A54") == TIER_OLED


def test_incell_signals_with_punctuation_variants():
    assert label_part_tier("Modulo A32 Incell") == TIER_INCELL
    assert label_part_tier("Pantalla IN-CELL calidad") == TIER_INCELL
    assert label_part_tier("Display TFT A32") == TIER_INCELL


def test_no_signal_is_unlabeled():
    assert label_part_tier("Modulo Samsung A32 4G") == TIER_UNLABELED


def test_conflict_humbler_tier_wins():
    # Sellers oversell: "OLED calidad original" is an OLED, not an original.
    assert label_part_tier("Pantalla OLED calidad original A32") == TIER_OLED
    assert label_part_tier("Modulo incell tipo original") == TIER_INCELL


def test_word_boundaries_no_substring_leaks():
    # "amoled" must not match via its "oled" substring twice, and unrelated
    # words containing signal letters must not match at all.
    assert label_part_tier("Funda originalidad dudosa") == TIER_UNLABELED
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'repuestos_radar.quality'`

- [ ] **Step 4: Implement `quality.py`**

```python
"""Quality-tier labeling from listing titles.

Tiers are computed at analysis time and never stored: tuning the signal
lists below relabels all history for free. Same philosophy as the relevance
filter — visible word lists, so "why did this get this label?" is always
answerable.
"""

from repuestos_radar.relevance import normalize

TIER_INCELL = "incell"
TIER_OLED = "oled"
TIER_ORIGINAL = "original"
TIER_UNLABELED = "unlabeled"

# Humbler tier first. On a title matching two tiers the humbler one wins:
# sellers oversell ("OLED calidad original"), so trust the humbler word.
PART_TIER_ORDER = (TIER_INCELL, TIER_OLED, TIER_ORIGINAL)

# Signals are written in normalized form (see relevance.normalize): "in-cell"
# normalizes to "in cell", so the space form covers the hyphen form too.
_PART_TIER_SIGNALS: dict[str, tuple[str, ...]] = {
    TIER_INCELL: ("incell", "in cell", "tft"),
    TIER_OLED: ("oled", "amoled"),
    TIER_ORIGINAL: ("original", "service pack", "genuine"),
}


def _has_signal(normalized_title: str, signal: str) -> bool:
    # Whole-word/phrase containment: keeps "amoled" from matching " oled "
    # and "originalidad" from matching " original ".
    return f" {signal} " in f" {normalized_title} "


def label_part_tier(title: str) -> str:
    """One tier per title; humbler tier wins conflicts; no signal = unlabeled."""
    normalized = normalize(title)
    for tier in PART_TIER_ORDER:
        if any(_has_signal(normalized, signal) for signal in _PART_TIER_SIGNALS[tier]):
            return tier
    return TIER_UNLABELED
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_quality.py -q`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/repuestos_radar/quality.py tests/test_quality.py
git commit -m "feat: part quality tiers from listing titles"
```

### Task 2: Frame detail, device condition, and the combined labeler

**Files:**
- Modify: `src/repuestos_radar/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces: `FRAME_WITH = "with"`, `FRAME_WITHOUT = "without"`, `FRAME_UNKNOWN = "unknown"`, `label_frame(title: str) -> str`; `DEVICE_NEW = "nuevo"`, `DEVICE_REFURBISHED = "reacondicionado"`, `label_device_condition(title: str) -> str` (returns `TIER_UNLABELED` when no signal); `label_tier(title: str) -> str` — the one function analysis uses: part tier if any part signal, else device condition, else `TIER_UNLABELED`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_quality.py`)

```python
from repuestos_radar.quality import (
    DEVICE_NEW,
    DEVICE_REFURBISHED,
    FRAME_UNKNOWN,
    FRAME_WITH,
    FRAME_WITHOUT,
    label_device_condition,
    label_frame,
    label_tier,
)


def test_frame_detail():
    assert label_frame("Modulo A32 OLED con marco") == FRAME_WITH
    assert label_frame("Modulo A32 sin marco negro") == FRAME_WITHOUT
    assert label_frame("Modulo A32 OLED") == FRAME_UNKNOWN


def test_device_condition():
    assert label_device_condition("Samsung S24 Ultra Reacondicionado") == DEVICE_REFURBISHED
    assert label_device_condition("Moto G35 usado impecable") == DEVICE_REFURBISHED
    assert label_device_condition("iPhone 13 nuevo caja sellada") == DEVICE_NEW
    assert label_device_condition("Moto G17 256GB") == TIER_UNLABELED


def test_device_condition_conflict_refurbished_wins():
    assert label_device_condition("Moto G35 usado como nuevo") == DEVICE_REFURBISHED


def test_label_tier_prefers_part_signals_then_device():
    assert label_tier("Pantalla OLED A32 con marco") == TIER_OLED
    assert label_tier("Moto G35 reacondicionado") == DEVICE_REFURBISHED
    assert label_tier("Modulo Samsung A32") == TIER_UNLABELED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quality.py -q`
Expected: FAIL — `ImportError: cannot import name 'label_frame'`

- [ ] **Step 3: Implement** (append to `quality.py`)

```python
FRAME_WITH = "with"
FRAME_WITHOUT = "without"
FRAME_UNKNOWN = "unknown"

DEVICE_NEW = "nuevo"
DEVICE_REFURBISHED = "reacondicionado"

# Refurbished wins a conflict for the same oversell reason as part tiers.
_DEVICE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DEVICE_REFURBISHED, ("reacondicionado", "refurbished", "usado")),
    (DEVICE_NEW, ("nuevo", "sellado", "caja sellada")),
)


def label_frame(title: str) -> str:
    """Frame detail is a price modifier within a tier, not a tier itself."""
    normalized = normalize(title)
    if _has_signal(normalized, "con marco"):
        return FRAME_WITH
    if _has_signal(normalized, "sin marco"):
        return FRAME_WITHOUT
    return FRAME_UNKNOWN


def label_device_condition(title: str) -> str:
    """nuevo / reacondicionado for whole devices; title signals only."""
    normalized = normalize(title)
    for condition, signals in _DEVICE_SIGNALS:
        if any(_has_signal(normalized, signal) for signal in signals):
            return condition
    return TIER_UNLABELED


def label_tier(title: str) -> str:
    """The labeler analysis uses: part tiers first, then device condition.

    Part and device tiers never mix in practice — a title either names a part
    quality or a device condition — so checking part signals first simply
    resolves the rare ambiguous title toward the parts domain.
    """
    part = label_part_tier(title)
    if part != TIER_UNLABELED:
        return part
    return label_device_condition(title)
```

- [ ] **Step 4: Run tests, then the full suite**

Run: `uv run pytest tests/test_quality.py -q` → 10 passed; `uv run pytest -q` → all pass (215 + 10).

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/quality.py tests/test_quality.py
git commit -m "feat: frame detail, device condition, combined tier labeler"
```

### Task 3: Commit the spec and open PR 1

- [ ] **Step 1: Add the spec doc** (already written at `docs/specs/2026-09-01-m3-analysis-layer-design.md`)

```bash
git add docs/specs/2026-09-01-m3-analysis-layer-design.md docs/plans/2026-09-01-m3-analysis-layer.md
git commit -m "docs: M3 analysis layer spec and implementation plan"
```

- [ ] **Step 2: Lint, full suite, push, open PR**

```bash
uv run ruff check && uv run ruff format --check && uv run pytest -q
git push -u origin feat/quality-tiers
gh pr create --title "feat: quality-tier labeling from listing titles" --body "<summary: tiers, conflict rule, frame/device labels, spec+plan docs. No AI attribution.>"
```

Expected: CI green. Do not merge.

---

## PR 2 — Analysis core (`feat/analysis-core`), branched from PR 1's branch (rebase onto main once PR 1 merges)

### Task 4: Per-store cheapest offers grouped by tier

**Files:**
- Create: `src/repuestos_radar/analysis.py`
- Test: `tests/test_analysis.py`

**Interfaces:**
- Consumes: `label_tier(title) -> str` and tier constants from `repuestos_radar.quality`.
- Produces:
  - `StoreOffer` frozen dataclass: `source_slug: str, title: str, price: Decimal, url: str, relevance: str, tier: str, outlier: bool = False`.
  - `TierAnalysis` frozen dataclass: `tier: str, offers: tuple[StoreOffer, ...]` (cheapest-first, one per store, outliers included and flagged), `fair_price: Decimal | None, price_min: Decimal | None, price_max: Decimal | None, store_count: int, basis: str`.
  - Constants `BASIS_MEDIAN = "median"`, `BASIS_SINGLE_STORE = "single-store"`, `MIN_STORES_FOR_OUTLIERS = 4`, `TIER_DISPLAY_ORDER = (TIER_ORIGINAL, TIER_OLED, TIER_INCELL, DEVICE_NEW, DEVICE_REFURBISHED, TIER_UNLABELED)`.
  - `analyze_item(listings) -> list[TierAnalysis]` — accepts any objects with `.title .price .source_slug .url .relevance` (ORM `Listing` rows or test fakes), already filtered to `match`/`low_confidence`.

Tests use this fake row helper at the top of `tests/test_analysis.py`:

```python
from dataclasses import dataclass
from decimal import Decimal


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
```

- [ ] **Step 1: Write the failing tests**

```python
from repuestos_radar.analysis import BASIS_MEDIAN, BASIS_SINGLE_STORE, analyze_item
from repuestos_radar.quality import TIER_OLED, TIER_UNLABELED


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analysis.py -q`
Expected: FAIL — no module `repuestos_radar.analysis`

- [ ] **Step 3: Implement `analysis.py`**

```python
"""Compute-on-demand analysis over stored listings.

Pure functions: rows in, dataclasses out. No printing (report.py owns all
Spanish text), no HTTP, nothing precomputed or stored. The M4 dashboard
imports these same functions.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from statistics import median

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analysis.py -q` → 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/analysis.py tests/test_analysis.py
git commit -m "feat: per-store offers, tier grouping, and median fair price"
```

### Task 5: Outlier behavior

**Files:**
- Modify: `src/repuestos_radar/analysis.py` (already implemented in Task 4 — this task pins the behavior with tests; adjust implementation only if a test fails)
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the tests**

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_analysis.py -q`
Expected: 7 passed (Task 4's implementation covers these; if either fails, fix `_flag_outliers` until green).

- [ ] **Step 3: Commit**

```bash
git add tests/test_analysis.py
git commit -m "test: outlier exclusion and small-group guard"
```

### Task 6: DB helpers — latest day and a day's relevant listings

**Files:**
- Modify: `src/repuestos_radar/analysis.py`
- Test: `tests/test_analysis.py`

**Interfaces:**
- Consumes: `Listing`, `TrackedItem` from `repuestos_radar.models`; `get_engine`, `get_session_factory`, `init_db` from `repuestos_radar.db`.
- Produces: `latest_day(session, tracked_item_id: int) -> date | None`; `listings_for_day(session, tracked_item_id: int, day: date) -> list[Listing]` (relevance filtered to `match`/`low_confidence`, i.e. excludes `reject` and NULL).

- [ ] **Step 1: Write the failing tests** (append; fixture included)

```python
from datetime import date

import pytest

from repuestos_radar.analysis import latest_day, listings_for_day
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, TrackedItem


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analysis.py -q`
Expected: FAIL — `ImportError: cannot import name 'latest_day'`

- [ ] **Step 3: Implement** (append to `analysis.py`; add imports `from datetime import date`, `from sqlalchemy import func, select`, `from sqlalchemy.orm import Session`, `from repuestos_radar.models import Listing`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analysis.py -q` → 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/analysis.py tests/test_analysis.py
git commit -m "feat: latest-day and per-day relevant-listing queries"
```

### Task 7: Real-data A32 sanity case, then open PR 2

- [ ] **Step 1: Write the end-to-end fixture test** (append to `tests/test_analysis.py`; values from the real 2026-08-31 spread)

```python
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
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_analysis.py -q` → 10 passed. If the display-order assert fails, fix the ordering in `analyze_item` (it must follow `TIER_DISPLAY_ORDER`).

- [ ] **Step 3: Full suite + lint, commit, push, open PR**

```bash
uv run pytest -q && uv run ruff check && uv run ruff format --check
git add tests/test_analysis.py
git commit -m "test: A32 two-tier real-data sanity case"
git push -u origin feat/analysis-core
gh pr create --title "feat: analysis core — best place, fair price, outliers" --body "<summary. No AI attribution.>"
```

---

## PR 3 — Service prices + margin (`feat/service-prices`)

### Task 8: `ServicePrice` model

**Files:**
- Modify: `src/repuestos_radar/models.py`
- Test: `tests/test_margin.py`

**Interfaces:**
- Produces: `ServicePrice` ORM model — `id: int`, `tracked_item_id: int` (FK `tracked_items.id`), `label: str` (Text, unique, non-empty check), `price_ars: Decimal` (Numeric(12, 2), positive check), `updated_at: datetime` (tz-aware, default + onupdate `_utcnow`). Table `service_prices`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the service price list and margin math."""

from datetime import date
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_margin.py -q`
Expected: FAIL — `ImportError: cannot import name 'ServicePrice'`

- [ ] **Step 3: Implement** (append to `models.py`)

```python
class ServicePrice(Base):
    """What Activcelu charges the customer for one repair.

    Linked to the tracked item whose part the repair consumes, so margin =
    this price minus that part's best price. Managed by the services CLI now,
    by the dashboard admin page in M4.
    """

    __tablename__ = "service_prices"
    __table_args__ = (
        CheckConstraint("length(trim(label)) > 0", name="ck_service_prices_label"),
        CheckConstraint("price_ars > 0", name="ck_service_prices_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(ForeignKey("tracked_items.id"))
    label: Mapped[str] = mapped_column(Text, unique=True)
    price_ars: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    tracked_item: Mapped[TrackedItem] = relationship()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_margin.py -q` → 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/models.py tests/test_margin.py
git commit -m "feat: service_prices table for Activcelu repair prices"
```

### Task 9: Margin math

**Files:**
- Create: `src/repuestos_radar/margin.py`
- Test: `tests/test_margin.py`

**Interfaces:**
- Consumes: `TierAnalysis`, `StoreOffer` from `repuestos_radar.analysis`.
- Produces: `TierMargin` frozen dataclass (`tier: str, part_price: Decimal, part_source: str, margin: Decimal`); `margins_for(service_price: Decimal, analyses: Sequence[TierAnalysis]) -> list[TierMargin]` — per tier, part cost = cheapest non-outlier offer; tiers whose offers are all outliers are skipped; preserves the analyses' tier order.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_margin.py`)

```python
from dataclasses import dataclass

from repuestos_radar.analysis import analyze_item
from repuestos_radar.margin import margins_for


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_margin.py -q`
Expected: FAIL — no module `repuestos_radar.margin`

- [ ] **Step 3: Implement `margin.py`**

```python
"""Margin math: what a repair earns given today's part prices.

Margin is naturally tier-aware — "con incell ganás X, con OLED Y" — because
the honest answer depends on which quality the customer is quoted.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from repuestos_radar.analysis import TierAnalysis


@dataclass(frozen=True, slots=True)
class TierMargin:
    tier: str
    part_price: Decimal
    part_source: str
    margin: Decimal


def margins_for(service_price: Decimal, analyses: Sequence[TierAnalysis]) -> list[TierMargin]:
    """One margin per tier: service price minus the cheapest trustworthy part.

    Outlier-flagged offers are never the basis of a margin; a tier whose
    offers are all outliers is skipped rather than shown with a suspect
    number. Tier order follows the input analyses (display order).
    """
    margins = []
    for analysis in analyses:
        best = next((offer for offer in analysis.offers if not offer.outlier), None)
        if best is None:
            continue
        margins.append(
            TierMargin(
                tier=analysis.tier,
                part_price=best.price,
                part_source=best.source_slug,
                margin=service_price - best.price,
            )
        )
    return margins
```

Note: remove the stray non-ASCII character if any linter complains about the docstring; the docstring must read "which quality the customer is quoted".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_margin.py -q` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/margin.py tests/test_margin.py
git commit -m "feat: tier-aware margin math over analysis results"
```

### Task 10: Services CLI, then open PR 3

**Files:**
- Create: `src/repuestos_radar/services.py`
- Test: `tests/test_margin.py`

**Interfaces:**
- Produces: `python -m repuestos_radar.services` with subcommands `add LABEL --item ITEM_ID --price PRICE`, `list`, `set-price ID PRICE`, `remove ID`. Session-level functions mirror `tracked.py`: `add_service(session, label, tracked_item_id, price) -> tuple[ServicePrice, str]` (statuses `ADDED`/`UPDATED` — re-adding an existing label updates its price), `list_services(session) -> list[ServicePrice]`, `set_price(session, service_id, price) -> tuple[ServicePrice | None, str]` (`CHANGED`/`UNCHANGED`/`NOT_FOUND`), `remove_service(session, service_id) -> str` (`REMOVED`/`NOT_FOUND`). Follow `tracked.py`'s structure exactly: same status-constant style, same key=value output lines, same `main() -> int` shape, same DATABASE_URL/init_db startup contract, exit 0 on success / 1 on bad input.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_margin.py`)

```python
from repuestos_radar.services import ADDED, UPDATED, add_service, list_services, remove_service


def test_add_service_and_update_on_same_label(session, item):
    service, status = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    assert status == ADDED
    service, status = add_service(session, "Cambio módulo A32", item.id, Decimal("80000"))
    assert status == UPDATED
    assert service.price_ars == Decimal("80000")
    assert len(list_services(session)) == 1


def test_remove_service(session, item):
    service, _ = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    session.flush()
    assert remove_service(session, service.id) == "removed"
    assert list_services(session) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_margin.py -q`
Expected: FAIL — no module `repuestos_radar.services`

- [ ] **Step 3: Implement `services.py`** — read `src/repuestos_radar/tracked.py` first and mirror it: module docstring explaining it is the dev-facing price-list CLI until M4's admin page; status constants `ADDED = "added"`, `UPDATED = "updated"`, `CHANGED = "changed"`, `UNCHANGED = "unchanged"`, `NOT_FOUND = "not-found"`, `REMOVED = "removed"`; the four session-level functions above (add validates the tracked item exists and the price is positive, returning a clear error via `SystemExit`-free status handling as `tracked.py` does); an argparse `main() -> int` wiring `add/list/set-price/remove` with the same engine/session startup and single-line key=value prints. `remove` deletes the row (`session.delete`) — unlike tracked items, a price-list entry has no history worth keeping.

- [ ] **Step 4: Run the tests and full suite**

Run: `uv run pytest tests/test_margin.py -q` → 6 passed; `uv run pytest -q` → all green.

- [ ] **Step 5: Commit, push, open PR**

```bash
git add src/repuestos_radar/services.py tests/test_margin.py
git commit -m "feat: service price-list CLI"
uv run ruff check && uv run ruff format --check
git push -u origin feat/service-prices
gh pr create --title "feat: service prices and tier-aware margins" --body "<summary. No AI attribution.>"
```

---

## PR 4 — History + report (`feat/history-report`)

### Task 11: Trends

**Files:**
- Modify: `src/repuestos_radar/analysis.py`
- Test: `tests/test_analysis.py`

**Interfaces:**
- Produces: `TrendPoint` frozen dataclass (`days_back: int, compared_date: date | None, direction: str, pct_change: Decimal | None`) — `direction` is `"↑"`, `"↓"`, `"="` (absolute change under 1%), or `""` when no comparable data; `tier_trends(session, tracked_item_id: int, tier: str, today: date) -> list[TrendPoint]` returning points for 7 and 30 days back, comparing today's fair price with the fair price on the nearest stored day within ±2 days of the target (skip = empty direction when none, when either day lacks a fair price, or when the tier is missing on either day).

- [ ] **Step 1: Write the failing test** (append to `tests/test_analysis.py`)

```python
from repuestos_radar.analysis import tier_trends


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis.py -q`
Expected: FAIL — `ImportError: cannot import name 'tier_trends'`

- [ ] **Step 3: Implement** (append to `analysis.py`; add `from datetime import timedelta`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analysis.py -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/analysis.py tests/test_analysis.py
git commit -m "feat: 7/30-day fair-price trends with missing-day tolerance"
```

### Task 12: Argentine price formatting and the Spanish report

**Files:**
- Create: `src/repuestos_radar/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: everything public from `analysis`, `margin`, `quality`; `Source` registry via `repuestos_radar.sources.load_sources` for slug → display-name mapping (check the actual loader name in `sources.py` before writing; use what exists).
- Produces: `format_ars(value: Decimal) -> str` (`Decimal("20700")` → `"$20.700"`, rounded to whole pesos, `.` thousands separator); `render_report(session, today: date | None = None) -> str` (full Spanish report, one section per active tracked item); `main() -> int` + `if __name__ == "__main__":` block so `python -m repuestos_radar.report` works.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Spanish daily report."""

from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem
from repuestos_radar.report import format_ars, render_report


@pytest.fixture()
def session():
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    with get_session_factory(engine)() as session:
        yield session


# Local copy of the listing helper (tests never import from sibling test files).
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


def test_format_ars_argentine_style():
    assert format_ars(Decimal("20700")) == "$20.700"
    assert format_ars(Decimal("1449999.50")) == "$1.450.000"
    assert format_ars(Decimal("900")) == "$900"


def test_report_renders_sections_margins_and_warnings(session):
    item = TrackedItem(query="modulo samsung a32")
    session.add(item)
    session.flush()
    today = date(2026, 9, 1)
    _store_listing(session, item.id, "novocell", "45000", today, title="Modulo A32 OLED")
    _store_listing(session, item.id, "celuphone", "41000", today, title="Modulo A32 OLED")
    _store_listing(
        session,
        item.id,
        "gofix",
        "40000",
        today,
        title="Modulo A32 OLED",
        relevance="low_confidence",
    )
    session.add(
        ServicePrice(tracked_item_id=item.id, label="Cambio módulo A32", price_ars=Decimal("75000"))
    )
    session.commit()

    text = render_report(session, today=today)
    assert "modulo samsung a32" in text
    assert "$41.000" in text  # cheapest OLED store price, Argentine format
    assert "revisar" in text  # low-confidence flagged in words
    assert "Cambio módulo A32" in text
    assert "$34.000" in text  # margin with the cheapest OLED
    assert "gofix" not in text.replace("GoFix", "")  # store display names, not slugs


def test_report_says_when_a_day_has_no_data(session):
    session.add(TrackedItem(query="bateria iphone 11"))
    session.commit()
    text = render_report(session, today=date(2026, 9, 1))
    assert "sin datos" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -q`
Expected: FAIL — no module `repuestos_radar.report`

- [ ] **Step 3: Implement `report.py`**

Shape (write real Spanish, made for a non-programmer — Mo reviews it in this PR):

```python
"""Daily Spanish report: the analysis rendered for humans.

Internal team tool (python -m repuestos_radar.report) until the M4 dashboard
exists; it also keeps the analysis honest — everything it prints is exactly
what the dashboard will show. All Spanish copy in the project's analysis
stack lives HERE, never in analysis/margin/quality.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from repuestos_radar.analysis import (
    BASIS_MEDIAN,
    analyze_item,
    latest_day,
    listings_for_day,
    tier_trends,
)
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem

TIER_LABELS_ES = {
    "original": "Original",
    "oled": "OLED",
    "incell": "Incell/TFT",
    "nuevo": "Nuevo",
    "reacondicionado": "Reacondicionado",
    "unlabeled": "Sin calidad indicada",
}


def format_ars(value: Decimal) -> str:
    """Whole pesos, Argentine thousands separator: 20700 -> "$20.700"."""
    whole = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return "$" + f"{whole:,}".replace(",", ".")
```

then `render_report(session, today=None)`:
- Load active tracked items ordered by id; store display names from the sources registry (slug → `Source.name`), falling back to the slug if a listing's source ever leaves the registry.
- Per item: resolve the day (`today` arg or `latest_day`); if `None` or no listings → line `"  sin datos de hoy para <query>"` and continue.
- Per `TierAnalysis` (already display-ordered): header `f"{TIER_LABELS_ES[a.tier]}:"`; winner line `f"  mejor precio: {format_ars(best.price)} en {store_name}"`; fair-price line — when `basis == BASIS_MEDIAN`: `f"  precio justo: {format_ars(a.fair_price)}"` plus, when `a.store_count <= 3`, the range `f" (entre {format_ars(a.price_min)} y {format_ars(a.price_max)}, {a.store_count} tiendas)"`; when single-store: `"  un solo negocio lo vende hoy — sin precio de referencia"`.
- Warnings in words: per outlier offer `f"  ⚠ revisar: {store_name} a {format_ars(offer.price)} — precio muy alejado del resto (posible error, calidad mal etiquetada o una oferta real)"`; per low-confidence offer `f"  ⚠ revisar: coincidencia dudosa en {store_name} — «{offer.title}»"`.
- Margins: for each `ServicePrice` linked to the item, `margins_for(...)` → `f"  {service.label}: ganás {format_ars(m.margin)} usando {TIER_LABELS_ES[m.tier]} de {store_name(m.part_source)}"`.
- Trends: per tier with data, `f"  tendencia: {p.direction} {p.pct_change}% vs hace {p.days_back} días"` only for points with a non-empty direction.
- `main() -> int`: engine/session startup identical to `tracked.py`'s `main`, print `render_report(session)`, return 0; `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -q` → 3 passed. Iterate on wording only until assertions pass; assertions pin substance, not full formatting.

- [ ] **Step 5: Commit**

```bash
git add src/repuestos_radar/report.py tests/test_report.py
git commit -m "feat: Spanish daily report CLI over the analysis layer"
```

### Task 13: README notes, full check, open PR 4

- [ ] **Step 1: Document the new tools** — README.md (EN) and README.es.md (ES): one short subsection covering `python -m repuestos_radar.report` (what the daily summary shows) and `python -m repuestos_radar.services` (managing the repair price list), stating both are internal team tools — the client-facing surface is the M4 dashboard. Match each README's existing structure and tone; keep EN and ES saying the same thing.

- [ ] **Step 2: Full verification**

```bash
uv run pytest -q && uv run ruff check && uv run ruff format --check
```

Expected: all tests green (baseline 215 + ~21 new across the four PRs), lint clean.

- [ ] **Step 3: Commit, push, open PR**

```bash
git add README.md README.es.md
git commit -m "docs: analysis report and service price-list CLIs"
git push -u origin feat/history-report
gh pr create --title "feat: price trends and Spanish daily report" --body "<summary; flag report.py Spanish for Mo. No AI attribution.>"
```

---

## Review flow (applies to every PR)

Lara reviews each PR's code (findings posted as PR comments — our gh account authored the PR, so formal reviews are blocked). Mo reviews user-facing text where present (PR 1: none; PR 4: report.py Spanish + both READMEs; PR 3: CLI output lines). Implementer applies fixes; Zahir merges. Each later PR rebases onto main after the previous merge.
