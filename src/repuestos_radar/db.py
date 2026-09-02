"""Engine and session wiring.

The connection string comes from ``DATABASE_URL`` (a ``.env`` file is honored
via python-dotenv). Tests never touch ``.env``: they pass an explicit SQLite
URL instead.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from repuestos_radar.models import KIND_PART, Base


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
    # SQLAlchemy maps the bare postgresql:// scheme to psycopg2, but this
    # project ships psycopg (v3) — pin the driver so plain Postgres URLs
    # (Neon's default format) work. Any other scheme passes through untouched.
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    # pool_pre_ping: the dashboard keeps one engine alive for hours while
    # Neon suspends the database after a few idle minutes and drops its
    # connections. Without the ping the pool hands back a dead connection and
    # the first query after a pause fails with OperationalError.
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite ships with FK enforcement off; turn it on per connection so
        # dev/tests behave like postgres.
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to the given engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables, then apply the one hand-rolled migration we carry.

    Known simplification: no Alembic yet. ``create_all`` creates missing
    TABLES but never adds columns to existing ones, so ``tracked_items.kind``
    (added after the first deploy) is back-filled here by
    ``_add_tracked_item_kind``. Idempotent, so every entry point (ingest, the
    CLIs, the dashboard) keeps calling this at startup. This is the only
    migration until Alembic lands; a second one should trigger that move
    rather than grow this function.
    """
    Base.metadata.create_all(engine)
    _add_tracked_item_kind(engine)


# DDL both SQLite and Postgres accept: ADD COLUMN with a constant default also
# fills existing rows. Same type as the model column; the CHECK constraint
# lives on the model only (fresh databases get it from create_all).
_ADD_KIND_COLUMN = (
    f"ALTER TABLE tracked_items ADD COLUMN kind VARCHAR(10) NOT NULL DEFAULT '{KIND_PART}'"
)


def _add_tracked_item_kind(engine: Engine) -> None:
    """Add ``tracked_items.kind`` when the table predates the column; no-op otherwise."""
    columns = {column["name"] for column in inspect(engine).get_columns("tracked_items")}
    if "kind" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text(_ADD_KIND_COLUMN))
