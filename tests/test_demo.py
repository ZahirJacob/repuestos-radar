"""The public demo's sample data: deterministic, complete, ends today."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from repuestos_radar.analysis import analyze_item, latest_day, listings_for_day
from repuestos_radar.dashboard import demo
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, ServicePrice, TrackedItem
from repuestos_radar.quality import label_tier
from repuestos_radar.sources import load_sources

TODAY = date(2026, 9, 4)


@pytest.fixture
def seeded(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path}/demo.sqlite")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        demo.seed(session, TODAY)
    return engine, factory


def test_seed_is_deterministic(tmp_path):
    def snapshot(name: str) -> list[tuple]:
        engine = get_engine(f"sqlite:///{tmp_path}/{name}.sqlite")
        init_db(engine)
        with get_session_factory(engine)() as session:
            demo.seed(session, TODAY)
            rows = session.execute(
                select(
                    Listing.source_slug, Listing.title, Listing.price, Listing.fetched_date
                ).order_by(Listing.id)
            ).all()
        return [tuple(r) for r in rows]

    assert snapshot("a") == snapshot("b")


def test_seed_covers_every_item_with_thirty_days_ending_today(seeded):
    _, factory = seeded
    with factory() as session:
        items = session.scalars(select(TrackedItem)).all()
        assert [i.query for i in items] == [q for q, _, _, _ in demo._ITEMS]
        assert {i.kind for i in items} == {"part", "phone"}
        assert session.scalar(select(func.max(Listing.fetched_date))) == TODAY
        assert session.scalar(select(func.min(Listing.fetched_date))) == TODAY - timedelta(
            days=demo.DAYS - 1
        )
        for item in items:
            assert latest_day(session, item.id) == TODAY  # nothing is missing on the last day
        assert session.scalar(select(func.count()).select_from(ServicePrice)) == len(demo._SERVICES)


def test_seed_uses_only_registered_stores_and_labeled_tiers(seeded):
    _, factory = seeded
    urls = {source.slug: source.url for source in load_sources()}
    with factory() as session:
        for listing in session.scalars(select(Listing)):
            assert listing.url == urls[listing.source_slug]  # links go to the real store
        for item in session.scalars(select(TrackedItem)):
            analyses = analyze_item(listings_for_day(session, item.id, TODAY))
            bases = next(b for q, _, b, _ in demo._ITEMS if q == item.query)
            assert {a.tier for a in analyses} == set(bases)
            for listing in listings_for_day(session, item.id, TODAY):
                assert label_tier(listing.title) in bases


def test_seed_shows_one_outlier_and_one_low_confidence_offer(seeded):
    _, factory = seeded
    with factory() as session:
        a32 = session.scalar(select(TrackedItem).where(TrackedItem.query == "modulo samsung a32"))
        analyses = {a.tier: a for a in analyze_item(listings_for_day(session, a32.id, TODAY))}
        outliers = [o for o in analyses["incell"].offers if o.outlier]
        assert [o.source_slug for o in outliers] == ["tienda-movil"]
        low = [o for o in analyses["oled"].offers if o.relevance == "low_confidence"]
        assert [o.source_slug for o in low] == ["evophone"]


def test_refresh_reseeds_only_when_the_newest_day_is_not_today(seeded):
    engine, factory = seeded
    assert demo.refresh_if_stale(engine, TODAY) is False
    assert demo.refresh_if_stale(engine, TODAY + timedelta(days=1)) is True
    with factory() as session:
        assert session.scalar(select(func.max(Listing.fetched_date))) == TODAY + timedelta(days=1)
        assert session.scalar(select(func.min(Listing.fetched_date))) == TODAY + timedelta(
            days=2 - demo.DAYS
        )


def test_database_url_is_a_private_sqlite_file_not_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://neon.example/prod")
    monkeypatch.setattr(demo, "_db_dir", None)
    url = demo.database_url()
    assert url.startswith("sqlite:///") and url.endswith("demo.sqlite")
    assert demo.database_url() == url  # one file per process


def test_configure_environment_sets_the_flag_and_a_public_shop_position(monkeypatch):
    monkeypatch.delenv(demo.DEMO_ENV, raising=False)
    monkeypatch.setenv("SHOP_LAT", "-1")
    monkeypatch.delenv("SHOP_LON", raising=False)
    assert demo.is_demo() is False
    demo.configure_environment()
    assert demo.is_demo() is True
    assert (demo.DEMO_SHOP_LAT, demo.DEMO_SHOP_LON) != ("-1", "x")
    import os

    assert os.environ["SHOP_LAT"] == "-1"  # an explicit value is kept
    assert os.environ["SHOP_LON"] == demo.DEMO_SHOP_LON
