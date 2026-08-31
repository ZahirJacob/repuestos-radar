"""Tests for the adapter contract and factory."""

import pytest

from repuestos_radar.adapters import adapter_for
from repuestos_radar.adapters.base import Adapter
from repuestos_radar.adapters.wix import WixAdapter
from repuestos_radar.adapters.woocommerce import WooCommerceAdapter
from repuestos_radar.sources import Source


def make_source(platform: str) -> Source:
    return Source(
        slug="shop-test",
        name="Shop Test",
        url="https://shop.example.com.ar",
        platform=platform,
        address="Calle Falsa 123",
        city="Rosario",
        trust_notes="Test shop.",
    )


def test_factory_returns_woocommerce_adapter() -> None:
    source = make_source("woocommerce")
    adapter = adapter_for(source)
    assert isinstance(adapter, WooCommerceAdapter)
    assert adapter.source is source


def test_factory_returns_wix_adapter() -> None:
    source = make_source("wix")
    adapter = adapter_for(source)
    assert isinstance(adapter, WixAdapter)
    assert adapter.source is source


@pytest.mark.parametrize("platform", ["woocommerce", "wix"])
def test_adapters_satisfy_the_contract(platform: str) -> None:
    adapter = adapter_for(make_source(platform))
    # Note: runtime_checkable isinstance only proves the attributes/methods
    # exist (source, skipped, fetch), not their signatures or types.
    assert isinstance(adapter, Adapter)
    assert adapter.skipped == 0


def test_unsupported_platform_raises() -> None:
    with pytest.raises(ValueError, match="shopify"):
        adapter_for(make_source("shopify"))
