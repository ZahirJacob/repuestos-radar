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
    assert sources[1].scraping_notes == "Cloudflare filters default bot user-agents."


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
