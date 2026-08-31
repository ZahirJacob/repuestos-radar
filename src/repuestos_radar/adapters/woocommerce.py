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
from urllib.robotparser import RobotFileParser

import httpx

from repuestos_radar.adapters.base import USER_AGENT, AdapterError
from repuestos_radar.schema import Condition, NormalizedListing
from repuestos_radar.sources import Source

logger = logging.getLogger(__name__)

_PRODUCTS_PATH = "/wp-json/wc/store/v1/products"
_TIMEOUT_SECONDS = 15.0
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 2.0
_COURTESY_DELAY_SECONDS = 1.0
_MAX_PAGES = 30


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
        self._base_url = source.url.rstrip("/")
        self._per_page = per_page
        self._sleep = sleep
        self._robots: RobotFileParser | None = None
        self._made_a_request = False
        self._client = httpx.Client(
            transport=transport,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WooCommerceAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, query: str) -> list[NormalizedListing]:
        """Return normalized listings for one query; raise AdapterError if the source fails."""
        self.skipped = 0
        products_url = f"{self._base_url}{_PRODUCTS_PATH}"
        if not self._robots_allow(products_url):
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
            if page > _MAX_PAGES:
                # A server that keeps serving full pages past the cap is
                # malfunctioning (e.g. ignoring the page param); partial data
                # would silently understate the market, so fail the run.
                raise AdapterError(
                    f"{self.source.slug}: more than {_MAX_PAGES} pages for one query; "
                    "server may be ignoring pagination",
                    slug=self.source.slug,
                )

    def _fetch_page(self, url: str, query: str, page: int) -> tuple[list, int | None]:
        params = {"search": query, "per_page": self._per_page, "page": page}
        response = self._get(url, params=params)
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

    def _robots_allow(self, url: str) -> bool:
        if self._robots is None:
            parser = RobotFileParser()
            try:
                response = self._get(f"{self._base_url}/robots.txt")
            except AdapterError as exc:
                # RFC 9309 2.3.1.4: robots.txt unreachable (5xx / network
                # failure after retries) means complete disallow — which is
                # also our courtesy posture. A 4xx (no robots.txt) is allow.
                raise AdapterError(
                    f"{self.source.slug}: robots.txt unreachable; "
                    "treating as disallow for this run",
                    slug=self.source.slug,
                ) from exc
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            self._robots = parser
        return self._robots.can_fetch(USER_AGENT, url)

    def _get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        """One GET with courtesy delay, bounded retries, and exponential backoff.

        Only transient failures (network errors, 5xx) are retried. A 4xx is
        final: in particular, a site rejecting our honest user-agent is an
        AdapterError, never a reason to retry with a different one.
        """
        failure = ""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            if attempt:
                self._sleep(_BACKOFF_BASE_SECONDS**attempt)
            if self._made_a_request:
                self._sleep(_COURTESY_DELAY_SECONDS)
            self._made_a_request = True
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                last_exc = exc
                continue
            if response.status_code >= 500:
                failure = f"HTTP {response.status_code}"
                last_exc = httpx.HTTPStatusError(
                    failure, request=response.request, response=response
                )
                continue
            return response
        raise AdapterError(
            f"{self.source.slug}: giving up on {url} after {_MAX_RETRIES + 1} attempts ({failure})",
            slug=self.source.slug,
        ) from last_exc
