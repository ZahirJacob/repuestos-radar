"""Tests for engine/session wiring. Offline: SQLite in-memory only, no .env values."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import Listing

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
