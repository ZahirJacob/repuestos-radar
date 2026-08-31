"""Shared polite HTTP machinery for adapters.

Implements the courtesy rules from :mod:`repuestos_radar.adapters.base` once,
so every adapter gets identical behavior: honest user-agent, 15s timeout,
>=1s courtesy delay between successive requests, bounded retries with
exponential backoff on transient failures only (network errors, 5xx; a 4xx
is final), and a cached robots.txt check where unreachable means disallow.
"""

import time
from collections.abc import Callable
from urllib.robotparser import RobotFileParser

import httpx

from repuestos_radar.adapters.base import USER_AGENT, AdapterError

_TIMEOUT_SECONDS = 15.0
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 2.0
_COURTESY_DELAY_SECONDS = 1.0

MAX_PAGES = 30
"""Shared pagination cap: a server still serving full pages past this is malfunctioning."""


class PoliteHttpClient:
    """httpx.Client wrapper enforcing the courtesy rules for one source host."""

    def __init__(
        self,
        slug: str,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self.slug = slug
        self.base_url = base_url.rstrip("/")
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

    def get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        return self._request("GET", url, params=params)

    def post(self, url: str, *, json: object = None, headers: dict | None = None) -> httpx.Response:
        return self._request("POST", url, json=json, headers=headers)

    def allows(self, url: str) -> bool:
        """robots.txt check, fetched once and cached for the client's lifetime."""
        if self._robots is None:
            parser = RobotFileParser()
            try:
                response = self.get(f"{self.base_url}/robots.txt")
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
        return self._robots.can_fetch(USER_AGENT, url)

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
                response = self._client.request(
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
