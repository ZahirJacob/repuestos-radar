"""Adapter for WooCommerce shops via the public Store API.

The Store API (``/wp-json/wc/store/v1/products``) is the same unauthenticated
JSON endpoint the storefront's own frontend uses; reading it is equivalent to
loading the shop's search page, minus the HTML. Prices arrive in minor units
with an explicit ``currency_minor_unit`` — converted with Decimal scaling,
never float math.
"""

import logging
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.adapters.politeness import MAX_PAGES, PoliteHttpClient
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.sources import Source

logger = logging.getLogger(__name__)

_PRODUCTS_PATH = "/wp-json/wc/store/v1/products"


class WooCommerceAdapter:
    """One instance per shop, configured from its `Source` entry."""

    def __init__(
        self,
        source: Source,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], object] = time.sleep,
        per_page: int = 100,
    ) -> None:
        self.source = source
        self.skipped = 0
        self._per_page = per_page
        self._http = PoliteHttpClient(source.slug, source.url, transport=transport, sleep=sleep)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WooCommerceAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, query: str) -> list[NormalizedListing]:
        """Return normalized listings for one query; raise AdapterError if the source fails."""
        self.skipped = 0
        products_url = f"{self._http.base_url}{_PRODUCTS_PATH}"
        if not self._http.allows(products_url):
            raise AdapterError(
                f"{self.source.slug}: robots.txt disallows {_PRODUCTS_PATH}; "
                "skipping per courtesy policy",
                slug=self.source.slug,
            )

        listings: list[NormalizedListing] = []
        page = 1
        while True:
            products, total_pages = self._fetch_page(products_url, query, page)
            for product in products:
                listing = self._parse_product(product)
                if listing is None:
                    self.skipped += 1
                else:
                    listings.append(listing)
            if len(products) < self._per_page:
                return listings
            if total_pages is not None and page >= total_pages:
                return listings
            page += 1
            if page > MAX_PAGES:
                # A server that keeps serving full pages past the cap is
                # malfunctioning (e.g. ignoring the page param); partial data
                # would silently understate the market, so fail the run.
                raise AdapterError(
                    f"{self.source.slug}: more than {MAX_PAGES} pages for one query; "
                    "server may be ignoring pagination",
                    slug=self.source.slug,
                )

    def _fetch_page(self, url: str, query: str, page: int) -> tuple[list, int | None]:
        params = {"search": query, "per_page": self._per_page, "page": page}
        response = self._http.get(url, params=params)
        if response.status_code != 200:
            raise AdapterError(
                f"{self.source.slug}: Store API returned HTTP {response.status_code} for {url}",
                slug=self.source.slug,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"{self.source.slug}: Store API returned non-JSON body",
                slug=self.source.slug,
            ) from exc
        if not isinstance(data, list):
            raise AdapterError(
                f"{self.source.slug}: Store API returned unexpected JSON shape",
                slug=self.source.slug,
            )
        try:
            total_pages = int(response.headers["X-WP-TotalPages"])
        except (KeyError, ValueError):
            total_pages = None
        return data, total_pages

    def _parse_product(self, product: object) -> NormalizedListing | None:
        # Fields are type-checked, never str()-coerced: a null name must be a
        # skipped product, not a listing titled "None".
        try:
            if not isinstance(product, dict):
                raise TypeError("product is not an object")
            product_id = product["id"]
            title = product["name"]
            permalink = product["permalink"]
            prices = product["prices"]
            currency = prices["currency_code"]
            if (
                isinstance(product_id, bool)
                or not isinstance(product_id, int | str)
                or not isinstance(title, str)
                or not isinstance(permalink, str)
                or not isinstance(currency, str)
            ):
                raise TypeError("wrong-typed id/name/permalink/currency_code")
            minor_units = Decimal(str(prices["price"]))
            price = minor_units.scaleb(-int(prices["currency_minor_unit"]))
            return NormalizedListing(
                source_slug=self.source.slug,
                external_id=str(product_id),
                title=title,
                price=price,
                currency=currency,
                condition=Condition.UNKNOWN,
                url=permalink,
                fetched_at=date.today(),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            logger.warning("%s: skipping malformed product: %.120r", self.source.slug, product)
            return None
