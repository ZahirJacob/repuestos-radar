"""Manual adapter check — NOT run by CI, makes real HTTP requests (politely).

Usage: python scripts/try_adapter.py <source-slug> <query>
"""

import sys

from repuestos_radar.adapters import AdapterError, adapter_for
from repuestos_radar.sources import load_sources


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 2
    slug, query = sys.argv[1], sys.argv[2]

    source = next((s for s in load_sources() if s.slug == slug), None)
    if source is None:
        print(f"unknown source slug '{slug}'")
        return 2

    adapter = adapter_for(source)
    try:
        listings = adapter.fetch(query)
    except AdapterError as error:
        print(f"{slug}: UNAVAILABLE — {error}")
        return 1

    print(f"{slug}: {len(listings)} listings for '{query}' (skipped {adapter.skipped} malformed)")
    for listing in listings[:3]:
        print(f"  [{listing.external_id}] {listing.title}")
        print(f"      {listing.price} {listing.currency} — {listing.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
