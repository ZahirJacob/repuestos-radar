"""Minimal tracked-items management CLI (dev-facing twin of the dashboard's admin page).

The watchlist — which searches the daily ingestion runs — lives in the
``tracked_items`` table. The client manages it from the dashboard's Ajustes
page; this CLI is the scriptable route over the same helpers:

    python -m repuestos_radar.tracked add "modulo samsung a34"
    python -m repuestos_radar.tracked list
    python -m repuestos_radar.tracked pause 3
    python -m repuestos_radar.tracked resume 3

Items are paused, never deleted: a paused item keeps its price history and is
simply skipped by the ingestion runner. ``add`` on an already-tracked query
is a friendly no-op — and reactivates the item if it was paused.

Same database contract as the ingestion runner: ``DATABASE_URL`` from the
environment (or ``.env``), tables created at startup if missing.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import TrackedItem

ADDED = "added"
REACTIVATED = "reactivated"
ALREADY_ACTIVE = "already-active"
CHANGED = "changed"
UNCHANGED = "unchanged"
NOT_FOUND = "not-found"


def add_item(session: Session, query: str) -> tuple[TrackedItem, str]:
    """Add a query to the watchlist, or revive it if it is already there.

    Returns the item plus a status: ADDED for a new item, REACTIVATED when an
    existing paused item was switched back on, ALREADY_ACTIVE when the query
    is already tracked and active (a no-op). The caller owns the commit.
    """
    existing = session.scalars(select(TrackedItem).where(TrackedItem.query == query)).one_or_none()
    if existing is None:
        item = TrackedItem(query=query)
        session.add(item)
        session.flush()  # assign the id so the caller can print it
        return item, ADDED
    if existing.active:
        return existing, ALREADY_ACTIVE
    existing.active = True
    return existing, REACTIVATED


def list_items(session: Session) -> list[TrackedItem]:
    """All tracked items (active and paused), oldest first."""
    return list(session.scalars(select(TrackedItem).order_by(TrackedItem.id)))


def set_active(session: Session, item_id: int, active: bool) -> tuple[TrackedItem | None, str]:
    """Pause or resume one item by id.

    Returns (item, CHANGED | UNCHANGED) or (None, NOT_FOUND). The caller owns
    the commit.
    """
    item = session.get(TrackedItem, item_id)
    if item is None:
        return None, NOT_FOUND
    if item.active == active:
        return item, UNCHANGED
    item.active = active
    return item, CHANGED


def _describe(item: TrackedItem) -> str:
    # Double quotes in the query are swapped for single so the key=value line
    # stays parseable (same convention as the ingestion run report).
    query_text = item.query.replace('"', "'")
    return f'id={item.id} active={"yes" if item.active else "no"} query="{query_text}"'


def _cmd_add(session: Session, args: argparse.Namespace) -> int:
    query = args.query.strip()
    if not query:
        print("error: query must be non-empty")
        return 1
    item, status = add_item(session, query)
    session.commit()
    messages = {
        ADDED: "added",
        REACTIVATED: "reactivated (was paused)",
        ALREADY_ACTIVE: "already tracked and active — nothing to do",
    }
    print(f"{messages[status]}: {_describe(item)}")
    return 0


def _cmd_list(session: Session, _args: argparse.Namespace) -> int:
    items = list_items(session)
    if not items:
        print("no tracked items")
        return 0
    for item in items:
        print(f"{_describe(item)} created={item.created_at.date().isoformat()}")
    active_count = sum(1 for item in items if item.active)
    print(f"total={len(items)} active={active_count} paused={len(items) - active_count}")
    return 0


def _cmd_set_active(session: Session, args: argparse.Namespace, active: bool) -> int:
    item, status = set_active(session, args.id, active)
    if item is None:
        print(f"error: no tracked item with id {args.id}")
        return 1
    session.commit()
    verb = "resumed" if active else "paused"
    prefix = verb if status == CHANGED else f"already {verb} — nothing to do"
    print(f"{prefix}: {_describe(item)}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m repuestos_radar.tracked",
        description="Manage the tracked-items watchlist (same data as the dashboard admin page).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="track a new search query (or revive a paused one)")
    add.add_argument("query", help='the search to track, e.g. "modulo samsung a34"')
    add.set_defaults(handler=_cmd_add)

    list_ = subparsers.add_parser("list", help="show every tracked item, active and paused")
    list_.set_defaults(handler=_cmd_list)

    pause = subparsers.add_parser("pause", help="stop ingesting an item (history is kept)")
    pause.add_argument("id", type=int, help="the item id shown by 'list'")
    pause.set_defaults(handler=lambda session, args: _cmd_set_active(session, args, False))

    resume = subparsers.add_parser("resume", help="reactivate a paused item")
    resume.add_argument("id", type=int, help="the item id shown by 'list'")
    resume.set_defaults(handler=lambda session, args: _cmd_set_active(session, args, True))

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse arguments, open the database, run one subcommand."""
    args = _build_parser().parse_args(argv)
    # One try around startup AND the command: a DB error during a handler
    # (e.g. the commit) must abort with the same one-line message, not a
    # traceback.
    try:
        engine = get_engine()
        init_db(engine)
        with get_session_factory(engine)() as session:
            return args.handler(session, args)
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"tracked aborted (database error): {' '.join(str(exc).split())}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
