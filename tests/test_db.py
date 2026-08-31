"""Tests for engine/session wiring. Offline: SQLite in-memory only, no .env values."""

import pytest
from sqlalchemy import inspect

from repuestos_radar.db import get_engine, get_session_factory, init_db

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
