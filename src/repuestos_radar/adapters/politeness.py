"""Shared polite HTTP machinery for adapters.

Implements the courtesy rules from :mod:`repuestos_radar.adapters.base` once,
so every adapter gets identical behavior: honest user-agent, 15s timeout,
>=1s courtesy delay between successive requests, bounded retries with
exponential backoff on transient failures only (network errors, 5xx; a 4xx
is final), and a cached robots.txt check where unreachable means disallow.

It also caps what a store can push back at us: a response body over
``MAX_RESPONSE_BYTES`` (measured after transport decompression) and a
redirect chain deeper than ``_MAX_REDIRECTS`` are both given up as an
:class:`AdapterError`. The quick search runs adapters inside the dashboard
process, so a misbehaving or compromised store must not be able to exhaust
its memory.
"""

import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from repuestos_radar.adapters.base import USER_AGENT, AdapterError

_TIMEOUT_SECONDS = 15.0
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 2.0
_COURTESY_DELAY_SECONDS = 1.0
_MAX_REDIRECTS = 5
_READ_CHUNK_BYTES = 64 * 1024

MAX_PAGES = 30
"""Shared pagination cap: a server still serving full pages past this is malfunctioning."""

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
"""Largest response body an adapter will hold (the biggest legitimate page we
see — a full Tiendanube sitemap or a 100-product Store API page — is well
under 2 MB)."""


class ResponseTooLarge(AdapterError):
    """A response body went past the size cap; final, never retried."""


class PoliteHttpClient:
    """httpx.Client wrapper enforcing the courtesy rules for one source host."""

    def __init__(
        self,
        slug: str,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], object] = time.sleep,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self.slug = slug
        self.base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._max_response_bytes = max_response_bytes
        self._robots: RobotFileParser | None = None
        self._made_a_request = False
        self._client = httpx.Client(
            transport=transport,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        return self._request("GET", url, params=params)

    def post(self, url: str, *, json: object = None, headers: dict | None = None) -> httpx.Response:
        return self._request("POST", url, json=json, headers=headers)

    def allows(self, url: str) -> bool:
        """robots.txt check, fetched once and cached for the client's lifetime.

        robots.txt lives at the HOST root (RFC 9309), not under the store's
        base path — so a shop installed in a subdirectory (e.g. a WordPress
        under /tienda/) is still governed by the site-wide rules.
        """
        return self._load_robots().can_fetch(USER_AGENT, url)

    def site_maps(self) -> list[str]:
        """Sitemap URLs advertised by robots.txt (empty when none).

        Served from the same cached robots.txt parse that `allows` uses, so
        asking for sitemaps costs no extra request.
        """
        return list(self._load_robots().site_maps() or [])

    def _load_robots(self) -> RobotFileParser:
        if self._robots is None:
            parser = RobotFileParser()
            split = urlsplit(self.base_url)
            try:
                response = self.get(f"{split.scheme}://{split.netloc}/robots.txt")
            except AdapterError as exc:
                # RFC 9309 2.3.1.4: robots.txt unreachable (5xx / network
                # failure after retries) means complete disallow — which is
                # also our courtesy posture. A 4xx (no robots.txt) is allow.
                raise AdapterError(
                    f"{self.slug}: robots.txt unreachable; treating as disallow for this run",
                    slug=self.slug,
                ) from exc
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            self._robots = parser
        return self._robots

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: object = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        """One request with courtesy delay, bounded retries, and exponential backoff.

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
                response = self._bounded_request(
                    method, url, params=params, json=json, headers=headers
                )
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
            f"{self.slug}: giving up on {url} after {_MAX_RETRIES + 1} attempts ({failure})",
            slug=self.slug,
        ) from last_exc

    def _bounded_request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None,
        json: object,
        headers: dict | None,
    ) -> httpx.Response:
        """One request, streamed, refusing a body past the size cap.

        The body is read in chunks after transport decompression, so the cap
        is on what we would actually hold. The result is rebuilt as a plain
        in-memory response (same status, headers, request) so callers keep
        using ``.text`` / ``.json()`` / ``.headers`` as before; the
        ``Content-Encoding``/``Content-Length`` headers are dropped because
        the content they describe is already decoded.
        """
        with self._client.stream(
            method, url, params=params, json=json, headers=headers
        ) as streamed:
            chunks: list[bytes] = []
            size = 0
            for chunk in streamed.iter_bytes(_READ_CHUNK_BYTES):
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise ResponseTooLarge(
                        f"{self.slug}: response from {url} is too large "
                        f"(over {self._max_response_bytes} bytes); giving up on it",
                        slug=self.slug,
                    )
                chunks.append(chunk)
            response_headers = streamed.headers.copy()
            for name in ("content-encoding", "content-length"):
                response_headers.pop(name, None)
            return httpx.Response(
                streamed.status_code,
                headers=response_headers,
                content=b"".join(chunks),
                request=streamed.request,
            )
