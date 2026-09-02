"""SQLAlchemy declarative models: tracked search items and daily listing snapshots."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# What a tracked item is. A "part" (the default) is a spare part such as a
# module or battery; a "phone" is a whole handset. The relevance filter uses
# the kind to reject part listings that merely carry a phone's model words.
KIND_PART = "part"
KIND_PHONE = "phone"
TRACKED_KINDS: frozenset[str] = frozenset({KIND_PART, KIND_PHONE})


class TrackedItem(Base):
    """A search the client wants tracked (e.g. "modulo samsung a32").

    Managed by the client through the dashboard admin page, not code.
    """

    __tablename__ = "tracked_items"
    __table_args__ = (
        CheckConstraint("length(trim(query)) > 0", name="ck_tracked_items_query"),
        CheckConstraint(f"kind IN ('{KIND_PART}', '{KIND_PHONE}')", name="ck_tracked_items_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(Text, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "part" or "phone" (TRACKED_KINDS). server_default so rows written outside
    # the ORM get it too; db.init_db back-fills the column on databases that
    # predate it.
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default=KIND_PART, server_default=KIND_PART
    )
    # Stored as UTC. Note: postgres returns aware datetimes here, but sqlite
    # (dev/tests) returns naive ones — normalize before comparing across dialects.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    listings: Mapped[list["Listing"]] = relationship(back_populates="tracked_item")


class Listing(Base):
    """One price snapshot of one listing from one source on one day."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
            "tracked_item_id",
            "source_slug",
            "external_id",
            "fetched_date",
            name="uq_listings_daily_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(ForeignKey("tracked_items.id"))
    source_slug: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    condition: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    fetched_date: Mapped[date] = mapped_column(Date)
    # Relevance label from the filter: match / low_confidence / reject.
    # Nullable so listings stored before classification (or without it) are
    # valid; the filter never drops rows, it only labels them.
    relevance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    tracked_item: Mapped[TrackedItem] = relationship(back_populates="listings")


class ServicePrice(Base):
    """What Activcelu charges the customer for one repair.

    Linked to the tracked item whose part the repair consumes, so margin =
    this price minus that part's best price. Managed by the services CLI now,
    by the dashboard admin page in M4.
    """

    __tablename__ = "service_prices"
    __table_args__ = (
        CheckConstraint("length(trim(label)) > 0", name="ck_service_prices_label"),
        CheckConstraint("price_ars > 0", name="ck_service_prices_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(ForeignKey("tracked_items.id"))
    label: Mapped[str] = mapped_column(Text, unique=True)
    price_ars: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    tracked_item: Mapped[TrackedItem] = relationship()


class QuickSearchRun(Base):
    """One on-demand quick search, recorded to enforce the daily cap.

    ``ran_on`` is the Argentine calendar day the run counts against —
    computed by the caller, stored explicitly so the cap query never does
    timezone math in SQL (SQLite and Postgres disagree on datetime handling).
    """

    __tablename__ = "quick_search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(ForeignKey("tracked_items.id"))
    ran_on: Mapped[date] = mapped_column(Date, index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
