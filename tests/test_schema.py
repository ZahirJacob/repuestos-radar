"""Tests for the normalized listing model shared by all adapters."""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.schema import Condition, NormalizedListing


def make_listing(**overrides) -> NormalizedListing:
    defaults = {
        "source_slug": "novocell",
        "external_id": "abc-123",
        "title": "Módulo Samsung A32",
        "price": Decimal("45000.00"),
        "currency": "ARS",
        "condition": Condition.NEW,
        "url": "https://novocell.com.ar/producto/modulo-a32",
        "fetched_at": date(2026, 8, 31),
    }
    defaults.update(overrides)
    return NormalizedListing(**defaults)


def test_valid_listing_holds_its_fields() -> None:
    listing = make_listing()
    assert listing.source_slug == "novocell"
    assert listing.external_id == "abc-123"
    assert listing.price == Decimal("45000.00")
    assert listing.currency == "ARS"
    assert listing.condition is Condition.NEW
    assert listing.fetched_at == date(2026, 8, 31)


def test_listing_is_frozen() -> None:
    listing = make_listing()
    with pytest.raises(dataclasses.FrozenInstanceError):
        listing.price = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-10")])
def test_price_must_be_positive(bad_price: Decimal) -> None:
    with pytest.raises(ValueError, match="price"):
        make_listing(price=bad_price)


@pytest.mark.parametrize("field", ["title", "source_slug", "external_id"])
@pytest.mark.parametrize("empty", ["", "   "])
def test_required_text_fields_must_be_non_empty(field: str, empty: str) -> None:
    with pytest.raises(ValueError, match=field):
        make_listing(**{field: empty})


@pytest.mark.parametrize(
    "bad_url",
    ["javascript:alert(1)", "ftp://shop.example/p/1", "//shop.example/p/1", "", "   ", "p/1"],
)
def test_url_must_be_http_or_https(bad_url: str) -> None:
    """A listing URL ends up as a link the client taps; only web URLs are
    accepted, whatever a store's JSON says."""
    with pytest.raises(ValueError, match="url"):
        make_listing(url=bad_url)


def test_http_and_https_urls_are_accepted() -> None:
    assert make_listing(url="http://shop.example/p/1").url == "http://shop.example/p/1"
    assert make_listing(url="HTTPS://shop.example/p/1").url == "HTTPS://shop.example/p/1"


def test_condition_enum_members() -> None:
    assert {c.value for c in Condition} == {"new", "used", "refurbished", "unknown"}
