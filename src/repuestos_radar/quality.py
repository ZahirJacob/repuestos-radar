"""Quality-tier labeling from listing titles.

Tiers are computed at analysis time and never stored: tuning the signal
lists below relabels all history for free. Same philosophy as the relevance
filter — visible word lists, so "why did this get this label?" is always
answerable.
"""

from repuestos_radar.relevance import normalize

TIER_INCELL = "incell"
TIER_OLED = "oled"
TIER_ORIGINAL = "original"
TIER_UNLABELED = "unlabeled"

# Humbler tier first. On a title matching two tiers the humbler one wins:
# sellers oversell ("OLED calidad original"), so trust the humbler word.
PART_TIER_ORDER = (TIER_INCELL, TIER_OLED, TIER_ORIGINAL)

# Signals are written in normalized form (see relevance.normalize): "in-cell"
# normalizes to "in cell", so the space form covers the hyphen form too.
_PART_TIER_SIGNALS: dict[str, tuple[str, ...]] = {
    TIER_INCELL: ("incell", "in cell", "tft"),
    TIER_OLED: ("oled", "amoled"),
    TIER_ORIGINAL: ("original", "service pack", "genuine"),
}


def _has_signal(normalized_title: str, signal: str) -> bool:
    # Whole-word/phrase containment: keeps "amoled" from matching " oled "
    # and "originalidad" from matching " original ".
    return f" {signal} " in f" {normalized_title} "


def label_part_tier(title: str) -> str:
    """One tier per title; humbler tier wins conflicts; no signal = unlabeled."""
    normalized = normalize(title)
    for tier in PART_TIER_ORDER:
        if any(_has_signal(normalized, signal) for signal in _PART_TIER_SIGNALS[tier]):
            return tier
    return TIER_UNLABELED
