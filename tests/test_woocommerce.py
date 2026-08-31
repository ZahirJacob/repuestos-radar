"""Tests for the WooCommerce Store API adapter. All offline via httpx.MockTransport."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from repuestos_radar.adapters.base import USER_AGENT, AdapterError
from repuestos_radar.adapters.woocommerce import WooCommerceAdapter
from repuestos_radar.schema import Condition
from repuestos_radar.sources import Source

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://shop.example.com.ar"
PRODUCTS_PATH = "/wp-json/wc/store/v1/products"

ROBOTS_ALLOW = "User-agent: *\nDisallow: /wp-admin/\n"
ROBOTS_DISALLOW = "User-agent: *\nDisallow: /wp-json/\n"


def make_source() -> Source:
    return Source(
        slug="shop-test",
        name="Shop Test",
        url=BASE_URL,
        platform="woocommerce",
        address="Calle Falsa 123",
        city="Rosario",
        trust_notes="Test shop.",
    )


def fixture(name: str) -> list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeShop:
    """MockTransport handler that serves robots.txt and Store API pages."""

    def __init__(self, pages: list[object], robots: str | None = ROBOTS_ALLOW):
        self.pages = pages
        self.robots = robots
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            if self.robots is None:
                return httpx.Response(404, text="not here")
            return httpx.Response(200, text=self.robots)
        if request.url.path == PRODUCTS_PATH:
            page = int(request.url.params.get("page", "1"))
            body = self.pages[min(page, len(self.pages)) - 1]
            if isinstance(body, int):
                return httpx.Response(body, text="error")
            return httpx.Response(200, json=body)
        return httpx.Response(404, text="unknown path")

    @property
    def product_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == PRODUCTS_PATH]


def make_adapter(shop: FakeShop, **kwargs) -> WooCommerceAdapter:
    sleeps = kwargs.pop("sleeps", [])
    return WooCommerceAdapter(
        make_source(),
        transport=httpx.MockTransport(shop),
        sleep=sleeps.append,
        **kwargs,
    )


def test_happy_path_maps_products_to_normalized_listings() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json")])
    listings = make_adapter(shop).fetch("modulo")

    assert len(listings) == 2
    first = listings[0]
    assert first.source_slug == "shop-test"
    assert first.external_id == "101"
    assert first.title == "Módulo Samsung A32 OLED con marco"
    assert first.price == Decimal("12345.67")  # 1234567 minor units, minor_unit=2
    assert first.currency == "ARS"
    assert first.condition is Condition.UNKNOWN
    assert first.url == "https://shop.example.com.ar/producto/modulo-samsung-a32-oled/"
    assert first.fetched_at == date.today()
    assert listings[1].price == Decimal("8999.00")

    (request,) = shop.product_requests
    assert request.url.params["search"] == "modulo"
    assert request.url.params["per_page"] == "100"


def test_empty_results_return_empty_list() -> None:
    shop = FakeShop(pages=[[]])
    assert make_adapter(shop).fetch("inexistente") == []


def test_pagination_follows_full_pages() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json"), fixture("store_api_page2.json")])
    listings = make_adapter(shop, per_page=2).fetch("modulo")

    assert [listing.external_id for listing in listings] == ["101", "202", "303"]
    pages = [r.url.params["page"] for r in shop.product_requests]
    assert pages == ["1", "2"]


def test_malformed_products_are_skipped_and_counted() -> None:
    shop = FakeShop(pages=[fixture("store_api_mixed.json")])
    adapter = make_adapter(shop)
    listings = adapter.fetch("modulo")

    assert [listing.external_id for listing in listings] == ["101", "606"]
    assert adapter.skipped == 2  # null prices + zero price


def test_server_errors_retry_with_backoff_then_raise() -> None:
    shop = FakeShop(pages=[500])
    sleeps: list[float] = []
    adapter = make_adapter(shop, sleeps=sleeps)

    with pytest.raises(AdapterError, match="shop-test"):
        adapter.fetch("modulo")

    assert len(shop.product_requests) == 3  # initial + 2 retries
    # Exponential backoff between retry attempts.
    backoffs = [s for s in sleeps if s > 1.0] or sleeps
    assert len([s for s in sleeps if s >= 1.0]) >= 2
    assert backoffs == sorted(backoffs)


def test_client_errors_raise_without_retry() -> None:
    shop = FakeShop(pages=[403])
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="403"):
        adapter.fetch("modulo")

    assert len(shop.product_requests) == 1  # a rejection is final: no retries, no UA games


def test_robots_disallow_blocks_before_any_product_request() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json")], robots=ROBOTS_DISALLOW)
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="robots.txt"):
        adapter.fetch("modulo")

    assert shop.product_requests == []


def test_missing_robots_txt_is_treated_as_allow() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json")], robots=None)
    assert len(make_adapter(shop).fetch("modulo")) == 2


def test_robots_txt_is_fetched_once_and_cached() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json")])
    adapter = make_adapter(shop)
    adapter.fetch("modulo")
    adapter.fetch("bateria")

    robots_requests = [r for r in shop.requests if r.url.path == "/robots.txt"]
    assert len(robots_requests) == 1


def test_honest_user_agent_on_every_request() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json")])
    make_adapter(shop).fetch("modulo")

    assert len(shop.requests) >= 2  # robots + products
    for request in shop.requests:
        assert request.headers["User-Agent"] == USER_AGENT
    assert "repuestos-radar" in USER_AGENT
    assert "Mozilla" not in USER_AGENT  # no browser impersonation, ever


def test_courtesy_delay_between_successive_requests() -> None:
    shop = FakeShop(pages=[fixture("store_api_page1.json"), fixture("store_api_page2.json")])
    sleeps: list[float] = []
    make_adapter(shop, per_page=2, sleeps=sleeps).fetch("modulo")

    # 3 requests (robots + 2 pages) -> at least 2 courtesy pauses of >= 1s.
    assert len(shop.requests) == 3
    assert len([s for s in sleeps if s >= 1.0]) >= 2


def test_timeout_is_15_seconds() -> None:
    shop = FakeShop(pages=[[]])
    adapter = make_adapter(shop)
    assert adapter._client.timeout == httpx.Timeout(15.0)
