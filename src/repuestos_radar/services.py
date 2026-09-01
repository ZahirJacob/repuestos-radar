"""Minimal service price-list CLI (dev-facing, until M4's admin page).

What Activcelu charges the customer for each repair lives in the
``service_prices`` table; margins compare those prices against the day's
part prices. Until the dashboard's admin page exists, this CLI is how the
price list is managed:

    python -m repuestos_radar.services add "Cambio módulo A32" --item 3 --price 75000
    python -m repuestos_radar.services list
    python -m repuestos_radar.services set-price 2 80000
    python -m repuestos_radar.services remove 2

``add`` is an upsert: on an existing label it sets the price AND the tracked
item link to the given values (a friendly no-op when both already match).
``remove`` deletes the row — unlike tracked items, a price-list entry has no
history worth keeping.

Same database contract as the ingestion runner: ``DATABASE_URL`` from the
environment (or ``.env``), tables created at startup if missing.
"""

import argparse
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.models import ServicePrice, TrackedItem

ADDED = "added"
UPDATED = "updated"
CHANGED = "changed"
UNCHANGED = "unchanged"
NOT_FOUND = "not-found"
REMOVED = "removed"


def add_service(
    session: Session, label: str, tracked_item_id: int, price: Decimal
) -> tuple[ServicePrice, str]:
    """Upsert a repair by label: add it, or point an existing label at the
    given item and price.

    Returns the service plus a status: ADDED for a new row, UPDATED when an
    existing label had its price or item link replaced, UNCHANGED when the
    given values already match (a no-op). The caller owns the commit.
    """
    existing = session.scalars(
        select(ServicePrice).where(ServicePrice.label == label)
    ).one_or_none()
    if existing is None:
        service = ServicePrice(tracked_item_id=tracked_item_id, label=label, price_ars=price)
        session.add(service)
        session.flush()  # assign the id so the caller can print it
        return service, ADDED
    if existing.price_ars == price and existing.tracked_item_id == tracked_item_id:
        return existing, UNCHANGED
    existing.price_ars = price
    existing.tracked_item_id = tracked_item_id
    return existing, UPDATED


def list_services(session: Session) -> list[ServicePrice]:
    """All service prices, oldest first."""
    return list(session.scalars(select(ServicePrice).order_by(ServicePrice.id)))


def set_price(session: Session, service_id: int, price: Decimal) -> tuple[ServicePrice | None, str]:
    """Change one service's price by id.

    Returns (service, CHANGED | UNCHANGED) or (None, NOT_FOUND). The caller
    owns the commit.
    """
    service = session.get(ServicePrice, service_id)
    if service is None:
        return None, NOT_FOUND
    if service.price_ars == price:
        return service, UNCHANGED
    service.price_ars = price
    return service, CHANGED


def remove_service(session: Session, service_id: int) -> str:
    """Delete one service price by id. Returns REMOVED or NOT_FOUND."""
    service = session.get(ServicePrice, service_id)
    if service is None:
        return NOT_FOUND
    session.delete(service)
    return REMOVED


def _parse_price(raw: str) -> Decimal | None:
    """A positive Decimal in whole centavos, or None after a one-line error.

    Decimal happily parses "nan" and "inf", so finiteness is checked before
    the sign (comparing NaN raises InvalidOperation). Quantizing here makes
    the echoed price match what Numeric(12, 2) will store.
    """
    try:
        price = Decimal(raw)
    except InvalidOperation:
        price = None
    if price is None or not price.is_finite():
        print(f'error: price must be a number, got "{raw}"')
        return None
    if price <= 0:
        print("error: price must be positive")
        return None
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _describe(service: ServicePrice) -> str:
    # Double quotes in the label are swapped for single so the key=value line
    # stays parseable (same convention as the tracked-items CLI).
    label_text = service.label.replace('"', "'")
    return (
        f"id={service.id} item={service.tracked_item_id} "
        f'price={service.price_ars} label="{label_text}"'
    )


def _cmd_add(session: Session, args: argparse.Namespace) -> int:
    label = args.label.strip()
    if not label:
        print("error: label must be non-empty")
        return 1
    price = _parse_price(args.price)
    if price is None:
        return 1
    if session.get(TrackedItem, args.item) is None:
        print(f"error: no tracked item with id {args.item}")
        return 1
    service, status = add_service(session, label, args.item, price)
    session.commit()
    messages = {
        ADDED: "added",
        UPDATED: "updated (label already existed — price and item set to the given values)",
        UNCHANGED: "already in the price list with those values — nothing to do",
    }
    print(f"{messages[status]}: {_describe(service)}")
    return 0


def _cmd_list(session: Session, _args: argparse.Namespace) -> int:
    services = list_services(session)
    if not services:
        print("no service prices")
        return 0
    for service in services:
        print(f"{_describe(service)} updated={service.updated_at.date().isoformat()}")
    print(f"total={len(services)}")
    return 0


def _cmd_set_price(session: Session, args: argparse.Namespace) -> int:
    price = _parse_price(args.price)
    if price is None:
        return 1
    service, status = set_price(session, args.id, price)
    if service is None:
        print(f"error: no service price with id {args.id}")
        return 1
    session.commit()
    prefix = "changed" if status == CHANGED else "already at that price — nothing to do"
    print(f"{prefix}: {_describe(service)}")
    return 0


def _cmd_remove(session: Session, args: argparse.Namespace) -> int:
    service = session.get(ServicePrice, args.id)
    if service is None:
        print(f"error: no service price with id {args.id}")
        return 1
    # Describe before deleting: after the commit the row is gone, and this is
    # the one command where confirming WHICH repair was touched matters most.
    description = _describe(service)
    remove_service(session, args.id)
    session.commit()
    print(f"removed: {description}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m repuestos_radar.services",
        description="Manage the repair price list (until the M4 admin page exists).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="add a repair (or reprice an existing label)")
    add.add_argument("label", help='the repair name, e.g. "Cambio módulo A32"')
    add.add_argument(
        "--item", type=int, required=True, help="tracked item id whose part this repair consumes"
    )
    add.add_argument("--price", required=True, help="what the customer pays, in ARS")
    add.set_defaults(handler=_cmd_add)

    list_ = subparsers.add_parser("list", help="show every service price")
    list_.set_defaults(handler=_cmd_list)

    set_price_ = subparsers.add_parser("set-price", help="change one repair's price")
    set_price_.add_argument("id", type=int, help="the service id shown by 'list'")
    set_price_.add_argument("price", help="the new price, in ARS")
    set_price_.set_defaults(handler=_cmd_set_price)

    remove = subparsers.add_parser("remove", help="delete a repair from the price list")
    remove.add_argument("id", type=int, help="the service id shown by 'list'")
    remove.set_defaults(handler=_cmd_remove)

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
        print(f"services aborted (database error): {' '.join(str(exc).split())}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
