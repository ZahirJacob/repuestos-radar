"""The polite HTTP client's hard limits: response size and redirect depth."""

import gzip

import httpx
import pytest

from repuestos_radar.adapters.base import AdapterError
from repuestos_radar.adapters.politeness import MAX_RESPONSE_BYTES, PoliteHttpClient

BASE = "https://shop.example.com.ar"


def _client(handler, **kwargs) -> PoliteHttpClient:
    return PoliteHttpClient(
        "shop-test", BASE, transport=httpx.MockTransport(handler), sleep=lambda _s: None, **kwargs
    )


def test_default_cap_is_a_few_megabytes():
    assert 1_000_000 <= MAX_RESPONSE_BYTES <= 20_000_000


def test_oversized_response_is_an_adapter_error_and_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x" * 2000)

    client = _client(handler, max_response_bytes=1000)
    with pytest.raises(AdapterError, match="too large"):
        client.get(f"{BASE}/big")
    assert calls == 1


def test_small_response_keeps_text_json_and_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"X-WP-TotalPages": "3"})

    response = _client(handler, max_response_bytes=1000).get(f"{BASE}/wp-json")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.text == '{"ok":true}'
    assert response.headers["X-WP-TotalPages"] == "3"
    assert response.request.url == f"{BASE}/wp-json"


def test_cap_applies_to_the_decoded_body_of_a_gzipped_response():
    """A tiny gzip body that inflates past the cap is still refused; a small
    one arrives already decoded and readable."""
    payload = gzip.compress(b"a" * 5000)

    def handler(request: httpx.Request) -> httpx.Response:
        # stream=, not content=: httpx decodes content= eagerly in the
        # constructor, which would leave the client's decoder path untested.
        return httpx.Response(
            200, stream=httpx.ByteStream(payload), headers={"Content-Encoding": "gzip"}
        )

    with pytest.raises(AdapterError, match="too large"):
        _client(handler, max_response_bytes=1000).get(f"{BASE}/gz")
    assert _client(handler, max_response_bytes=10_000).get(f"{BASE}/gz").text == "a" * 5000


def test_redirect_chains_are_bounded_and_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"Location": f"{BASE}/{n + 1}"})

    with pytest.raises(AdapterError, match="TooManyRedirects"):
        _client(handler).get(f"{BASE}/0")
    assert calls == 6  # the request plus five hops; a loop is deterministic, no retry
