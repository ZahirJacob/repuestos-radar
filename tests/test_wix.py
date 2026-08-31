"""Tests for the Wix storefront adapter. All offline via httpx.MockTransport.

Fixtures mirror the real shapes recon found on novocell.com.ar: the
``/_api/v2/dynamicmodel`` token payload and the storefront GraphQL
``getFilteredProducts`` response. Fixture bodies are served as raw text so
the adapter's exact-decimal JSON parsing is what the tests exercise.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from repuestos_radar.adapters.base import USER_AGENT, AdapterError
from repuestos_radar.adapters.wix import WixAdapter
from repuestos_radar.schema import Condition
from repuestos_radar.sources import Source

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://shop.example.com.ar"
GRAPHQL_PATH = "/_api/wix-ecommerce-storefront-web/api"
DYNAMICMODEL_PATH = "/_api/v2/dynamicmodel"

ROBOTS_ALLOW = "User-agent: *\nAllow: /\nDisallow: *?lightbox=\n"
ROBOTS_DISALLOW = "User-agent: *\nDisallow: /_api/\n"


def make_source() -> Source:
    return Source(
        slug="wix-test",
        name="Wix Test",
        url=BASE_URL,
        platform="wix",
        address="Calle Falsa 123",
        city="Rosario",
        trust_notes="Test shop.",
    )


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeWixShop:
    """Serves robots.txt, dynamicmodel, and GraphQL pages keyed by offset.

    ``pages`` entries are raw JSON strings or int status codes; the entry is
    picked by ``offset // limit`` from the POSTed GraphQL variables (the last
    entry repeats for later offsets). ``dynamicmodel`` may be a raw JSON
    string or an int status code.
    """

    def __init__(
        self,
        pages: list[object],
        robots: str | int | None = ROBOTS_ALLOW,
        dynamicmodel: str | int = fixture_text("wix_dynamicmodel.json"),
    ):
        self.pages = pages
        self.robots = robots
        self.dynamicmodel = dynamicmodel
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            if self.robots is None:
                return httpx.Response(404, text="not here")
            if isinstance(self.robots, int):
                return httpx.Response(self.robots, text="robots error")
            return httpx.Response(200, text=self.robots)
        if request.url.path == DYNAMICMODEL_PATH:
            if isinstance(self.dynamicmodel, int):
                return httpx.Response(self.dynamicmodel, text="error")
            return httpx.Response(
                200, text=self.dynamicmodel, headers={"content-type": "application/json"}
            )
        if request.url.path == GRAPHQL_PATH:
            variables = json.loads(request.content)["variables"]
            index = variables["offset"] // variables["limit"]
            body = self.pages[min(index, len(self.pages) - 1)]
            if isinstance(body, int):
                return httpx.Response(body, text="error")
            return httpx.Response(200, text=body, headers={"content-type": "application/json"})
        return httpx.Response(404, text="unknown path")

    @property
    def graphql_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == GRAPHQL_PATH]

    @property
    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.startswith("/_api/")]


def make_adapter(shop: FakeWixShop, **kwargs) -> WixAdapter:
    sleeps = kwargs.pop("sleeps", [])
    return WixAdapter(
        make_source(),
        transport=httpx.MockTransport(shop),
        sleep=sleeps.append,
        **kwargs,
    )


def test_happy_path_maps_products_to_normalized_listings() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    listings = make_adapter(shop).fetch("modulo")

    assert len(listings) == 2
    first = listings[0]
    assert first.source_slug == "wix-test"
    assert first.external_id == "39e0e097-d169-1b15-bdba-cb0dca978ff4"
    assert first.title == "PLACA CARGA MOTOROLA MOTO EDGE 50 FUSION GENERICO"
    assert first.price == Decimal("6800.0")
    assert first.currency == "ARS"
    assert first.condition is Condition.UNKNOWN
    assert first.url == (
        f"{BASE_URL}/product-page/placa-carga-motorola-moto-edge-50-fusion-generico"
    )
    assert first.fetched_at == date.today()


def test_prices_never_pass_through_float() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    listings = make_adapter(shop).fetch("modulo")

    # The fixture literal is 8999.90; a float round-trip would render "8999.9".
    assert str(listings[1].price) == "8999.90"


def test_search_term_and_paging_sent_in_graphql_variables() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    make_adapter(shop, per_page=50).fetch("modulo samsung")

    (request,) = shop.graphql_requests
    variables = json.loads(request.content)["variables"]
    assert variables["filters"]["term"]["values"] == ["modulo samsung"]
    assert variables["limit"] == 50
    assert variables["offset"] == 0


def test_instance_token_is_fetched_and_sent_as_authorization() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    make_adapter(shop).fetch("modulo")

    (request,) = shop.graphql_requests
    assert request.headers["Authorization"] == "fake-instance-token"


def test_pagination_follows_total_count() -> None:
    shop = FakeWixShop(
        pages=[fixture_text("wix_graphql_page1.json"), fixture_text("wix_graphql_page2.json")]
    )
    listings = make_adapter(shop, per_page=2).fetch("modulo")

    assert len(listings) == 3
    offsets = [json.loads(r.content)["variables"]["offset"] for r in shop.graphql_requests]
    assert offsets == [0, 2]


class ClampingWixShop:
    """A server that serves at most `served_per_page` products regardless of the
    requested limit, slicing a fixed pool by offset — reproduces a Wix server
    that clamps page size."""

    def __init__(self, total: int, served_per_page: int):
        self.served_per_page = served_per_page
        self.products = [
            {
                "id": f"id-{n:04d}",
                "name": f"MODULO TEST {n}",
                "price": 1000 + n,
                "currency": "ARS",
                "urlPart": f"modulo-test-{n}",
                "isInStock": True,
            }
            for n in range(total)
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        if request.url.path == DYNAMICMODEL_PATH:
            return httpx.Response(200, text=fixture_text("wix_dynamicmodel.json"))
        variables = json.loads(request.content)["variables"]
        offset = variables["offset"]
        page = self.products[offset : offset + self.served_per_page]
        body = {
            "data": {
                "catalog": {
                    "category": {
                        "productsWithMetaData": {"totalCount": len(self.products), "list": page}
                    }
                }
            }
        }
        return httpx.Response(200, json=body)


def test_clamped_page_size_still_yields_all_products() -> None:
    # Adapter asks for 100/page; server clamps to 10. Advancing by items
    # returned (not requested per_page) must lose nothing.
    shop = ClampingWixShop(total=25, served_per_page=10)
    adapter = WixAdapter(
        make_source(), transport=httpx.MockTransport(shop), sleep=lambda _s: None, per_page=100
    )
    listings = adapter.fetch("modulo")

    assert len(listings) == 25
    assert adapter.skipped == 0
    assert [listing.external_id for listing in listings] == [f"id-{n:04d}" for n in range(25)]


def test_runaway_pagination_hits_cap_and_raises() -> None:
    # totalCount is 3 in the fixture, but serve a huge one page after page.
    runaway = fixture_text("wix_graphql_page1.json").replace(
        '"totalCount": 3', '"totalCount": 9999'
    )
    shop = FakeWixShop(pages=[runaway])
    adapter = make_adapter(shop, per_page=2)

    with pytest.raises(AdapterError, match="page"):
        adapter.fetch("modulo")

    assert len(shop.graphql_requests) == 30


def test_malformed_products_are_skipped_and_counted() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_mixed.json")])
    adapter = make_adapter(shop)
    listings = adapter.fetch("modulo")

    assert [listing.external_id for listing in listings] == ["39e0e097-d169-1b15-bdba-cb0dca978ff4"]
    assert adapter.skipped == 4  # null name, zero price, missing urlPart, null price
    assert all(listing.title != "None" for listing in listings)


def test_empty_results_return_empty_list() -> None:
    empty = json.dumps(
        {"data": {"catalog": {"category": {"productsWithMetaData": {"totalCount": 0, "list": []}}}}}
    )
    shop = FakeWixShop(pages=[empty])
    assert make_adapter(shop).fetch("inexistente") == []


def test_graphql_errors_body_raises() -> None:
    errors = json.dumps({"errors": [{"message": "something broke"}]})
    shop = FakeWixShop(pages=[errors])
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="GraphQL") as excinfo:
        adapter.fetch("modulo")
    assert excinfo.value.slug == "wix-test"


def test_missing_store_token_raises() -> None:
    shop = FakeWixShop(
        pages=[fixture_text("wix_graphql_happy.json")], dynamicmodel=json.dumps({"apps": {}})
    )
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="token") as excinfo:
        adapter.fetch("modulo")
    assert excinfo.value.slug == "wix-test"
    assert shop.graphql_requests == []


def test_robots_disallow_blocks_before_any_api_request() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")], robots=ROBOTS_DISALLOW)
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="robots.txt"):
        adapter.fetch("modulo")

    assert shop.api_requests == []


def test_unreachable_robots_txt_is_treated_as_disallow() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")], robots=500)
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError, match="robots.txt"):
        adapter.fetch("modulo")

    assert shop.api_requests == []


def test_server_errors_retry_then_raise_with_cause() -> None:
    shop = FakeWixShop(pages=[500])
    adapter = make_adapter(shop)

    with pytest.raises(AdapterError) as excinfo:
        adapter.fetch("modulo")

    assert len(shop.graphql_requests) == 3  # initial + 2 retries
    assert excinfo.value.slug == "wix-test"
    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)


def test_honest_user_agent_on_every_request() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    make_adapter(shop).fetch("modulo")

    assert len(shop.requests) >= 3  # robots + dynamicmodel + graphql
    for request in shop.requests:
        assert request.headers["User-Agent"] == USER_AGENT


def test_courtesy_delay_between_successive_requests() -> None:
    shop = FakeWixShop(
        pages=[fixture_text("wix_graphql_page1.json"), fixture_text("wix_graphql_page2.json")]
    )
    sleeps: list[float] = []
    make_adapter(shop, per_page=2, sleeps=sleeps).fetch("modulo")

    # 4 requests (robots + dynamicmodel + 2 pages) -> at least 3 pauses >= 1s.
    assert len(shop.requests) == 4
    assert len([s for s in sleeps if s >= 1.0]) >= 3


def test_context_manager_closes_client() -> None:
    shop = FakeWixShop(pages=[fixture_text("wix_graphql_happy.json")])
    with make_adapter(shop) as adapter:
        adapter.fetch("modulo")
        assert not adapter._http._client.is_closed
    assert adapter._http._client.is_closed
