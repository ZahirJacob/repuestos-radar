"""Tests for the relevance filter. Pure functions, offline, table-driven with real titles."""

from datetime import date
from decimal import Decimal

import pytest

from repuestos_radar.relevance import (
    HARD_REJECT,
    PART_WORDS,
    SOFT,
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


def test_normalize_does_not_merge_stopwords_into_numbers() -> None:
    # L1 regression: only a SINGLE leading letter joins a number, so "de 11"
    # keeps "11" as its own token and never invents a fake model "de11".
    tokens = normalize("modulo de 11").split(" ")
    assert "11" in tokens
    assert "de11" not in tokens


def test_query_with_bare_number_still_matches() -> None:
    # "modulo 11" must still MATCH an "iPhone 11" title after the regex fix.
    result = classify("modulo 11", "Modulo iPhone 11 Original")
    assert result.relevance is Relevance.MATCH, result.reason


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
    ("modulo a34", "Soporte Vehicular Samsung A34", "soporte"),
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


def test_blocklist_tiers_are_frozensets_and_tunable() -> None:
    assert isinstance(HARD_REJECT, frozenset)
    assert isinstance(SOFT, frozenset)
    # Unambiguous accessories hard-reject.
    assert {"funda", "templado", "protector", "carcasa", "film", "hidrogel"} <= HARD_REJECT
    # Ambiguous terms that also name real parts must be SOFT only.
    assert {"vidrio", "auricular", "parlante", "cable", "memoria", "chip"} <= SOFT
    # Plural auriculares (headphones) stays hard; singular auricular (earpiece) is soft.
    assert "auriculares" in HARD_REJECT
    assert "auricular" in SOFT
    # A term is never in both tiers.
    assert not (HARD_REJECT & SOFT)


def test_soft_term_never_hard_rejects() -> None:
    # "Vidrio Trasero A34" (back glass — a real part) for a tapa query must
    # not be hard-rejected even though "tapa" doesn't literally appear.
    result = classify("tapa a34", "Vidrio Trasero Samsung A34")
    assert result.relevance is not Relevance.REJECT
    assert "ambiguous" in result.reason.lower()


def test_soft_term_as_the_part_can_match() -> None:
    # Querying for the earpiece itself: auricular is the real part, MATCH.
    result = classify("auricular a34", "Auricular Samsung A34 Original")
    assert result.relevance is Relevance.MATCH, result.reason


def test_hard_reject_new_terms() -> None:
    # New HARD_REJECT terms added in M1 actually reject.
    for title in ("Film Hidrogel A34", "Lamina Protectora A34", "Popsocket A34"):
        result = classify("modulo a34", title)
        assert result.relevance is Relevance.REJECT, f"{title}: {result.reason}"


def test_hard_reject_reason_wording() -> None:
    hard = classify("modulo a34", "Funda Samsung A34 Silicona")
    assert "accessory term" in hard.reason.lower()
    soft = classify("tapa a34", "Vidrio Trasero Samsung A34")
    assert "ambiguous term" in soft.reason.lower()


def test_combo_listing_matches_each_model_and_rejects_others() -> None:
    # L2: one title listing several compatible models.
    title = "MODULO ZTE BLADE A34 / A54"
    assert classify("a34", title).relevance is Relevance.MATCH
    assert classify("a54", title).relevance is Relevance.MATCH
    assert classify("a75", title).relevance is Relevance.REJECT


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


# --- tracked item kind: phone vs part ---------------------------------------

# Real titles fetched for the tracked phone "samsung s24 ultra" on 2026-09-02:
# every one is a spare part FOR the phone, and every one carries the query
# words, so only the item's kind can separate them from the handset itself.
S24_ULTRA_PART_TITLES = [
    "LENTE CUBRE CAMARA SAMSUNG S24 ULTRA",
    "FLEX ENCENDIDO SAMSUNG S24 ULTRA",
    "Bateria Samsung S24 Ultra",
    "TAPA TRASERA SAMSUNG S24 ULTRA NEGRA",
    "PLACA CARGA SAMSUNG S24 ULTRA ORIGINAL",
    "Modulo Samsung S24 Ultra Oled Con Marco Negro",
    "PORTA SIM SAMSUNG S24 ULTRA",
]
S24_ULTRA_PHONE_TITLES = [
    "Samsung Galaxy S24 Ultra 256GB",
    "Samsung Galaxy S24 Ultra Reacondicionado",
]


def test_part_words_are_normalized_tokens() -> None:
    for word in PART_WORDS:
        assert normalize(word) == word


@pytest.mark.parametrize("title", S24_ULTRA_PART_TITLES)
def test_phone_item_rejects_part_listings(title: str) -> None:
    result = classify("samsung s24 ultra", title, kind="phone")
    assert result.relevance is Relevance.REJECT
    assert result.reason.startswith("part word in a phone item: ")


@pytest.mark.parametrize("title", S24_ULTRA_PHONE_TITLES)
def test_phone_item_keeps_actual_phone_listings(title: str) -> None:
    result = classify("samsung s24 ultra", title, kind="phone")
    assert result.relevance is Relevance.MATCH
    assert "part word" not in result.reason


@pytest.mark.parametrize("title", S24_ULTRA_PART_TITLES + S24_ULTRA_PHONE_TITLES)
def test_part_item_is_unchanged_by_the_kind_argument(title: str) -> None:
    # kind="part" is the default and must be identical to the two-arg call.
    assert classify("samsung s24 ultra", title, kind="part") == classify("samsung s24 ultra", title)


def test_part_item_keeps_todays_labels_for_the_s24_part_titles() -> None:
    labels = {
        title: classify("samsung s24 ultra", title).relevance for title in S24_ULTRA_PART_TITLES
    }
    # Only the SIM tray trips the accessory blocklist (softened to
    # LOW_CONFIDENCE because "samsung"/"ultra" count as query part words);
    # the rest carry every query word and MATCH — exactly the today's-data
    # problem the kind fixes.
    assert labels["PORTA SIM SAMSUNG S24 ULTRA"] is Relevance.LOW_CONFIDENCE
    for title, label in labels.items():
        if title != "PORTA SIM SAMSUNG S24 ULTRA":
            assert label is Relevance.MATCH, title


def test_phone_item_still_applies_the_accessory_blocklist() -> None:
    # No part word, but an accessory: the regular HARD_REJECT rule runs after.
    # For a PART item the shared "samsung"/"ultra" tokens soften this to
    # LOW_CONFIDENCE; a phone query has no part words, so nothing softens it.
    title = "Funda Samsung S24 Ultra Silicona"
    result = classify("samsung s24 ultra", title, kind="phone")
    assert result.relevance is Relevance.REJECT
    assert result.reason == "accessory term: funda"
    assert classify("samsung s24 ultra", title).relevance is Relevance.LOW_CONFIDENCE


def test_phone_item_still_requires_the_model_number() -> None:
    result = classify("samsung s24 ultra", "Samsung Galaxy S23 Ultra 256GB", kind="phone")
    assert result.relevance is Relevance.REJECT
    assert result.reason == "required model number missing: s24"


def test_phone_part_word_reason_names_the_first_word_alphabetically() -> None:
    result = classify("samsung s24 ultra", "Modulo Samsung S24 Ultra Con Marco", kind="phone")
    assert result.reason == "part word in a phone item: marco"


def test_unknown_kind_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown tracked item kind: 'tablet'"):
        classify("samsung s24 ultra", "Samsung Galaxy S24 Ultra", kind="tablet")
    with pytest.raises(ValueError, match="unknown tracked item kind"):
        apply_relevance("samsung s24 ultra", [make_listing("Samsung S24 Ultra")], kind="")


def test_apply_relevance_passes_the_kind_through() -> None:
    listings = [
        make_listing("Samsung Galaxy S24 Ultra 256GB", "1"),
        make_listing("Bateria Samsung S24 Ultra", "2"),
    ]
    as_phone = apply_relevance("samsung s24 ultra", listings, kind="phone")
    as_part = apply_relevance("samsung s24 ultra", listings)

    assert [c.result.relevance for c in as_phone] == [Relevance.MATCH, Relevance.REJECT]
    assert [c.result.relevance for c in as_part] == [Relevance.MATCH, Relevance.MATCH]
