"""Adapter for Wix Stores shops via the storefront GraphQL catalog API.

Wix storefronts expose their catalog through
``/_api/wix-ecommerce-storefront-web/api`` — the same GraphQL endpoint the
shop's own frontend queries for every visitor. It requires the site's public
storefront token, which the site hands to every visitor at
``/_api/v2/dynamicmodel``; no credentials or browser impersonation involved.

Prices arrive as plain decimal JSON numbers (not minor units). Bodies are
parsed with ``parse_float=Decimal`` so a price never materializes as a
Python float.
"""

import json
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

# Platform-wide Wix constants (identical for every Wix Stores site).
_STORES_APP_ID = "1380b703-ce81-ff05-f115-39571d94dfcd"
_ALL_PRODUCTS_CATEGORY = "00000000-000000-000000-000000000001"
_DYNAMICMODEL_PATH = "/_api/v2/dynamicmodel"
_GRAPHQL_PATH = "/_api/wix-ecommerce-storefront-web/api"

_PRODUCTS_QUERY = """\
query getFilteredProducts($mainCollectionId: String!, $filters: ProductFilters, \
$offset: Int, $limit: Int) {
  catalog {
    category(categoryId: $mainCollectionId) {
      productsWithMetaData(filters: $filters, limit: $limit, offset: $offset, onlyVisible: true) {
        totalCount
        list { id name price currency urlPart isInStock }
      }
    }
  }
}"""


class WixAdapter:
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
        self._token: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WixAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, query: str) -> list[NormalizedListing]:
        """Return normalized listings for one query; raise AdapterError if the source fails."""
        self.skipped = 0
        graphql_url = f"{self._http.base_url}{_GRAPHQL_PATH}"
        for path, url in (
            (_DYNAMICMODEL_PATH, f"{self._http.base_url}{_DYNAMICMODEL_PATH}"),
            (_GRAPHQL_PATH, graphql_url),
        ):
            if not self._http.allows(url):
                raise AdapterError(
                    f"{self.source.slug}: robots.txt disallows {path}; "
                    "skipping per courtesy policy",
                    slug=self.source.slug,
                )

        token = self._storefront_token()
        listings: list[NormalizedListing] = []
        offset = 0
        while True:
            products, total = self._fetch_page(graphql_url, token, query, offset)
            for product in products:
                listing = self._parse_product(product)
                if listing is None:
                    self.skipped += 1
                else:
                    listings.append(listing)
            # Advance by items actually returned, not the requested page size:
            # a server that clamps the page size would otherwise skip listings
            # silently. An honest full-page server behaves identically.
            offset += len(products)
            if offset >= total or not products:
                return listings
            if offset >= MAX_PAGES * self._per_page:
                # Same posture as the WooCommerce adapter: partial data would
                # silently understate the market, so a runaway server fails.
                raise AdapterError(
                    f"{self.source.slug}: more than {MAX_PAGES} pages for one query; "
                    "server may be ignoring pagination",
                    slug=self.source.slug,
                )

    def _storefront_token(self) -> str:
        """The public storefront token the site serves every visitor."""
        if self._token is None:
            url = f"{self._http.base_url}{_DYNAMICMODEL_PATH}"
            response = self._http.get(url)
            if response.status_code != 200:
                raise AdapterError(
                    f"{self.source.slug}: dynamicmodel returned HTTP {response.status_code}",
                    slug=self.source.slug,
                )
            try:
                token = json.loads(response.text)["apps"][_STORES_APP_ID]["instance"]
            except (ValueError, KeyError, TypeError) as exc:
                raise AdapterError(
                    f"{self.source.slug}: no storefront token in dynamicmodel response",
                    slug=self.source.slug,
                ) from exc
            if not isinstance(token, str) or not token:
                raise AdapterError(
                    f"{self.source.slug}: no storefront token in dynamicmodel response",
                    slug=self.source.slug,
                )
            self._token = token
        return self._token

    def _fetch_page(self, url: str, token: str, query: str, offset: int) -> tuple[list, int]:
        body = {
            "query": _PRODUCTS_QUERY,
            "variables": {
                "mainCollectionId": _ALL_PRODUCTS_CATEGORY,
                "offset": offset,
                "limit": self._per_page,
                "filters": {"term": {"field": "name", "op": "CONTAINS", "values": [query]}},
            },
            "source": "WixStoresWebClient",
            "operationName": "getFilteredProducts",
        }
        response = self._http.post(url, json=body, headers={"Authorization": token})
        if response.status_code != 200:
            raise AdapterError(
                f"{self.source.slug}: storefront API returned HTTP {response.status_code}",
                slug=self.source.slug,
            )
        try:
            # parse_float=Decimal: prices come as JSON decimals; keep them exact.
            data = json.loads(response.text, parse_float=Decimal)
        except ValueError as exc:
            raise AdapterError(
                f"{self.source.slug}: storefront API returned non-JSON body",
                slug=self.source.slug,
            ) from exc
        if not isinstance(data, dict):
            raise AdapterError(
                f"{self.source.slug}: storefront API returned unexpected JSON shape",
                slug=self.source.slug,
            )
        if data.get("errors"):
            raise AdapterError(
                f"{self.source.slug}: storefront GraphQL returned errors",
                slug=self.source.slug,
            )
        try:
            meta = data["data"]["catalog"]["category"]["productsWithMetaData"]
            products, total = meta["list"], meta["totalCount"]
        except (KeyError, TypeError) as exc:
            raise AdapterError(
                f"{self.source.slug}: storefront API returned unexpected JSON shape",
                slug=self.source.slug,
            ) from exc
        if not isinstance(products, list) or not isinstance(total, int):
            raise AdapterError(
                f"{self.source.slug}: storefront API returned unexpected JSON shape",
                slug=self.source.slug,
            )
        return products, total

    def _parse_product(self, product: object) -> NormalizedListing | None:
        # Fields are type-checked, never str()-coerced, same as woocommerce.
        try:
            if not isinstance(product, dict):
                raise TypeError("product is not an object")
            product_id = product["id"]
            title = product["name"]
            url_part = product["urlPart"]
            currency = product["currency"]
            price = product["price"]
            if (
                not isinstance(product_id, str)
                or not isinstance(title, str)
                or not isinstance(url_part, str)
                or not isinstance(currency, str)
            ):
                raise TypeError("wrong-typed id/name/urlPart/currency")
            # parse_float=Decimal means a price is Decimal or int, never float.
            if isinstance(price, bool) or not isinstance(price, Decimal | int):
                raise TypeError("wrong-typed price")
            return NormalizedListing(
                source_slug=self.source.slug,
                external_id=product_id,
                title=title,
                price=price if isinstance(price, Decimal) else Decimal(price),
                currency=currency,
                condition=Condition.UNKNOWN,
                url=f"{self._http.base_url}/product-page/{url_part}",
                fetched_at=date.today(),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            logger.warning("%s: skipping malformed product: %.120r", self.source.slug, product)
            return None
