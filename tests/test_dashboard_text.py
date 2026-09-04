"""The language proxy and the ES/EN parity of the string modules."""

import re

import pytest

from repuestos_radar.dashboard import text, text_en, text_es

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _names(module) -> set[str]:
    return {name for name in vars(module) if name.isupper()}


def test_english_has_every_spanish_name_and_nothing_else():
    assert _names(text_en) == _names(text_es)


@pytest.mark.parametrize("name", sorted(_names(text_es)))
def test_placeholders_match_between_languages(name: str):
    es, en = getattr(text_es, name), getattr(text_en, name)
    assert type(es) is type(en)
    if isinstance(es, str):
        assert set(_PLACEHOLDER.findall(es)) == set(_PLACEHOLDER.findall(en)), name
    else:  # TIER_LABELS
        assert set(es) == set(en)


def test_current_language_falls_back_to_spanish():
    assert text.current_language({}) == "es"
    assert text.current_language({"lang": "klingon"}) == "es"
    assert text.current_language({"lang": "en"}) == "en"


def test_set_language_rejects_unknown_codes():
    state: dict[str, str] = {}
    text.set_language("en", state)
    assert state == {"lang": "en"}
    with pytest.raises(ValueError, match="unknown language"):
        text.set_language("pt", state)


def test_proxy_reads_the_module_for_the_active_language(monkeypatch):
    monkeypatch.setattr(text, "current_language", lambda state=None: "en")
    assert text.t.NAV_PRICES == text_en.NAV_PRICES == "Prices"
    monkeypatch.setattr(text, "current_language", lambda state=None: "es")
    assert text.t.NAV_PRICES == text_es.NAV_PRICES == "Precios"
    with pytest.raises(AttributeError):
        getattr(text.t, "NOT_A_STRING")  # noqa: B009 — the proxy path is the point
