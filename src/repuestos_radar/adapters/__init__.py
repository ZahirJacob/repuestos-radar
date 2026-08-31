"""Source adapters and the factory that picks one per source."""

from repuestos_radar.adapters.base import Adapter, AdapterError
from repuestos_radar.adapters.woocommerce import WooCommerceAdapter
from repuestos_radar.sources import Source

__all__ = ["Adapter", "AdapterError", "WooCommerceAdapter", "adapter_for"]


def adapter_for(source: Source) -> Adapter:
    """Return the adapter for a source's platform (only woocommerce so far)."""
    if source.platform == "woocommerce":
        return WooCommerceAdapter(source)
    raise ValueError(f"no adapter for platform '{source.platform}' (source '{source.slug}')")
