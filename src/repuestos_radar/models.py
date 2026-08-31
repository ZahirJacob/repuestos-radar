"""SQLAlchemy declarative models: tracked search items and daily listing snapshots."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
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


class TrackedItem(Base):
    """A search the client wants tracked (e.g. "modulo samsung a32").

    Managed by the client through the dashboard admin page, not code.
    """

    __tablename__ = "tracked_items"
    __table_args__ = (CheckConstraint("length(trim(query)) > 0", name="ck_tracked_items_query"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(Text, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    listings: Mapped[list["Listing"]] = relationship(back_populates="tracked_item")


class Listing(Base):
    """One price snapshot of one listing from one source on one day."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
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

    tracked_item: Mapped[TrackedItem] = relationship(back_populates="listings")
