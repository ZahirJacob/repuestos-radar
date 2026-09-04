"""The public demo: no password, generated sample data, read-only settings.

Switched on by the ``REPUESTOS_RADAR_DEMO`` environment variable, which the
``demo_app.py`` entry point sets before the app runs, so a second Streamlit
Cloud app pointed at that file needs no secrets at all. In demo mode the
dashboard never reads ``DATABASE_URL``: it works on a throw-away SQLite file
seeded from :func:`seed` with thirty days of made-up prices for the real
stores of the registry (their names and distances are real; the prices are
not, and the banner says so). The sample is deterministic — same numbers on
every start — and ends on the current day, so the demo never looks stale;
when the process outlives the day it is reseeded on the next request.
"""

import os
import random
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import (
    KIND_PART,
    KIND_PHONE,
    Listing,
    QuickSearchRun,
    ServicePrice,
    TrackedItem,
)
from repuestos_radar.relevance import Relevance

DEMO_ENV = "REPUESTOS_RADAR_DEMO"
DAYS = 30
SEED = 20260904

# A public spot in central Rosario (Monumento a la Bandera) stands in for the
# shop, whose real position stays out of the public repo.
DEMO_SHOP_LAT = "-32.9476"
DEMO_SHOP_LON = "-60.6304"

# (query, kind, {tier: base price}, title template per tier)
_ITEMS: tuple[tuple[str, str, dict[str, int], dict[str, str]], ...] = (
    (
        "modulo samsung a32",
        KIND_PART,
        {"incell": 21000, "oled": 44000, "original": 58000},
        {
            "incell": "Modulo Samsung A32 4G Incell con marco",
            "oled": "Modulo Samsung A32 4G Oled con marco",
            "original": "Modulo Samsung A32 4G Original con marco",
        },
    ),
    (
        "modulo motorola g60",
        KIND_PART,
        {"incell": 15000, "original": 19800},
        {
            "incell": "Modulo Motorola Moto G60 Incell sin marco",
            "original": "Modulo Motorola Moto G60 / G60s Original",
        },
    ),
    (
        "bateria iphone 11",
        KIND_PART,
        {"unlabeled": 15500},
        {"unlabeled": "Bateria iPhone 11"},
    ),
    (
        "placa carga samsung a15",
        KIND_PART,
        {"unlabeled": 4200, "original": 12600},
        {
            "unlabeled": "Placa de carga Samsung A15 A155 generica",
            "original": "Placa de carga Samsung A15 A155 Original",
        },
    ),
    (
        "moto g35",
        KIND_PHONE,
        {"reacondicionado": 150000, "nuevo": 215000},
        {
            "reacondicionado": "Motorola Moto G35 128GB Reacondicionado",
            "nuevo": "Motorola Moto G35 256GB Nuevo sellado",
        },
    ),
)

# Which stores carry each tier, with a fixed price factor per store: a tier
# needs at least four stores for the outlier rule to fire on one of them.
_STORES: dict[str, tuple[tuple[str, float], ...]] = {
    "modulo samsung a32|incell": (
        ("celuphone", 0.97),
        ("novocell", 1.06),
        ("evophone", 1.02),
        ("tienda-movil", 1.10),
    ),
    "modulo samsung a32|oled": (("celuphone", 0.95), ("novocell", 1.08), ("evophone", 1.03)),
    "modulo samsung a32|original": (("novocell", 1.0), ("evophone", 0.94)),
    "modulo motorola g60|incell": (("mdrepuestos", 1.0), ("celuphone", 1.07)),
    "modulo motorola g60|original": (
        ("celuphone", 1.0),
        ("novocell", 1.05),
        ("mdrepuestos", 0.96),
        ("tienda-movil", 1.12),
    ),
    "bateria iphone 11|unlabeled": (
        ("celuphone", 0.9),
        ("novocell", 1.15),
        ("tienda-movil", 1.0),
        ("mdrepuestos", 1.05),
    ),
    "placa carga samsung a15|unlabeled": (("novocell", 1.0), ("celuphone", 0.9)),
    "placa carga samsung a15|original": (("novocell", 1.0), ("mdrepuestos", 1.1)),
    "moto g35|reacondicionado": (("gofix", 1.0),),
    "moto g35|nuevo": (("gofix", 1.0), ("celuphone", 1.04)),
}

# One deliberate outlier and one low-confidence title, so the demo shows the
# warnings the client sees on real data.
_OUTLIER = ("modulo samsung a32", "incell", "tienda-movil")  # ~2.4x the tier on the last day
_LOW_CONFIDENCE = ("modulo samsung a32", "oled", "evophone", "Modulo Samsung A32 5G Oled con marco")

_SERVICES: tuple[tuple[str, str, int], ...] = (
    ("Cambio módulo A32", "modulo samsung a32", 85000),
    ("Cambio módulo Moto G60", "modulo motorola g60", 45000),
    ("Cambio batería iPhone 11", "bateria iphone 11", 35000),
    ("Cambio placa de carga A15", "placa carga samsung a15", 18000),
)


def is_demo() -> bool:
    return bool(os.environ.get(DEMO_ENV))


def configure_environment() -> None:
    """Set the demo flag and a public stand-in for the shop position (only
    where nothing is configured), for the ``demo_app.py`` entry point."""
    os.environ[DEMO_ENV] = "1"
    os.environ.setdefault("SHOP_LAT", DEMO_SHOP_LAT)
    os.environ.setdefault("SHOP_LON", DEMO_SHOP_LON)


_db_dir: Path | None = None


def database_url() -> str:
    """A throw-away SQLite file for this process; never ``DATABASE_URL``."""
    global _db_dir
    if _db_dir is None:
        _db_dir = Path(tempfile.mkdtemp(prefix="repuestos-radar-demo-"))
    return f"sqlite:///{_db_dir / 'demo.sqlite'}"


def engine(today: date | None = None) -> Engine:
    """The demo engine, tables created and seeded through ``today``."""
    demo_engine = get_engine(database_url())
    init_db(demo_engine)
    refresh_if_stale(demo_engine, today or date.today())
    return demo_engine


def _price(base: int, factor: float, walk: float) -> Decimal:
    return Decimal(round(base * factor * walk / 100) * 100)


def seed(session: Session, today: date) -> None:
    """Replace everything with the sample: items, DAYS days of listings,
    repair prices. Deterministic for a given ``today``."""
    for table in (QuickSearchRun, ServicePrice, Listing, TrackedItem):
        session.execute(delete(table))
    rng = random.Random(SEED)
    items: dict[str, TrackedItem] = {}
    for query, kind, _, _ in _ITEMS:
        item = TrackedItem(query=query, kind=kind)
        session.add(item)
        items[query] = item
    session.flush()

    first_day = today - timedelta(days=DAYS - 1)
    for query, _, bases, titles in _ITEMS:
        for tier, base in bases.items():
            for slug, factor in _STORES[f"{query}|{tier}"]:
                walk = 1.0
                title = titles[tier]
                relevance = Relevance.MATCH.value
                if (query, tier, slug, title) == _LOW_CONFIDENCE or (
                    query,
                    tier,
                    slug,
                ) == _LOW_CONFIDENCE[:3]:
                    title = _LOW_CONFIDENCE[3]
                    relevance = Relevance.LOW_CONFIDENCE.value
                for offset in range(DAYS):
                    day = first_day + timedelta(days=offset)
                    walk *= 1 + rng.uniform(-0.015, 0.02)
                    if rng.random() < 0.08 and day != today:
                        continue  # a store missing a day, like real crawls
                    factor_today = factor
                    if (query, tier, slug) == _OUTLIER and day == today:
                        factor_today = 2.4
                    session.add(
                        Listing(
                            tracked_item_id=items[query].id,
                            source_slug=slug,
                            external_id=f"demo-{query}-{tier}-{slug}",
                            title=title,
                            price=_price(base, factor_today, walk),
                            currency="ARS",
                            condition="new",
                            url="https://example.com/demo",
                            fetched_date=day,
                            relevance=relevance,
                            relevance_score=1.0 if relevance == "match" else 0.7,
                        )
                    )
    for label, query, price in _SERVICES:
        session.add(
            ServicePrice(tracked_item_id=items[query].id, label=label, price_ars=Decimal(price))
        )
    session.commit()


def refresh_if_stale(demo_engine: Engine, today: date) -> bool:
    """Seed when empty or when the newest stored day is not ``today``.

    Returns True when a seed ran. One cheap query otherwise, so it can run
    on every request.
    """
    with get_session_factory(demo_engine)() as session:
        newest = session.scalar(select(func.max(Listing.fetched_date)))
        if newest == today:
            return False
        seed(session, today)
        return True
