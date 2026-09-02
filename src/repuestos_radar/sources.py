"""Loader for the vetted source registry (sources.yaml at the repo root).

The registry carries trust metadata per source so every price in the system
has an auditable provenance. Tracked search items, by contrast, live in the
database (client-managed); only the vetted sources are code-reviewed data.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).parents[2] / "sources.yaml"
_REQUIRED_FIELDS = ("slug", "name", "url", "platform", "address", "city", "trust_notes")


@dataclass(frozen=True, slots=True)
class Source:
    """One vetted source and its trust metadata."""

    slug: str
    name: str
    url: str
    platform: str
    address: str
    city: str
    trust_notes: str
    scraping_notes: str | None = None
    priority_categories: tuple[str, ...] | None = None
    """Category path slugs a crawl-based adapter visits first, in this order.

    Only meaningful for tiendanube-platform sources; other adapters ignore it.
    """
    max_catalog_pages: int | None = None
    """Per-source override of the crawl page budget (adapter default when None).

    Only meaningful for tiendanube-platform sources; other adapters ignore it.
    """
    lat: float | None = None
    """Store latitude, hand-entered (see sources.yaml). Both lat and lon or neither."""
    lon: float | None = None


def _parse_priority_categories(index: int, value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(slug, str) and slug.strip() for slug in value
    ):
        raise ValueError(
            f"source #{index}: 'priority_categories' must be a list of "
            "non-empty strings when present"
        )
    return tuple(slug.strip() for slug in value)


def _parse_max_catalog_pages(index: int, value: object) -> int | None:
    if value is None:
        return None
    # bool is excluded explicitly: YAML `true` is an int subclass in Python.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(
            f"source #{index}: 'max_catalog_pages' must be a positive integer when present"
        )
    return value


def _parse_coordinate(index: int, name: str, value: object, limit: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"source #{index}: '{name}' must be a number when present")
    if not -limit <= value <= limit:
        raise ValueError(f"source #{index}: '{name}' must be within ±{limit}")
    return float(value)


def _parse_entry(index: int, entry: object) -> Source:
    if not isinstance(entry, dict):
        raise ValueError(f"source #{index}: expected a mapping, got {type(entry).__name__}")
    for field_name in _REQUIRED_FIELDS:
        value = entry.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source #{index}: missing or empty required field '{field_name}'")
    scraping_notes = entry.get("scraping_notes")
    if scraping_notes is not None and not isinstance(scraping_notes, str):
        raise ValueError(f"source #{index}: 'scraping_notes' must be a string when present")
    lat = _parse_coordinate(index, "lat", entry.get("lat"), 90.0)
    lon = _parse_coordinate(index, "lon", entry.get("lon"), 180.0)
    if (lat is None) != (lon is None):
        raise ValueError(f"source #{index}: 'lat' and 'lon' must be given together")
    return Source(
        **{field_name: entry[field_name].strip() for field_name in _REQUIRED_FIELDS},
        scraping_notes=(scraping_notes or "").strip() or None,
        priority_categories=_parse_priority_categories(index, entry.get("priority_categories")),
        max_catalog_pages=_parse_max_catalog_pages(index, entry.get("max_catalog_pages")),
        lat=lat,
        lon=lon,
    )


def load_sources(path: Path | None = None) -> list[Source]:
    """Load and validate the source registry; raise ValueError on bad data."""
    registry_path = path or _DEFAULT_PATH
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if data is not None and not isinstance(data, dict):
        raise ValueError(
            f"{registry_path}: expected a top-level mapping with a 'sources' list, "
            f"got {type(data).__name__}"
        )
    entries = (data or {}).get("sources")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{registry_path}: expected a non-empty 'sources' list")

    sources = [_parse_entry(index, entry) for index, entry in enumerate(entries)]

    seen: set[str] = set()
    for source in sources:
        if source.slug in seen:
            raise ValueError(f"duplicate source slug '{source.slug}'")
        seen.add(source.slug)
    return sources
