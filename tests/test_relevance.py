"""Tests for the relevance filter. Pure functions, offline, table-driven with real titles."""

from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.relevance import (
    BLOCKLIST,
    ClassifiedListing,
    Relevance,
    apply_relevance,
    classify,
    normalize,
)
from repuestos_radar.schema import Condition, NormalizedListing

# --- normalization ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Módulo", "modulo"),
        ("MODULO", "modulo"),
        ("A 34", "a34"),
        ("a-34", "a34"),
        ("A34", "a34"),
        ("  módulo   samsung  ", "modulo samsung"),
        ("Vidrio Templado 9D!!", "vidrio templado 9d"),
        ("batería", "bateria"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_joins_model_number_spacing() -> None:
    # A letter+space+digits collapses so "a 34" == "a-34" == "a34".
    assert normalize("modulo a 34") == "modulo a34"
    assert normalize("modulo a-34") == "modulo a34"


# --- classify: MATCH -------------------------------------------------------


MATCH_CASES = [
    ("modulo a12", "MODULO SAMSUNG A12 FLEX A032F ORIG PREMIUN"),
    ("modulo a32", "MODULO SAMSUNG A32 OLED C/MARCO"),
    ("modulo a34", "Modulo Samsung A34 Oled Con Marco"),
    ("bateria s8", "Batería Samsung S8 Plus G955 Original"),
    ("modulo a 34", "MODULO SAMSUNG A34 INCELL"),  # spacing normalization
]


@pytest.mark.parametrize(("query", "title"), MATCH_CASES)
def test_match_cases(query: str, title: str) -> None:
    result = classify(query, title)
    assert result.relevance is Relevance.MATCH, result.reason
    assert result.score >= 0.0


def test_match_reason_mentions_all_tokens() -> None:
    result = classify("modulo a12", "MODULO SAMSUNG A12 FLEX A032F ORIG PREMIUN")
    assert "all" in result.reason.lower()


# --- classify: REJECT ------------------------------------------------------


REJECT_CASES = [
    # Accessories (blocklist) even though "a34"/"samsung" match.
    ("modulo a34", "Funda Samsung A34 Silicona Negra", "funda"),
    ("modulo a34", "Vidrio Templado Samsung A34 9D", "templado"),
    ("modulo a34", "Protector de Pantalla Samsung A34", "protector"),
    ("modulo a34", "Cable USB Tipo C Samsung A34", "cable"),
    ("cargador a34", "Carcasa Trasera Samsung A34", "carcasa"),
    # Wrong model number: everything else matches but the model differs.
    ("modulo a34", "Modulo Samsung A54 Oled Con Marco", "model"),
    ("modulo a12", "MODULO SAMSUNG A32 OLED C/MARCO", "model"),
]


@pytest.mark.parametrize(("query", "title", "reason_fragment"), REJECT_CASES)
def test_reject_cases(query: str, title: str, reason_fragment: str) -> None:
    result = classify(query, title)
    assert result.relevance is Relevance.REJECT, result.reason
    assert reason_fragment in result.reason.lower()


def test_wrong_model_rejected_even_with_perfect_word_overlap() -> None:
    # This is the highest-value rule: wrong model price is worse than a miss.
    result = classify("modulo a34", "Modulo Samsung Galaxy A54 Oled Con Marco Original")
    assert result.relevance is Relevance.REJECT
    assert "model" in result.reason.lower()


# --- classify: LOW_CONFIDENCE ---------------------------------------------


def test_low_confidence_partial_token_match() -> None:
    # "pantalla" matches nothing here, "a34" matches -> most-but-not-all.
    result = classify("modulo pantalla a34", "Modulo Samsung A34 Oled")
    assert result.relevance is Relevance.LOW_CONFIDENCE, result.reason


def test_low_confidence_fuzzy_typo() -> None:
    # Typo in the part word: fuzzy in the middle band, model number present.
    result = classify("modolo a34", "Modulo Samsung A34 Oled Con Marco")
    assert result.relevance in (Relevance.MATCH, Relevance.LOW_CONFIDENCE)
    assert result.relevance is not Relevance.REJECT


# --- degenerate inputs -----------------------------------------------------


def test_empty_title_is_reject() -> None:
    result = classify("modulo a34", "")
    assert result.relevance is Relevance.REJECT


def test_empty_query_is_reject() -> None:
    result = classify("", "Modulo Samsung A34")
    assert result.relevance is Relevance.REJECT


def test_query_without_model_number_can_match() -> None:
    # Not every query has a model number; the required-model rule must not
    # reject when the query itself has none.
    result = classify("cargador samsung", "Cargador Samsung Original 25w")
    assert result.relevance is Relevance.MATCH


# --- blocklist -------------------------------------------------------------


def test_blocklist_is_frozenset_and_tunable() -> None:
    assert isinstance(BLOCKLIST, frozenset)
    assert {"funda", "templado", "protector", "cable", "carcasa"} <= BLOCKLIST


# --- apply_relevance batch -------------------------------------------------


def make_listing(title: str, external_id: str = "1") -> NormalizedListing:
    return NormalizedListing(
        source_slug="celuphone",
        external_id=external_id,
        title=title,
        price=Decimal("10000"),
        currency="ARS",
        condition=Condition.UNKNOWN,
        url="https://celuphone.com.ar/producto/x",
        fetched_at=date.today(),
    )


def test_apply_relevance_labels_whole_batch_without_dropping() -> None:
    listings = [
        make_listing("Modulo Samsung A34 Oled Con Marco", "1"),
        make_listing("Funda Samsung A34 Silicona", "2"),
        make_listing("Modulo Samsung A54 Oled", "3"),
    ]
    classified = apply_relevance("modulo a34", listings)

    assert len(classified) == 3  # nothing dropped
    assert all(isinstance(c, ClassifiedListing) for c in classified)
    labels = [c.result.relevance for c in classified]
    assert labels[0] is Relevance.MATCH
    assert labels[1] is Relevance.REJECT
    assert labels[2] is Relevance.REJECT
    assert classified[0].listing.external_id == "1"
