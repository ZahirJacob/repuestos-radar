"""Adapter for Tiendanube-hosted storefronts.

Tiendanube offers no public JSON front door (``/products.json`` 404s and the
platform API is OAuth-only), and search paths are robots-disallowed
platform-wide (``Disallow: /search/``) — so this adapter NEVER queries a
search endpoint. Instead it crawls the store's server-rendered category
pages once per run: every category page embeds one schema.org/Product
JSON-LD block per product (theme-independent), carrying name, sku, the
offer's price/currency/availability, and the product URL.

Category URLs are discovered from the sitemap advertised in robots.txt
(hosted on the platform CDN), falling back to same-host homepage links when
no sitemap is advertised. Categories are crawled broad-first (shallow paths
first), except that categories matching the source's ``priority_categories``
slugs are crawled before everything else, in the configured order — so the
categories the client actually cares about are covered even when the page
budget cuts the crawl short. Each category is paginated with ``?page=N``;
past-the-end pages return HTTP 200 with zero products, so the crawl stops on
the first empty page — and a page identical to the previous one also ends
the category, because some themes serve a "recommended products" fallback
for every page of an effectively empty category. The whole crawl is bounded
by the source's
``max_catalog_pages`` (``MAX_CATALOG_PAGES`` when unset) page fetches;
hitting the cap logs a warning and returns the partial catalog rather than
failing the source. ``pages_fetched`` and ``budget_exhausted`` report the
crawl's coverage for the run report.

The crawled catalog is cached on the adapter instance — the ingestion runner
reuses one adapter per source across all tracked items, so the store is
crawled once per run — and ``fetch(query)`` filters the cache locally with
case/accent-insensitive token containment. The downstream relevance filter
does the real classification.

Out-of-stock products are excluded: their listed price is not a price the
client can actually buy at today. external_id is the product SKU when the
store provides one, else the product URL slug (stable per product either
way; SKUs are preferred because a store can re-slug a product page).
"""

import gzip
import io
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.adapters.politeness import MAX_RESPONSE_BYTES, PoliteHttpClient
from repuestos_radar.relevance import normalize
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.sources import Source

logger = logging.getLogger(__name__)

MAX_CATALOG_PAGES = 80
"""Default total category-page fetches allowed per crawl (~80s at the 1s
courtesy delay); a source's ``max_catalog_pages`` overrides it."""

_MAX_CANDIDATES = 100
"""Cap on discovered category candidates, defensive against pathological sitemaps."""

MAX_SITEMAP_BYTES = MAX_RESPONSE_BYTES
"""Cap on a sitemap's INFLATED size. The polite client caps what comes over
the wire, but a .xml.gz sitemap is inflated here, and a few KB of gzip can
inflate to gigabytes; past this the sitemap is skipped (homepage fallback)."""


def _gunzip_bounded(data: bytes, limit: int) -> bytes | None:
    """Inflate gzip ``data``, or None when the result would exceed ``limit``
    bytes — without ever holding more than ``limit`` + 1 bytes of output."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as inflater:
        inflated = inflater.read(limit + 1)
    return None if len(inflated) > limit else inflated


_LD_JSON_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.DOTALL)
_HREF_RE = re.compile(r"href=\"([^\"#?]+)")


class TiendanubeAdapter:
    """One instance per store, configured from its `Source` entry."""

    def __init__(
        self,
        source: Source,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self.source = source
        self.skipped = 0
        self.pages_fetched = 0
        """Category pages fetched by the catalog crawl (0 until it runs)."""
        self.budget_exhausted = False
        """True when the crawl hit the page budget and the catalog may be partial."""
        self._http = PoliteHttpClient(source.slug, source.url, transport=transport, sleep=sleep)
        self._catalog: list[NormalizedListing] | None = None
        self._catalog_skipped = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TiendanubeAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, query: str) -> list[NormalizedListing]:
        """Return catalog listings matching one query; crawl at most once per run.

        ``skipped`` reports malformed products for the most recent fetch: the
        crawl's count on the fetch that crawled, 0 on cache hits (no parsing
        happened).
        """
        self.skipped = 0
        if self._catalog is None:
            self._catalog = self._crawl_catalog()
            self.skipped = self._catalog_skipped
        return [listing for listing in self._catalog if _matches(query, listing.title)]

    # --- catalog crawl ------------------------------------------------------

    def _crawl_catalog(self) -> list[NormalizedListing]:
        candidates = self._discover_categories()
        allowed = [url for url in candidates if self._http.allows(url)]
        if not allowed:
            raise AdapterError(
                f"{self.source.slug}: no crawlable category pages "
                "(robots.txt disallows them all); skipping per courtesy policy",
                slug=self.source.slug,
            )

        self._catalog_skipped = 0
        budget = self.source.max_catalog_pages or MAX_CATALOG_PAGES
        listings: list[NormalizedListing] = []
        seen: set[str] = set()
        total_product_blocks = 0
        for category_url in allowed:
            page = 1
            previous_page_ids: set[str] | None = None
            while True:
                if self.pages_fetched >= budget:
                    logger.warning(
                        "%s: catalog page budget (%d pages) reached; catalog may be partial",
                        self.source.slug,
                        budget,
                    )
                    self.budget_exhausted = True
                    break
                response = self._http.get(category_url, params={"page": page})
                self.pages_fetched += 1
                if response.status_code != 200:
                    # A stale sitemap entry (404 etc.) is not a source failure:
                    # skip this category and keep crawling the rest.
                    logger.warning(
                        "%s: HTTP %d for category %s; skipping it",
                        self.source.slug,
                        response.status_code,
                        category_url,
                    )
                    break
                page_listings, product_blocks = self._parse_products(response.text)
                total_product_blocks += product_blocks
                if product_blocks == 0:
                    # Zero Product blocks = past the end (or not a category
                    # page at all). Counted on blocks, not parsed listings, so
                    # a page of only out-of-stock products does not end the
                    # category early.
                    break
                page_ids = {listing.external_id for listing in page_listings}
                if page_ids and page_ids == previous_page_ids:
                    # Some themes serve the same "recommended products"
                    # listing for every ?page=N of an effectively empty
                    # category, so the zero-products stop never fires and one
                    # category would eat the whole page budget. An identical
                    # repeat of the previous page ends the category. (Only
                    # non-empty id sets are compared, so consecutive pages of
                    # out-of-stock products do not trigger this.)
                    logger.warning(
                        "%s: page %d of %s is identical to the previous page "
                        "(theme fallback listing?); ending the category",
                        self.source.slug,
                        page,
                        category_url,
                    )
                    break
                previous_page_ids = page_ids
                for listing in page_listings:
                    if listing.external_id not in seen:
                        seen.add(listing.external_id)
                        listings.append(listing)
                page += 1
            if self.budget_exhausted:
                break
        if total_product_blocks == 0:
            # A real store whose whole crawl shows zero Product JSON-LD is
            # almost certainly markup drift or a parser regression, not an
            # empty store — every fetch would silently return [] otherwise.
            logger.warning(
                "%s: crawl completed with ZERO Product JSON-LD blocks across %d page(s); "
                "platform markup may have changed — the empty catalog is suspect",
                self.source.slug,
                self.pages_fetched,
            )
        return listings

    # --- category discovery -------------------------------------------------

    def _discover_categories(self) -> list[str]:
        """Category page candidates: priority categories first, then broad-first.

        Broad-first (shallow-path) ordering means that if the page budget cuts
        the crawl short, the parent categories — which list their subtree's
        products — have already been covered. Categories matching the source's
        ``priority_categories`` slugs are moved to the very front (in the
        configured order, broad-first within each slug), and survive the
        candidate cap, so the categories the client cares about are crawled
        even on stores where the budget or the cap truncates the rest.
        """
        candidates = self._categories_from_sitemap()
        if not candidates:
            candidates = self._categories_from_homepage()
        ordered = sorted(candidates, key=lambda u: (urlsplit(u).path.count("/"), u))
        return self._prioritized(ordered)[:_MAX_CANDIDATES]

    def _prioritized(self, ordered: list[str]) -> list[str]:
        """Move categories matching ``priority_categories`` to the front.

        A slug matches a category URL when it appears as a contiguous run of
        path segments (so ``celulares`` matches ``/celulares/`` and
        ``/celulares/samsung/``, and ``repuestos/modulos`` works too). A slug
        that matches nothing is harmless but logged: a store re-slugging a
        category should be noticed, not silently ignored.
        """
        if not self.source.priority_categories:
            return ordered
        remaining = list(ordered)
        front: list[str] = []
        for slug in self.source.priority_categories:
            needle = f"/{slug.strip('/')}/"
            matched = [url for url in remaining if needle in _slashed_path(url)]
            if not matched:
                logger.warning(
                    "%s: priority category '%s' matched no discovered category page; "
                    "the store may have renamed or re-slugged it",
                    self.source.slug,
                    slug,
                )
            front.extend(matched)
            remaining = [url for url in remaining if url not in matched]
        return front + remaining

    def _categories_from_sitemap(self) -> set[str]:
        # Sitemap URLs come from the client's cached robots.txt parse, so
        # robots.txt is fetched exactly once per crawl.
        candidates: set[str] = set()
        for sitemap_url in self._http.site_maps():
            if "blog" in sitemap_url:
                continue
            candidates |= self._category_locs(sitemap_url)
        return candidates

    def _category_locs(self, sitemap_url: str) -> set[str]:
        response = self._http.get(sitemap_url)
        if response.status_code != 200:
            return set()
        try:
            inflated = _gunzip_bounded(response.content, MAX_SITEMAP_BYTES)
        except (gzip.BadGzipFile, OSError, EOFError):
            xml = response.text  # served un-gzipped (or transport-decompressed)
        else:
            if inflated is None:
                logger.warning(
                    "%s: sitemap %s is too large once inflated (over %d bytes); skipping it",
                    self.source.slug,
                    sitemap_url,
                    MAX_SITEMAP_BYTES,
                )
                return set()
            xml = inflated.decode("utf-8", errors="replace")
        return {url for url in _LOC_RE.findall(xml) if self._is_category_candidate(url)}

    def _categories_from_homepage(self) -> set[str]:
        response = self._http.get(f"{self._http.base_url}/")
        if response.status_code != 200:
            return set()
        candidates: set[str] = set()
        for href in _HREF_RE.findall(response.text):
            url = f"{self._base_root()}{href}" if href.startswith("/") else href
            if self._is_category_candidate(url):
                candidates.add(url)
        return candidates

    def _base_root(self) -> str:
        split = urlsplit(self._http.base_url)
        return f"{split.scheme}://{split.netloc}"

    def _is_category_candidate(self, url: str) -> bool:
        split = urlsplit(url)
        if f"{split.scheme}://{split.netloc}" != self._base_root():
            return False
        path = split.path
        first_segment = path.strip("/").split("/")[0] if path.strip("/") else ""
        # /productos/... are product detail pages, not category listings.
        return bool(first_segment) and first_segment != "productos" and "sitemap" not in path

    # --- JSON-LD parsing ----------------------------------------------------

    def _parse_products(self, html: str) -> tuple[list[NormalizedListing], int]:
        """Parse one page; return (listings, count of Product blocks seen)."""
        listings: list[NormalizedListing] = []
        product_blocks = 0
        for raw in _LD_JSON_RE.findall(html):
            try:
                data = json.loads(raw)
            except ValueError:
                # Real stores ship broken non-product blocks too (e.g. a
                # WebPage with a trailing comma); only count blocks that
                # were meant to be products.
                if '"Product"' in raw:
                    product_blocks += 1
                    self._catalog_skipped += 1
                    logger.warning(
                        "%s: skipping unparseable Product JSON-LD block", self.source.slug
                    )
                continue
            if not (isinstance(data, dict) and data.get("@type") == "Product"):
                continue
            product_blocks += 1
            listing = self._parse_product(data)
            if listing is not None:
                listings.append(listing)
        return listings, product_blocks

    def _parse_product(self, product: dict) -> NormalizedListing | None:
        # Fields are type-checked, never coerced: a missing name must be a
        # skipped product, not a listing titled "None".
        try:
            offers = product["offers"]
            if not isinstance(offers, dict):
                raise TypeError("offers is not an object")
            availability = offers.get("availability", "")
            if isinstance(availability, str) and availability.endswith("OutOfStock"):
                return None  # not purchasable today; deliberately not "malformed"
            title = product["name"]
            currency = offers["priceCurrency"]
            # mainEntityOfPage is legally either an object carrying @id or a
            # plain URL string; both shapes appear in the wild.
            main_entity = product.get("mainEntityOfPage")
            fallback_url = main_entity.get("@id") if isinstance(main_entity, dict) else main_entity
            url = offers.get("url") or fallback_url
            if not isinstance(title, str) or not isinstance(currency, str):
                raise TypeError("wrong-typed name/priceCurrency")
            if not isinstance(url, str) or not url:
                raise TypeError("missing product URL")
            price = Decimal(str(offers["price"]))
            sku = product.get("sku")
            if isinstance(sku, str) and sku.strip():
                external_id = sku.strip()
            else:
                external_id = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            return NormalizedListing(
                source_slug=self.source.slug,
                external_id=external_id,
                title=title,
                price=price,
                currency=currency,
                condition=Condition.UNKNOWN,
                url=url,
                fetched_at=date.today(),
            )
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
            # AttributeError included defensively: one odd product shape must
            # be a skipped product, never a source-wide failure.
            self._catalog_skipped += 1
            logger.warning("%s: skipping malformed product: %.120r", self.source.slug, product)
            return None


def _slashed_path(url: str) -> str:
    """URL path with leading and trailing slashes guaranteed, for segment matching."""
    path = urlsplit(url).path
    if not path.startswith("/"):
        path = f"/{path}"
    if not path.endswith("/"):
        path = f"{path}/"
    return path


def _matches(query: str, title: str) -> bool:
    """Case/accent-insensitive containment of every query token in the title.

    This only shortlists candidates the way a storefront search would; the
    relevance filter downstream does the real classification.
    """
    title_norm = normalize(title)
    tokens = normalize(query).split()
    return bool(tokens) and all(token in title_norm for token in tokens)
