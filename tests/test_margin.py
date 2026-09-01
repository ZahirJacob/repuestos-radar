"""Tests for the service price list and margin math."""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from repuestos_radar.analysis import analyze_item
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.services import (
    ADDED,
    CHANGED,
    NOT_FOUND,
    UNCHANGED,
    UPDATED,
    add_service,
    list_services,
    main,
    remove_service,
    set_price,
)


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


def test_add_service_and_update_on_same_label(session, item):
    service, status = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    assert status == ADDED
    service, status = add_service(session, "Cambio módulo A32", item.id, Decimal("80000"))
    assert status == UPDATED
    assert service.price_ars == Decimal("80000")
    assert len(list_services(session)) == 1


def test_add_service_relinks_item_on_readd(session, item):
    other = TrackedItem(query="modulo samsung a52")
    session.add(other)
    session.flush()
    add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    service, status = add_service(session, "Cambio módulo A32", other.id, Decimal("80000"))
    assert status == UPDATED
    assert service.tracked_item_id == other.id  # --item is honored, not discarded
    assert service.price_ars == Decimal("80000")


def test_add_service_identical_readd_is_a_no_op(session, item):
    add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    service, status = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    assert status == UNCHANGED
    assert service.price_ars == Decimal("75000")


def test_set_price_changes_and_moves_updated_at(session, item):
    service, _ = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    session.commit()
    first_updated = service.updated_at
    changed, status = set_price(session, service.id, Decimal("80000"))
    assert status == CHANGED
    session.commit()
    assert changed.price_ars == Decimal("80000")
    assert changed.updated_at != first_updated  # onupdate fired


def test_set_price_unchanged_and_not_found(session, item):
    service, _ = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    session.commit()
    first_updated = service.updated_at
    same, status = set_price(session, service.id, Decimal("75000"))
    assert status == UNCHANGED
    session.commit()
    assert same.updated_at == first_updated  # no UPDATE, timestamp untouched
    assert set_price(session, 42, Decimal("80000")) == (None, NOT_FOUND)


def test_remove_service(session, item):
    service, _ = add_service(session, "Cambio módulo A32", item.id, Decimal("75000"))
    session.flush()
    assert remove_service(session, service.id) == "removed"
    assert list_services(session) == []


# --- main() wiring, against a temp SQLite file ------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'radar.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def _seed_item(url: str) -> int:
    """Create one tracked item in the CLI's database and return its id."""
    engine = get_engine(url)
    init_db(engine)
    with get_session_factory(engine)() as session:
        tracked = TrackedItem(query="modulo samsung a32")
        session.add(tracked)
        session.commit()
        item_id = tracked.id
    engine.dispose()
    return item_id


def test_main_add_list_remove_roundtrip(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "75000"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("added:")
    assert 'label="Cambio módulo A32"' in out
    assert main(["list"]) == 0
    assert "total=1" in capsys.readouterr().out
    assert main(["remove", "1"]) == 0
    capsys.readouterr()
    assert main(["list"]) == 0
    assert "no service prices" in capsys.readouterr().out


def test_main_readd_identical_says_nothing_to_do(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "75000"]) == 0
    capsys.readouterr()
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "75000"]) == 0
    out = capsys.readouterr().out
    assert "nothing to do" in out
    assert "replaced" not in out


def test_main_set_price_changed_unchanged_and_missing(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "75000"]) == 0
    capsys.readouterr()
    assert main(["set-price", "1", "80000"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("changed:")
    assert "price=80000.00" in out
    assert main(["set-price", "1", "80000"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert main(["set-price", "42", "80000"]) == 1
    assert "no service price with id 42" in capsys.readouterr().out


def test_main_add_unknown_tracked_item_exits_one(capsys, cli_db) -> None:
    assert main(["add", "Cambio módulo A32", "--item", "42", "--price", "75000"]) == 1
    assert "no tracked item with id 42" in capsys.readouterr().out


def test_main_add_rejects_non_positive_price(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "0"]) == 1
    assert "positive" in capsys.readouterr().out
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "abc"]) == 1
    assert 'got "abc"' in capsys.readouterr().out


def test_main_add_rejects_non_finite_price(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    for bad in ("nan", "inf", "-inf"):
        # --price=-inf: the = form keeps argparse from reading -inf as a flag.
        assert main(["add", "Cambio módulo A32", "--item", str(item_id), f"--price={bad}"]) == 1
        assert f'error: price must be a number, got "{bad}"' in capsys.readouterr().out
    capsys.readouterr()
    assert main(["list"]) == 0
    assert "no service prices" in capsys.readouterr().out  # nothing was stored


def test_main_add_quantizes_price_to_centavos(capsys, cli_db) -> None:
    item_id = _seed_item(cli_db)
    assert main(["add", "Cambio módulo A32", "--item", str(item_id), "--price", "75000.999"]) == 0
    assert "price=75001.00" in capsys.readouterr().out  # echo matches what is stored
