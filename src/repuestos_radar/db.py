"""Engine and session wiring.

The connection string comes from ``DATABASE_URL`` (a ``.env`` file is honored
via python-dotenv). Tests never touch ``.env``: they pass an explicit SQLite
URL instead.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from repuestos_radar.models import Base


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(database_url: str | None = None) -> Engine:
    """Create an engine from an explicit URL, or from DATABASE_URL (env / .env)."""
    if database_url is None:
        load_dotenv()
        database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set (pass a URL or configure the environment)")
    engine = create_engine(database_url)
    if engine.dialect.name == "sqlite":
        # SQLite ships with FK enforcement off; turn it on per connection so
        # dev/tests behave like postgres.
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to the given engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables. Known simplification: no migrations yet (create_all only)."""
    Base.metadata.create_all(engine)
