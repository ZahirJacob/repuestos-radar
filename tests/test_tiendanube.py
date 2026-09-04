"""Tests for the Tiendanube category-crawl adapter. All offline via httpx.MockTransport."""

import gzip
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

import repuestos_radar.adapters.tiendanube
from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.adapters.tiendanube import TiendanubeAdapter
from repuestos_radar.schema import Condition
from repuestos_radar.sources import Source

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://store.example.com.ar"
CDN_SITEMAP_URL = "https://cdn.example.com/stores/001/themes/common/sitemap.xml.gz"

ROBOTS_WITH_SITEMAP = (
    f"User-agent: *\nDisallow: /search/\nDisallow: /comprar/\n\nSitemap: {CDN_SITEMAP_URL}\n"
)
ROBOTS_DISALLOW_ALL = f"User-agent: *\nDisallow: /\n\nSitemap: {CDN_SITEMAP_URL}\n"

SITEMAP_LOCS = [
    f"{BASE_URL}/",
    f"{BASE_URL}/celulares/",
    f"{BASE_URL}/contacto/",
    f"{BASE_URL}/productos/modulo-pantalla-motorola-e13-xt2345/",  # product page: excluded
    "https://other-host.example.com/celulares/",  # foreign host: excluded
]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def sitemap_gz(locs: list[str]) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset>{body}</urlset>'
    return gzip.compress(xml.encode("utf-8"))


def make_source(**overrides) -> Source:
    fields = dict(
        slug="store-test",
        name="Store Test",
        url=BASE_URL,
        platform="tiendanube",
        address="Calle Falsa 123",
        city="Rosario",
        trust_notes="Test store.",
    )
    fields.update(overrides)
    return Source(**fields)


class FakeStore:
    """Serves robots.txt, the CDN sitemap, the homepage, and category pages.

    ``pages`` maps a category path (no trailing slash) to the HTML for its
    successive ?page=N values; pages past the end serve the empty fixture.
    ``always_full`` serves the first page for every page number (the theme-
    fallback trap seen in the wild: an "empty" category serving the same
    recommended products forever). ``always_full_unique`` also serves a full
    page for every page number, but with per-page SKUs — a crawl that only
    the page budget can stop.
    """

    def __init__(
        self,
        pages: dict[str, list[str]],
        robots: str | int | None = ROBOTS_WITH_SITEMAP,
        sitemap_locs: list[str] | None = None,
        home_html: str | None = None,
        always_full: bool = False,
        always_full_unique: bool = False,
    ):
        self.pages = pages
        self.robots = robots
        self.sitemap = sitemap_gz(SITEMAP_LOCS if sitemap_locs is None else sitemap_locs)
        self.home_html = home_html
        self.always_full = always_full
        self.always_full_unique = always_full_unique
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "store.example.com.ar":
            if str(request.url) == CDN_SITEMAP_URL:
                return httpx.Response(200, content=self.sitemap)
            return httpx.Response(404, text="unknown host")
        path = request.url.path.rstrip("/") or "/"
        if path == "/robots.txt":
            if self.robots is None:
                return httpx.Response(404, text="not here")
            if isinstance(self.robots, int):
                return httpx.Response(self.robots, text="robots error")
            return httpx.Response(200, text=self.robots)
        if path == "/":
            return httpx.Response(200, text=self.home_html or fixture("tiendanube_home.html"))
        if path in self.pages:
            page = int(request.url.params.get("page", "1"))
            sequence = self.pages[path]
            if self.always_full_unique:
                unique = sequence[0].replace('"sku": "', f'"sku": "P{page}-')
                unique = unique.replace("/productos/", f"/productos/p{page}-")
                return httpx.Response(200, text=unique)
            if self.always_full:
                return httpx.Response(200, text=sequence[0])
            if page <= len(sequence):
                return httpx.Response(200, text=sequence[page - 1])
            return httpx.Response(200, text=fixture("tiendanube_empty.html"))
        return httpx.Response(404, text="unknown path")

    def category_requests(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if (r.url.path.rstrip("/") or "/") == path]


def make_adapter(store: FakeStore, source: Source | None = None) -> TiendanubeAdapter:
    return TiendanubeAdapter(
        source or make_source(), transport=httpx.MockTransport(store), sleep=lambda _s: None
    )


def two_page_store(**kwargs) -> FakeStore:
    return FakeStore(
        pages={
            "/celulares": [
                fixture("tiendanube_cat_page1.html"),
                fixture("tiendanube_cat_page2.html"),
            ]
        },
        **kwargs,
    )


def test_happy_path_crawls_sitemap_categories_and_parses_jsonld() -> None:
    store = two_page_store()
    adapter = make_adapter(store)

    listings = adapter.fetch("modulo")

    assert [listing.external_id for listing in listings] == [
        "MD-E13-SM",
        "modulo-motorola-g8-power",
    ]
    first = listings[0]
    assert first.source_slug == "store-test"
    assert first.title == "Módulo Pantalla Motorola E13 XT2345 Sin Marco"
    assert first.price == Decimal("16000")
    assert first.currency == "ARS"
    assert first.condition is Condition.UNKNOWN
    assert first.url == f"{BASE_URL}/productos/modulo-pantalla-motorola-e13-xt2345/"
    assert first.fetched_at == date.today()
    # Broken Product block + zero-price product; the store's own malformed
    # WebPage block and the out-of-stock product are NOT counted.
    assert adapter.skipped == 2
    # /search/ is robots-disallowed platform-wide and must never be fetched;
    # /productos/ detail pages and foreign hosts are not crawled either.
    paths = [r.url.path for r in store.requests]
    assert not any(p.startswith("/search") for p in paths)
    assert not any(p.startswith("/productos") for p in paths)
    assert all(r.url.host in {"store.example.com.ar", "cdn.example.com"} for r in store.requests)


def test_robots_txt_is_fetched_exactly_once_per_crawl() -> None:
    store = two_page_store()
    make_adapter(store).fetch("modulo")

    robots_requests = [r for r in store.requests if r.url.path == "/robots.txt"]
    assert len(robots_requests) == 1  # shared between sitemap discovery and allows()


def test_plain_string_main_entity_is_used_as_url_fallback() -> None:
    """A Product with no offers.url and mainEntityOfPage as a plain URL string
    (legal schema.org) must be parsed — and must never fail the whole source."""
    adapter = make_adapter(two_page_store())

    listings = adapter.fetch("cargador turbopower")

    (listing,) = listings
    assert listing.external_id == "cargador-motorola-turbopower"
    assert listing.url == f"{BASE_URL}/productos/cargador-motorola-turbopower/"
    assert listing.price == Decimal("18000")
    assert adapter.skipped == 2  # the odd-but-legal shape is not "malformed"


def test_zero_product_crawl_logs_a_loud_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A completed crawl that parses zero Product blocks anywhere is almost
    certainly markup drift, not an empty store — it must not stay silent."""
    store = FakeStore(pages={"/celulares": [fixture("tiendanube_empty.html")]})
    adapter = make_adapter(store)

    with caplog.at_level(logging.WARNING):
        listings = adapter.fetch("modulo")

    assert listings == []
    assert any("ZERO Product JSON-LD" in record.message for record in caplog.records)


def test_sku_missing_falls_back_to_url_slug() -> None:
    adapter = make_adapter(two_page_store())
    listings = adapter.fetch("g8 power")
    assert [listing.external_id for listing in listings] == ["modulo-motorola-g8-power"]


def test_out_of_stock_products_are_excluded_but_not_counted_as_malformed() -> None:
    adapter = make_adapter(two_page_store())
    listings = adapter.fetch("modulo motorola e7")
    assert listings == []  # in the fixture but OutOfStock
    assert adapter.skipped == 2


def test_local_filter_is_case_and_accent_insensitive() -> None:
    adapter = make_adapter(two_page_store())

    accented_query = adapter.fetch("módulo e13")  # accented query, mixed title
    unaccented_query = adapter.fetch("bateria motorola")  # unaccented query, accented title

    assert [listing.external_id for listing in accented_query] == ["MD-E13-SM"]
    assert [listing.external_id for listing in unaccented_query] == ["BAT-G6-BL270"]


def test_pagination_stops_on_first_empty_page() -> None:
    store = two_page_store()
    make_adapter(store).fetch("modulo")

    pages = [r.url.params["page"] for r in store.category_requests("/celulares")]
    assert pages == ["1", "2", "3"]  # page 3 has zero Product blocks -> stop


def test_catalog_is_crawled_once_and_reused_across_fetches() -> None:
    store = two_page_store()
    adapter = make_adapter(store)

    first = adapter.fetch("modulo")
    requests_after_first = len(store.requests)
    second = adapter.fetch("bateria")

    assert len(store.requests) == requests_after_first  # cache hit: zero new requests
    assert first and second
    assert adapter.skipped == 0  # the cached fetch parsed nothing


def test_non_category_sitemap_entries_cost_one_request_each() -> None:
    # /contacto/ is in the sitemap; it serves no Product JSON-LD, so the
    # crawl probes it once and moves on.
    store = two_page_store()
    make_adapter(store).fetch("modulo")

    contacto = store.category_requests("/contacto")
    assert len(contacto) == 1


def test_robots_disallowing_every_category_raises() -> None:
    store = two_page_store(robots=ROBOTS_DISALLOW_ALL)
    adapter = make_adapter(store)

    with pytest.raises(AdapterError, match="no crawlable category"):
        adapter.fetch("modulo")

    assert store.category_requests("/celulares") == []


def test_unreachable_robots_txt_raises() -> None:
    store = two_page_store(robots=500)
    with pytest.raises(AdapterError):
        make_adapter(store).fetch("modulo")


def test_page_budget_caps_a_runaway_crawl(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(repuestos_radar.adapters.tiendanube, "MAX_CATALOG_PAGES", 3)
    store = two_page_store(always_full_unique=True)
    adapter = make_adapter(store)

    with caplog.at_level(logging.WARNING):
        listings = adapter.fetch("modulo")

    crawled = len(store.category_requests("/celulares")) + len(store.category_requests("/contacto"))
    assert crawled == 3
    assert listings  # partial catalog is returned, the source is not failed
    assert any("page budget" in record.message for record in caplog.records)


def test_homepage_fallback_when_no_sitemap_is_advertised() -> None:
    store = two_page_store(robots=None)  # 404 robots: no sitemap, allow-all

    listings = make_adapter(store).fetch("modulo")

    assert [listing.external_id for listing in listings] == [
        "MD-E13-SM",
        "modulo-motorola-g8-power",
    ]
    # Categories came from the homepage nav; /productos/ and foreign links skipped.
    assert len(store.category_requests("/")) >= 1
    assert not any(r.url.path.startswith("/productos") for r in store.requests)


def test_sitemap_that_inflates_past_the_cap_is_skipped_not_inflated(monkeypatch, caplog) -> None:
    """A gzip bomb served as the sitemap: the wire size passes the client's
    cap, so the decompression itself must be bounded. The sitemap is dropped
    with a warning and the crawl falls back to the homepage categories."""
    store = two_page_store()
    store.sitemap = gzip.compress(b"0" * 200_000)  # ~200 bytes on the wire
    monkeypatch.setattr(repuestos_radar.adapters.tiendanube, "MAX_SITEMAP_BYTES", 100_000)

    with caplog.at_level(logging.WARNING):
        listings = make_adapter(store).fetch("modulo")

    assert "sitemap" in caplog.text and "too large" in caplog.text
    assert [listing.external_id for listing in listings] == [
        "MD-E13-SM",
        "modulo-motorola-g8-power",
    ]
    assert len(store.category_requests("/")) >= 1  # homepage fallback ran


# --- per-source crawl tuning -------------------------------------------------

MULTI_CAT_LOCS = [
    f"{BASE_URL}/accesorios/",
    f"{BASE_URL}/celulares/",
    f"{BASE_URL}/celulares/samsung/",
    f"{BASE_URL}/repuestos/modulos/",
]


def multi_cat_store(**kwargs) -> FakeStore:
    page = fixture("tiendanube_cat_page1.html")
    empty = fixture("tiendanube_empty.html")
    return FakeStore(
        pages={
            "/accesorios": [page],
            "/celulares": [page],
            "/celulares/samsung": [empty],
            "/repuestos/modulos": [page],
        },
        sitemap_locs=MULTI_CAT_LOCS,
        **kwargs,
    )


def first_category_paths(store: FakeStore) -> list[str]:
    """Distinct category paths in the order their first page was requested."""
    seen: list[str] = []
    for request in store.requests:
        path = request.url.path.rstrip("/") or "/"
        if path in {"/robots.txt", "/"} or request.url.host != "store.example.com.ar":
            continue
        if path not in seen:
            seen.append(path)
    return seen


def test_priority_categories_are_crawled_first_in_configured_order() -> None:
    source = make_source(priority_categories=("repuestos/modulos", "celulares"))
    store = multi_cat_store()

    make_adapter(store, source).fetch("modulo")

    # The two priority slugs first (configured order, subtree matches included,
    # broad-first within a slug), then the rest broad-first as before.
    assert first_category_paths(store) == [
        "/repuestos/modulos",
        "/celulares",
        "/celulares/samsung",
        "/accesorios",
    ]


def test_without_priority_categories_ordering_is_broad_first() -> None:
    store = multi_cat_store()

    make_adapter(store).fetch("modulo")

    assert first_category_paths(store) == [
        "/accesorios",
        "/celulares",
        "/celulares/samsung",
        "/repuestos/modulos",
    ]


def test_priority_slug_matching_nothing_logs_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_source(priority_categories=("no-such-category",))
    store = multi_cat_store()

    with caplog.at_level(logging.WARNING):
        listings = make_adapter(store, source).fetch("modulo")

    assert listings  # harmless: the crawl proceeds broad-first
    assert any("no-such-category" in record.message for record in caplog.records)


def test_priority_categories_survive_the_candidate_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # /repuestos/modulos/ sorts last broad-first; with only 2 candidate slots
    # it must still be crawled because it is configured as a priority.
    monkeypatch.setattr(repuestos_radar.adapters.tiendanube, "_MAX_CANDIDATES", 2)
    source = make_source(priority_categories=("repuestos/modulos",))
    store = multi_cat_store()

    make_adapter(store, source).fetch("modulo")

    assert first_category_paths(store) == ["/repuestos/modulos", "/accesorios"]


def test_max_catalog_pages_overrides_the_default_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_source(max_catalog_pages=3)
    store = two_page_store(always_full_unique=True)
    adapter = make_adapter(store, source)

    with caplog.at_level(logging.WARNING):
        listings = adapter.fetch("modulo")

    crawled = len(store.category_requests("/celulares")) + len(store.category_requests("/contacto"))
    assert crawled == 3
    assert listings
    assert any("(3 pages)" in record.message for record in caplog.records)


def test_identical_repeated_page_ends_the_category_not_the_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Some themes serve the same 'recommended products' listing for every
    ?page=N of an effectively empty category (seen live on onestore) — the
    zero-products stop never fires and one such category would eat the whole
    page budget. A page identical to the previous one ends the category."""
    store = two_page_store(always_full=True)
    adapter = make_adapter(store)

    with caplog.at_level(logging.WARNING):
        listings = adapter.fetch("modulo")

    # /celulares pages 1+2 (identical -> stop) + the /contacto probe.
    assert len(store.category_requests("/celulares")) == 2
    assert adapter.pages_fetched == 3
    assert adapter.budget_exhausted is False
    assert listings  # page 1's products are still in the catalog
    assert any("identical" in record.message for record in caplog.records)


def test_default_budget_is_unchanged() -> None:
    assert repuestos_radar.adapters.tiendanube.MAX_CATALOG_PAGES == 80


def test_full_crawl_reports_pages_fetched_and_no_exhaustion() -> None:
    store = two_page_store()
    adapter = make_adapter(store)

    assert adapter.pages_fetched == 0
    assert adapter.budget_exhausted is False

    adapter.fetch("modulo")

    # /celulares pages 1-3 + one probe of /contacto = 4 category page fetches.
    assert adapter.pages_fetched == 4
    assert adapter.budget_exhausted is False


def test_exhausted_budget_is_reported_on_the_adapter() -> None:
    source = make_source(max_catalog_pages=3)
    adapter = make_adapter(two_page_store(always_full_unique=True), source)

    adapter.fetch("modulo")

    assert adapter.pages_fetched == 3
    assert adapter.budget_exhausted is True


def test_crawl_attributes_survive_cached_fetches() -> None:
    adapter = make_adapter(two_page_store())

    adapter.fetch("modulo")
    adapter.fetch("bateria")  # cache hit: no new requests

    assert adapter.pages_fetched == 4
    assert adapter.budget_exhausted is False


def test_context_manager_closes_client() -> None:
    adapter = make_adapter(two_page_store())
    with adapter as entered:
        assert entered is adapter
    assert adapter._http._client.is_closed
