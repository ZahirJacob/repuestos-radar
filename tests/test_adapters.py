"""Tests for the adapter contract and factory."""

import pytest

from repuestos_radar.adapters import adapter_for
from repuestos_radar.adapters.base import Adapter
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


def test_woocommerce_adapter_satisfies_the_contract() -> None:
    adapter = adapter_for(make_source("woocommerce"))
    assert isinstance(adapter, Adapter)


def test_unsupported_platform_raises() -> None:
    with pytest.raises(ValueError, match="wix"):
        adapter_for(make_source("wix"))
