"""Manual relevance check — NOT run by CI, makes real HTTP requests (politely).

Usage: python scripts/try_relevance.py <source-slug> <query>
Fetches live listings, classifies them, and prints the label distribution
plus a few examples per bucket.
"""

import sys
from collections import Counter

from repuestos_radar.adapters import AdapterError, adapter_for
from repuestos_radar.relevance import Relevance, apply_relevance
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

    with adapter_for(source) as adapter:
        try:
            listings = adapter.fetch(query)
        except AdapterError as error:
            print(f"{slug}: UNAVAILABLE — {error}")
            return 1

    classified = apply_relevance(query, listings)
    counts = Counter(c.result.relevance for c in classified)
    total = len(classified)
    print(f"{slug} '{query}': {total} listings")
    for label in Relevance:
        n = counts.get(label, 0)
        pct = (100 * n / total) if total else 0
        print(f"  {label.value:15} {n:4}  ({pct:4.1f}%)")

    for label in Relevance:
        examples = [c for c in classified if c.result.relevance is label][:4]
        if not examples:
            continue
        print(f"\n-- {label.value} examples --")
        for c in examples:
            print(f"  {c.listing.price} ARS | {c.listing.title}")
            print(f"       reason: {c.result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
