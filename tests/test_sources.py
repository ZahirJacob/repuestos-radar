"""Tests for the vetted source registry and its loader."""

from pathlib import Path

import pytest

from repuestos_radar.sources import Source, load_sources

VALID_YAML = """
sources:
  - slug: novocell
    name: Novocell
    url: https://novocell.com.ar
    platform: wix
    address: Av. Pellegrini 356
    city: Rosario
    trust_notes: Established storefront.
  - slug: evophone
    name: Evophone
    url: https://evophone.com.ar
    platform: woocommerce
    address: Av. Pellegrini 4041
    city: Rosario
    trust_notes: Established storefront.
    scraping_notes: Cloudflare filters default bot user-agents.
    cloud_blocked: true
"""


BASE_ENTRY = """
sources:
  - slug: novocell
    name: Novocell
    url: https://novocell.com.ar
    platform: wix
    address: Av. Pellegrini 356
    city: Rosario
    trust_notes: Established storefront.
"""


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_sources_with_fields(tmp_path: Path) -> None:
    sources = load_sources(write_yaml(tmp_path, VALID_YAML))
    assert [s.slug for s in sources] == ["novocell", "evophone"]
    novocell = sources[0]
    assert novocell.name == "Novocell"
    assert novocell.url == "https://novocell.com.ar"
    assert novocell.platform == "wix"
    assert novocell.address == "Av. Pellegrini 356"
    assert novocell.city == "Rosario"
    assert novocell.trust_notes == "Established storefront."
    assert novocell.scraping_notes is None
    assert novocell.cloud_blocked is False
    assert sources[1].scraping_notes == "Cloudflare filters default bot user-agents."
    assert sources[1].cloud_blocked is True


def test_source_is_frozen(tmp_path: Path) -> None:
    source = load_sources(write_yaml(tmp_path, VALID_YAML))[0]
    assert isinstance(source, Source)
    with pytest.raises(AttributeError):
        source.slug = "other"  # type: ignore[misc]


def test_duplicate_slugs_are_rejected(tmp_path: Path) -> None:
    duplicated = VALID_YAML.replace("slug: evophone", "slug: novocell")
    with pytest.raises(ValueError, match="novocell"):
        load_sources(write_yaml(tmp_path, duplicated))


@pytest.mark.parametrize(
    "field", ["slug", "name", "url", "platform", "address", "city", "trust_notes"]
)
def test_missing_required_field_is_rejected(tmp_path: Path, field: str) -> None:
    broken = VALID_YAML.replace(f"{field}:", f"ignored_{field}:", 1)
    with pytest.raises(ValueError, match=field):
        load_sources(write_yaml(tmp_path, broken))


def test_empty_required_field_is_rejected(tmp_path: Path) -> None:
    broken = VALID_YAML.replace("name: Novocell", 'name: "  "', 1)
    with pytest.raises(ValueError, match="name"):
        load_sources(write_yaml(tmp_path, broken))


def test_top_level_list_is_rejected_with_value_error(tmp_path: Path) -> None:
    # A registry that forgets the 'sources:' key and starts with the list
    # directly must raise the documented ValueError, not AttributeError.
    top_level_list = "- slug: novocell\n  name: Novocell\n"
    with pytest.raises(ValueError, match="top-level mapping"):
        load_sources(write_yaml(tmp_path, top_level_list))


def test_top_level_scalar_is_rejected_with_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="top-level mapping"):
        load_sources(write_yaml(tmp_path, "just a string\n"))


def test_non_mapping_entry_is_rejected(tmp_path: Path) -> None:
    broken = VALID_YAML + "  - just-a-string\n"
    with pytest.raises(ValueError, match="mapping"):
        load_sources(write_yaml(tmp_path, broken))


def test_wrong_typed_field_is_rejected(tmp_path: Path) -> None:
    broken = VALID_YAML.replace("platform: wix", "platform: 123", 1)
    with pytest.raises(ValueError, match="platform"):
        load_sources(write_yaml(tmp_path, broken))


CRAWL_TUNED_YAML = (
    VALID_YAML
    + """\
  - slug: partsnube
    name: Partsnube
    url: https://partsnube.example.com.ar
    platform: tiendanube
    address: Calle Falsa 123
    city: Rosario
    trust_notes: Established storefront.
    priority_categories:
      - celulares
      - repuestos/modulos
    max_catalog_pages: 160
"""
)


def test_crawl_tuning_keys_are_loaded(tmp_path: Path) -> None:
    source = load_sources(write_yaml(tmp_path, CRAWL_TUNED_YAML))[2]
    assert source.priority_categories == ("celulares", "repuestos/modulos")
    assert source.max_catalog_pages == 160


def test_crawl_tuning_keys_default_to_none(tmp_path: Path) -> None:
    source = load_sources(write_yaml(tmp_path, VALID_YAML))[0]
    assert source.priority_categories is None
    assert source.max_catalog_pages is None


def test_wrong_typed_priority_categories_is_rejected(tmp_path: Path) -> None:
    broken = CRAWL_TUNED_YAML.replace(
        "priority_categories:\n      - celulares\n      - repuestos/modulos",
        "priority_categories: celulares",
        1,
    )
    with pytest.raises(ValueError, match="priority_categories"):
        load_sources(write_yaml(tmp_path, broken))


def test_empty_priority_category_slug_is_rejected(tmp_path: Path) -> None:
    broken = CRAWL_TUNED_YAML.replace("- repuestos/modulos", '- "  "', 1)
    with pytest.raises(ValueError, match="priority_categories"):
        load_sources(write_yaml(tmp_path, broken))


@pytest.mark.parametrize("bad_value", ["eighty", "0", "-3", "true"])
def test_invalid_max_catalog_pages_is_rejected(tmp_path: Path, bad_value: str) -> None:
    broken = CRAWL_TUNED_YAML.replace(
        "max_catalog_pages: 160", f"max_catalog_pages: {bad_value}", 1
    )
    with pytest.raises(ValueError, match="max_catalog_pages"):
        load_sources(write_yaml(tmp_path, broken))


def test_wrong_typed_scraping_notes_is_rejected(tmp_path: Path) -> None:
    broken = VALID_YAML.replace(
        "scraping_notes: Cloudflare filters default bot user-agents.", "scraping_notes: 5", 1
    )
    with pytest.raises(ValueError, match="scraping_notes"):
        load_sources(write_yaml(tmp_path, broken))


def test_default_path_loads_repo_registry() -> None:
    sources = load_sources()
    assert {s.slug for s in sources} == {
        "novocell",
        "tienda-movil",
        "evophone",
        "celuphone",
        "litoral-accesorios",
        "mdrepuestos",
        "gofix",
        "onestore",
    }
    rosario = [s for s in sources if s.platform in {"wix", "woocommerce"}]
    assert all(s.city == "Rosario" for s in rosario)
    evophone = next(s for s in sources if s.slug == "evophone")
    assert evophone.scraping_notes is not None
    # Every Tiendanube store carries the Cloudflare/no-search caveat.
    for source in sources:
        if source.platform == "tiendanube":
            assert source.scraping_notes is not None
            assert "Cloudflare" in source.scraping_notes


def test_source_coordinates_parsed(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        BASE_ENTRY + "    lat: -32.9526\n    lon: -60.6310\n", encoding="utf-8"
    )  # BASE_ENTRY: reuse/define a minimal valid single-source yaml string local to the test file
    (source,) = load_sources(registry)
    assert source.lat == pytest.approx(-32.9526)
    assert source.lon == pytest.approx(-60.6310)


def test_source_coordinates_default_none(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(BASE_ENTRY, encoding="utf-8")
    (source,) = load_sources(registry)
    assert source.lat is None and source.lon is None


@pytest.mark.parametrize(
    "extra",
    [
        "    lat: -32.9526\n",  # lat without lon
        "    lon: -60.6310\n",  # lon without lat
        "    lat: -95.0\n    lon: -60.0\n",  # lat out of range
        "    lat: -32.0\n    lon: 190.0\n",  # lon out of range
        "    lat: south\n    lon: -60.0\n",  # not a number
    ],
)
def test_bad_coordinates_rejected(tmp_path, extra):
    registry = tmp_path / "sources.yaml"
    registry.write_text(BASE_ENTRY + extra, encoding="utf-8")
    with pytest.raises(ValueError):
        load_sources(registry)


def test_real_registry_rosario_sources_have_coordinates():
    by_slug = {source.slug: source for source in load_sources()}
    for slug in ("novocell", "tienda-movil", "evophone", "celuphone", "litoral-accesorios"):
        assert by_slug[slug].lat is not None, slug


def test_cloud_blocked_defaults_to_false(tmp_path: Path) -> None:
    (source,) = load_sources(write_yaml(tmp_path, BASE_ENTRY))
    assert source.cloud_blocked is False


@pytest.mark.parametrize("bad_value", ['"yes"', "1", "0", "[true]"])
def test_non_boolean_cloud_blocked_is_rejected(tmp_path: Path, bad_value: str) -> None:
    broken = VALID_YAML.replace("cloud_blocked: true", f"cloud_blocked: {bad_value}", 1)
    with pytest.raises(ValueError, match="cloud_blocked"):
        load_sources(write_yaml(tmp_path, broken))


def test_real_registry_cloud_blocked_stores_explain_themselves() -> None:
    """Every store flagged cloud_blocked says why in its scraping_notes (the
    403s and the flag), keeps its coordinates so the dashboard can still name
    it and measure distance, and the flag is a real bool on every source.
    The exact set of flagged stores is not pinned: flipping one back to
    false when it starts answering again must not break CI."""
    for source in load_sources():
        assert isinstance(source.cloud_blocked, bool), source.slug
        if source.cloud_blocked:
            assert source.scraping_notes, source.slug
            assert "403" in source.scraping_notes, source.slug
            assert "cloud_blocked" in source.scraping_notes, source.slug
            assert source.lat is not None, source.slug
