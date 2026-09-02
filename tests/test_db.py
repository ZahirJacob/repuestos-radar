"""Tests for engine/session wiring. Offline: SQLite in-memory only, no .env values."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from repuestos_radar import db as db_module
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing, TrackedItem

SQLITE_URL = "sqlite+pysqlite:///:memory:"


def test_get_engine_uses_explicit_url() -> None:
    engine = get_engine(SQLITE_URL)
    assert engine.url.get_backend_name() == "sqlite"
    engine.dispose()


def test_get_engine_without_url_anywhere_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("repuestos_radar.db.load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_engine()


def test_get_engine_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", SQLITE_URL)
    monkeypatch.setattr("repuestos_radar.db.load_dotenv", lambda: None)
    engine = get_engine()
    assert engine.url.get_backend_name() == "sqlite"
    engine.dispose()


def test_bare_postgresql_url_is_pinned_to_psycopg3() -> None:
    # No connection is made: create_engine only resolves the dialect/driver.
    # Without the normalization this raises ModuleNotFoundError (psycopg2).
    engine = get_engine("postgresql://user:secret@db.example.neon.tech/radar")
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.host == "db.example.neon.tech"
    engine.dispose()


def test_postgres_engine_pre_pings_pooled_connections() -> None:
    # Neon suspends idle databases and drops connections; the long-lived
    # dashboard engine must detect a dead pooled connection before using it.
    engine = get_engine("postgresql://user:secret@db.example.neon.tech/radar")
    assert engine.pool._pre_ping is True
    engine.dispose()


def test_explicit_psycopg_url_passes_through_unchanged() -> None:
    engine = get_engine("postgresql+psycopg://user:secret@db.example.neon.tech/radar")
    assert engine.url.drivername == "postgresql+psycopg"
    engine.dispose()


def test_non_postgres_scheme_is_not_rewritten() -> None:
    engine = get_engine(SQLITE_URL)
    assert engine.url.drivername == "sqlite+pysqlite"
    engine.dispose()


def test_init_db_creates_tables() -> None:
    engine = get_engine(SQLITE_URL)
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"tracked_items", "listings"} <= tables
    engine.dispose()


def test_session_factory_produces_working_sessions() -> None:
    engine = get_engine(SQLITE_URL)
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as session:
        assert session.get_bind() is engine
    engine.dispose()


def test_sqlite_engine_enforces_foreign_keys() -> None:
    engine = get_engine(SQLITE_URL)
    init_db(engine)
    with get_session_factory(engine)() as session:
        session.add(
            Listing(
                tracked_item_id=999,  # no such tracked item
                source_slug="novocell",
                external_id="abc-123",
                title="Módulo Samsung A32",
                price=Decimal("45000.00"),
                currency="ARS",
                condition="new",
                url="https://novocell.com.ar/producto/modulo-a32",
                fetched_date=date(2026, 8, 31),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


# --- the one hand-rolled migration: tracked_items.kind -----------------------

# The tracked_items table as create_all made it BEFORE the kind column existed
# (what Neon holds from the first deploy).
_PRE_KIND_TRACKED_ITEMS = """
CREATE TABLE tracked_items (
    id INTEGER NOT NULL PRIMARY KEY,
    query TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT ck_tracked_items_query CHECK (length(trim(query)) > 0)
)
"""


def _kind_column(engine) -> dict | None:
    for column in inspect(engine).get_columns("tracked_items"):
        if column["name"] == "kind":
            return column
    return None


def test_init_db_adds_kind_to_a_tracked_items_table_that_predates_it(tmp_path) -> None:
    engine = get_engine(f"sqlite+pysqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text(_PRE_KIND_TRACKED_ITEMS))
        connection.execute(
            text(
                "INSERT INTO tracked_items (query, active, created_at) "
                "VALUES ('modulo a32', 1, '2026-09-01 00:00:00')"
            )
        )
    assert _kind_column(engine) is None

    init_db(engine)

    column = _kind_column(engine)
    assert column is not None
    assert column["nullable"] is False
    assert "part" in column["default"]
    with get_session_factory(engine)() as session:
        item = session.scalars(select(TrackedItem)).one()
        assert item.kind == "part"  # the existing row got the default
        # And the ORM can write both the default and the new value afterwards.
        session.add(TrackedItem(query="samsung s24 ultra", kind="phone"))
        session.commit()
        kinds = dict(session.execute(select(TrackedItem.query, TrackedItem.kind)).all())
        assert kinds == {"modulo a32": "part", "samsung s24 ultra": "phone"}
    engine.dispose()


def test_init_db_is_a_no_op_when_kind_already_exists(tmp_path) -> None:
    engine = get_engine(f"sqlite+pysqlite:///{tmp_path / 'new.db'}")
    init_db(engine)
    before = [c["name"] for c in inspect(engine).get_columns("tracked_items")]
    assert before.count("kind") == 1

    init_db(engine)  # second call must not try to add the column again

    after = [c["name"] for c in inspect(engine).get_columns("tracked_items")]
    assert after == before
    engine.dispose()


class _RecordingEngine:
    """Enough of an Engine for the migration: a dialect name and begin()."""

    def __init__(self, dialect: str) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.statements: list[str] = []

    @contextmanager
    def begin(self):
        yield SimpleNamespace(execute=lambda statement: self.statements.append(str(statement)))


def test_migration_adds_the_check_constraint_on_postgres_only(monkeypatch) -> None:
    monkeypatch.setattr(db_module, "_has_kind_column", lambda engine: False)

    postgres = _RecordingEngine("postgresql")
    db_module._add_tracked_item_kind(postgres)
    assert postgres.statements == [db_module._ADD_KIND_COLUMN, db_module._ADD_KIND_CHECK]
    assert "ADD CONSTRAINT ck_tracked_items_kind CHECK (kind IN ('part', 'phone'))" in (
        db_module._ADD_KIND_CHECK
    )

    sqlite = _RecordingEngine("sqlite")
    db_module._add_tracked_item_kind(sqlite)
    assert sqlite.statements == [db_module._ADD_KIND_COLUMN]


def _pre_kind_engine(tmp_path):
    engine = get_engine(f"sqlite+pysqlite:///{tmp_path / 'race.db'}")
    with engine.begin() as connection:
        connection.execute(text(_PRE_KIND_TRACKED_ITEMS))
    return engine


def test_migration_survives_losing_the_race_to_another_process(tmp_path, monkeypatch) -> None:
    """Dashboard and cron both call init_db on a fresh deploy. The loser's
    ALTER fails with "duplicate column"; if the column is there now, fine."""
    engine = _pre_kind_engine(tmp_path)
    real_check = db_module._has_kind_column
    calls = []

    def other_process_wins_between_inspect_and_alter(target):
        calls.append(target)
        if len(calls) == 1:
            # The column is missing at inspection time, but by the time our
            # ALTER runs the other process has added it.
            with engine.begin() as connection:
                connection.execute(text(db_module._ADD_KIND_COLUMN))
            return False
        return real_check(target)

    monkeypatch.setattr(db_module, "_has_kind_column", other_process_wins_between_inspect_and_alter)

    init_db(engine)  # must not raise

    assert len(calls) == 2  # inspected, ALTER failed, re-inspected
    assert _kind_column(engine) is not None
    engine.dispose()


def test_migration_reraises_when_the_alter_fails_for_another_reason(tmp_path, monkeypatch) -> None:
    engine = _pre_kind_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(text(db_module._ADD_KIND_COLUMN))
    # The inspection says "missing" every time, so the duplicate-column error
    # cannot be explained by a race and must surface.
    monkeypatch.setattr(db_module, "_has_kind_column", lambda engine: False)

    with pytest.raises(OperationalError, match="duplicate column"):
        init_db(engine)
    engine.dispose()
