"""Normalized listing schema shared by all source adapters.

Every adapter, whatever the source looks like, emits ``NormalizedListing``
instances; everything downstream (storage, analysis, dashboard) only ever
sees this shape.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Condition(Enum):
    """Condition of the listed item, as far as the source states it."""

    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NormalizedListing:
    """One listing from one source on one day, in the common shape.

    Prices are ARS unless the source says otherwise.
    """

    source_slug: str
    external_id: str
    title: str
    price: Decimal
    currency: str
    condition: Condition
    url: str
    fetched_at: date

    def __post_init__(self) -> None:
        for field_name in ("source_slug", "external_id", "title"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.price <= 0:
            raise ValueError(f"price must be positive, got {self.price}")
        # The URL becomes a link the client taps on the dashboard: only web
        # URLs, whatever a store's JSON happens to say. Malformed -> the
        # adapter's usual "skip malformed product" path (ValueError).
        if not self.url.lower().startswith(("http://", "https://")):
            raise ValueError(f"url must be an http(s) URL, got {self.url!r}")
