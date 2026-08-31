"""Tests for the tracked-items management CLI. SQLite in-memory / temp file."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import repuestos_radar.tracked
from repuestos_radar.models import Base, TrackedItem
from repuestos_radar.tracked import (
    ADDED,
    ALREADY_ACTIVE,
    CHANGED,
    NOT_FOUND,
    REACTIVATED,
    UNCHANGED,
    add_item,
    list_items,
    main,
    set_active,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_add_new_query(session: Session) -> None:
    item, status = add_item(session, "modulo samsung a34")
    session.commit()

    assert status == ADDED
    assert item.id is not None
    assert item.active
    row = session.scalars(select(TrackedItem)).one()
    assert row.query == "modulo samsung a34"


def test_add_existing_active_query_is_a_no_op(session: Session) -> None:
    add_item(session, "modulo samsung a34")
    session.commit()

    item, status = add_item(session, "modulo samsung a34")
    session.commit()

    assert status == ALREADY_ACTIVE
    assert item.active
    assert len(session.scalars(select(TrackedItem)).all()) == 1


def test_add_paused_query_reactivates_it(session: Session) -> None:
    original, _ = add_item(session, "modulo samsung a34")
    original.active = False
    session.commit()

    item, status = add_item(session, "modulo samsung a34")
    session.commit()

    assert status == REACTIVATED
    assert item.id == original.id  # same row revived, history intact
    assert item.active
    assert len(session.scalars(select(TrackedItem)).all()) == 1


def test_list_items_returns_all_in_id_order(session: Session) -> None:
    add_item(session, "modulo a34")
    paused, _ = add_item(session, "bateria iphone 11")
    paused.active = False
    session.commit()

    items = list_items(session)

    assert [item.query for item in items] == ["modulo a34", "bateria iphone 11"]
    assert [item.active for item in items] == [True, False]


def test_pause_and_resume(session: Session) -> None:
    item, _ = add_item(session, "modulo a34")
    session.commit()

    paused, status = set_active(session, item.id, active=False)
    session.commit()
    assert status == CHANGED
    assert paused is not None and not paused.active

    resumed, status = set_active(session, item.id, active=True)
    session.commit()
    assert status == CHANGED
    assert resumed is not None and resumed.active


def test_pause_already_paused_is_unchanged(session: Session) -> None:
    item, _ = add_item(session, "modulo a34")
    item.active = False
    session.commit()

    _, status = set_active(session, item.id, active=False)

    assert status == UNCHANGED


def test_set_active_unknown_id(session: Session) -> None:
    item, status = set_active(session, 999, active=False)
    assert item is None
    assert status == NOT_FOUND


# --- main() wiring, against a temp SQLite file ------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'radar.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def all_rows(url: str) -> list[tuple[str, bool]]:
    engine = create_engine(url)
    with Session(engine) as session:
        rows = [(i.query, i.active) for i in session.scalars(select(TrackedItem))]
    engine.dispose()
    return rows


def test_main_add_list_pause_resume_roundtrip(capsys, cli_db) -> None:
    assert main(["add", "modulo samsung a34"]) == 0
    assert 'added: id=1 active=yes query="modulo samsung a34"' in capsys.readouterr().out

    assert main(["pause", "1"]) == 0
    assert "paused: id=1 active=no" in capsys.readouterr().out
    assert all_rows(cli_db) == [("modulo samsung a34", False)]

    assert main(["resume", "1"]) == 0
    assert "resumed: id=1 active=yes" in capsys.readouterr().out
    assert all_rows(cli_db) == [("modulo samsung a34", True)]

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert 'id=1 active=yes query="modulo samsung a34" created=' in out
    assert "total=1 active=1 paused=0" in out


def test_main_add_duplicate_says_so_and_exits_zero(capsys, cli_db) -> None:
    assert main(["add", "modulo a34"]) == 0
    capsys.readouterr()

    assert main(["add", "modulo a34"]) == 0

    assert "already tracked and active" in capsys.readouterr().out


def test_main_add_strips_and_rejects_blank_query(capsys, cli_db) -> None:
    assert main(["add", "   "]) == 1
    assert "query must be non-empty" in capsys.readouterr().out
    assert all_rows(cli_db) == []

    assert main(["add", "  modulo a34  "]) == 0
    assert all_rows(cli_db) == [("modulo a34", True)]


def test_main_add_paused_query_prints_reactivated(capsys, cli_db) -> None:
    assert main(["add", "modulo a34"]) == 0
    assert main(["pause", "1"]) == 0
    capsys.readouterr()

    assert main(["add", "modulo a34"]) == 0

    assert 'reactivated (was paused): id=1 active=yes query="modulo a34"' in capsys.readouterr().out
    assert all_rows(cli_db) == [("modulo a34", True)]


def test_main_output_swaps_double_quotes_in_query(capsys, cli_db) -> None:
    assert main(["add", 'pantalla 5" tablet']) == 0

    out = capsys.readouterr().out
    assert 'query="pantalla 5\' tablet"' in out
    # The row itself keeps the original query; only the display is escaped.
    assert all_rows(cli_db) == [('pantalla 5" tablet', True)]


def test_main_pause_unknown_id_exits_one(capsys, cli_db) -> None:
    assert main(["pause", "42"]) == 1
    assert "no tracked item with id 42" in capsys.readouterr().out


def test_main_list_empty_database(capsys, cli_db) -> None:
    assert main(["list"]) == 0
    assert "no tracked items" in capsys.readouterr().out


def test_main_database_unreachable_exits_one(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'missing' / 'x.db'}")
    assert main(["list"]) == 1
    assert "tracked aborted (database error)" in capsys.readouterr().out


def test_main_db_error_during_command_aborts_with_one_line(monkeypatch, capsys, cli_db) -> None:
    """A DB failure inside a handler (e.g. at commit) must produce the same
    one-line abort as a startup failure, not a raw traceback."""

    def exploding_handler(session, args):
        raise OperationalError("INSERT INTO tracked_items ...", {}, Exception("db went\naway"))

    monkeypatch.setattr(repuestos_radar.tracked, "_cmd_list", exploding_handler)

    assert main(["list"]) == 1
    out = capsys.readouterr().out
    assert "tracked aborted (database error)" in out
    # The multi-line SQLAlchemy message is collapsed onto the abort line.
    assert "db went away" in out
