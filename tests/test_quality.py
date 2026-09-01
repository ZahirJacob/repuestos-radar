"""Tests for title-based quality-tier labeling."""

from repuestos_radar.quality import (
    DEVICE_NEW,
    DEVICE_REFURBISHED,
    FRAME_UNKNOWN,
    FRAME_WITH,
    FRAME_WITHOUT,
    TIER_INCELL,
    TIER_OLED,
    TIER_ORIGINAL,
    TIER_UNLABELED,
    label_device_condition,
    label_frame,
    label_part_tier,
    label_tier,
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


def test_frame_detail():
    assert label_frame("Modulo A32 OLED con marco") == FRAME_WITH
    assert label_frame("Modulo A32 sin marco negro") == FRAME_WITHOUT
    assert label_frame("Modulo A32 OLED") == FRAME_UNKNOWN


def test_device_condition():
    assert label_device_condition("Samsung S24 Ultra Reacondicionado") == DEVICE_REFURBISHED
    assert label_device_condition("Moto G35 usado impecable") == DEVICE_REFURBISHED
    assert label_device_condition("iPhone 13 nuevo caja sellada") == DEVICE_NEW
    assert label_device_condition("Moto G17 256GB") == TIER_UNLABELED


def test_device_condition_conflict_refurbished_wins():
    assert label_device_condition("Moto G35 usado como nuevo") == DEVICE_REFURBISHED


def test_label_tier_prefers_part_signals_then_device():
    assert label_tier("Pantalla OLED A32 con marco") == TIER_OLED
    assert label_tier("Moto G35 reacondicionado") == DEVICE_REFURBISHED
    assert label_tier("Modulo Samsung A32") == TIER_UNLABELED
