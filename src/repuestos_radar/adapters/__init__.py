"""Source adapters and the factory that picks one per source."""

from repuestos_radar.adapters.base import Adapter, AdapterError
from repuestos_radar.adapters.wix import WixAdapter
from repuestos_radar.adapters.woocommerce import WooCommerceAdapter
from repuestos_radar.sources import Source

__all__ = ["Adapter", "AdapterError", "WixAdapter", "WooCommerceAdapter", "adapter_for"]

_ADAPTERS = {"woocommerce": WooCommerceAdapter, "wix": WixAdapter}


def adapter_for(source: Source) -> Adapter:
    """Return the adapter for a source's platform."""
    adapter_class = _ADAPTERS.get(source.platform)
    if adapter_class is None:
        raise ValueError(f"no adapter for platform '{source.platform}' (source '{source.slug}')")
    return adapter_class(source)
