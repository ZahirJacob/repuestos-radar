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


def _parse_entry(index: int, entry: dict) -> Source:
    for field_name in _REQUIRED_FIELDS:
        value = entry.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source #{index}: missing or empty required field '{field_name}'")
    return Source(
        **{field_name: entry[field_name].strip() for field_name in _REQUIRED_FIELDS},
        scraping_notes=entry.get("scraping_notes", "").strip() or None,
    )


def load_sources(path: Path | None = None) -> list[Source]:
    """Load and validate the source registry; raise ValueError on bad data."""
    registry_path = path or _DEFAULT_PATH
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
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
