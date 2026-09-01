"""Tests for title-based quality-tier labeling."""

from repuestos_radar.quality import (
    TIER_INCELL,
    TIER_OLED,
    TIER_ORIGINAL,
    TIER_UNLABELED,
    label_part_tier,
)


def test_original_signals():
    assert label_part_tier("Modulo Samsung A32 Original") == TIER_ORIGINAL
    assert label_part_tier("Pantalla iPhone 11 Service Pack") == TIER_ORIGINAL


def test_oled_signals_including_amoled():
    assert label_part_tier("Módulo A32 OLED con marco") == TIER_OLED
    assert label_part_tier("Pantalla AMOLED Samsung A54") == TIER_OLED


def test_incell_signals_with_punctuation_variants():
    assert label_part_tier("Modulo A32 Incell") == TIER_INCELL
    assert label_part_tier("Pantalla IN-CELL calidad") == TIER_INCELL
    assert label_part_tier("Display TFT A32") == TIER_INCELL


def test_no_signal_is_unlabeled():
    assert label_part_tier("Modulo Samsung A32 4G") == TIER_UNLABELED


def test_conflict_humbler_tier_wins():
    # Sellers oversell: "OLED calidad original" is an OLED, not an original.
    assert label_part_tier("Pantalla OLED calidad original A32") == TIER_OLED
    assert label_part_tier("Modulo incell tipo original") == TIER_INCELL


def test_word_boundaries_no_substring_leaks():
    # "amoled" must not match via its "oled" substring twice, and unrelated
    # words containing signal letters must not match at all.
    assert label_part_tier("Funda originalidad dudosa") == TIER_UNLABELED
