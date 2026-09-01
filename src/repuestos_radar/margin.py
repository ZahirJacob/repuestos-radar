"""Margin math: what a repair earns given today's part prices.

Margin is naturally tier-aware — "con incell ganás X, con OLED Y" — because
the honest answer depends on which quality the customer is quoted.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from repuestos_radar.analysis import TierAnalysis


@dataclass(frozen=True, slots=True)
class TierMargin:
    tier: str
    part_price: Decimal
    part_source: str
    margin: Decimal


def margins_for(service_price: Decimal, analyses: Sequence[TierAnalysis]) -> list[TierMargin]:
    """One margin per tier: service price minus the cheapest trustworthy part.

    Outlier-flagged offers are never the basis of a margin; a tier whose
    offers are all outliers is skipped rather than shown with a suspect
    number. Tier order follows the input analyses (display order).
    """
    margins = []
    for analysis in analyses:
        best = next((offer for offer in analysis.offers if not offer.outlier), None)
        if best is None:
            continue
        margins.append(
            TierMargin(
                tier=analysis.tier,
                part_price=best.price,
                part_source=best.source_slug,
                margin=service_price - best.price,
            )
        )
    return margins
