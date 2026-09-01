"""Daily Spanish report: the analysis rendered for humans.

Internal team tool (python -m repuestos_radar.report) until the M4 dashboard
exists; it also keeps the analysis honest — everything it prints is exactly
what the dashboard will show. All Spanish copy in the project's analysis
stack lives HERE, never in analysis/margin/quality.
"""

import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from repuestos_radar.analysis import (
    BASIS_MEDIAN,
    TierAnalysis,
    analyze_item,
    latest_day,
    listings_for_day,
    tier_trends,
)
from repuestos_radar.db import get_engine, get_session_factory, init_db
from repuestos_radar.margin import margins_for
from repuestos_radar.models import ServicePrice, TrackedItem
from repuestos_radar.sources import load_sources

TIER_LABELS_ES = {
    "original": "Original",
    "oled": "OLED",
    "incell": "Incell/TFT",
    "nuevo": "Nuevo",
    "reacondicionado": "Reacondicionado",
    "unlabeled": "Sin calidad indicada",
}


def format_ars(value: Decimal) -> str:
    """Whole pesos, Argentine thousands separator: 20700 -> "$20.700"."""
    whole = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return "$" + f"{whole:,}".replace(",", ".")


def _format_pct(value: Decimal) -> str:
    """Percentage magnitude with the Spanish decimal comma: 10.0 -> "10,0"."""
    return str(abs(value)).replace(".", ",")


def _format_day(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def render_report(session: Session, today: date | None = None) -> str:
    """The whole day's summary, in Spanish, one section per active item.

    ``today`` pins the day for every item (tests, replaying a past day);
    without it each item uses its own most recent stored day.
    """
    store_names = {source.slug: source.name for source in load_sources()}
    items = list(
        session.scalars(select(TrackedItem).where(TrackedItem.active).order_by(TrackedItem.id))
    )

    lines: list[str] = ["Reporte diario de precios — repuestos-radar"]
    if not items:
        lines += ["", "No hay búsquedas activas — nada para informar."]
        return "\n".join(lines) + "\n"

    for item in items:
        lines.append("")
        day = today if today is not None else latest_day(session, item.id)
        listings = listings_for_day(session, item.id, day) if day is not None else []
        if not listings:
            # A day can exist with only rejected listings (latest_day counts
            # them), so an empty day here is ordinary — say so, don't skip.
            lines.append(f"=== {item.query} ===")
            lines.append(f"  sin datos de hoy para {item.query}")
            continue

        lines.append(f"=== {item.query} — precios del {_format_day(day)} ===")
        analyses = analyze_item(listings)
        for analysis in analyses:
            lines.extend(_render_tier(session, item.id, day, analysis, store_names))
        lines.extend(_render_margins(session, item.id, analyses, store_names))

    return "\n".join(lines) + "\n"


def _render_tier(
    session: Session,
    tracked_item_id: int,
    day: date,
    analysis: TierAnalysis,
    store_names: dict[str, str],
) -> list[str]:
    lines = [f"{TIER_LABELS_ES[analysis.tier]}:"]

    # A group is never all-outliers (nothing at the median gets flagged), so
    # there is always a trustworthy cheapest offer to call the best price.
    best = next(offer for offer in analysis.offers if not offer.outlier)
    lines.append(
        f"  mejor precio: {format_ars(best.price)} en "
        f"{store_names.get(best.source_slug, best.source_slug)}"
    )

    if analysis.basis == BASIS_MEDIAN:
        fair = f"  precio justo: {format_ars(analysis.fair_price)}"
        if analysis.store_count <= 3:
            fair += (
                f" (entre {format_ars(analysis.price_min)} y "
                f"{format_ars(analysis.price_max)}, {analysis.store_count} tiendas)"
            )
        lines.append(fair)
    else:
        lines.append("  un solo negocio lo vende hoy — sin precio de referencia")

    for offer in analysis.offers:
        store = store_names.get(offer.source_slug, offer.source_slug)
        if offer.outlier:
            lines.append(
                f"  ⚠ revisar: {store} a {format_ars(offer.price)} — precio muy "
                "alejado del resto (posible error, calidad mal etiquetada o una "
                "oferta real)"
            )
        if offer.relevance == "low_confidence":
            lines.append(f"  ⚠ revisar: coincidencia dudosa en {store} — «{offer.title}»")

    for point in tier_trends(session, tracked_item_id, analysis.tier, day):
        if not point.direction:
            continue
        if point.direction == "=":
            lines.append(f"  tendencia: = sin cambios vs hace {point.days_back} días")
        else:
            lines.append(
                f"  tendencia: {point.direction} {_format_pct(point.pct_change)}% "
                f"vs hace {point.days_back} días"
            )
    return lines


def _render_margins(
    session: Session,
    tracked_item_id: int,
    analyses: list[TierAnalysis],
    store_names: dict[str, str],
) -> list[str]:
    services = list(
        session.scalars(
            select(ServicePrice)
            .where(ServicePrice.tracked_item_id == tracked_item_id)
            .order_by(ServicePrice.id)
        )
    )
    lines = []
    for service in services:
        for tier_margin in margins_for(service.price_ars, analyses):
            tier_label = TIER_LABELS_ES[tier_margin.tier]
            store = store_names.get(tier_margin.part_source, tier_margin.part_source)
            if tier_margin.margin >= 0:
                lines.append(
                    f"  {service.label}: ganás {format_ars(tier_margin.margin)} "
                    f"usando {tier_label} de {store}"
                )
            else:
                lines.append(
                    f"  ⚠ {service.label}: perdés {format_ars(-tier_margin.margin)} "
                    f"usando {tier_label} de {store} — el repuesto cuesta más que "
                    "lo que se cobra"
                )
    if lines:
        lines.insert(0, "Márgenes por reparación:")
    return lines


def main() -> int:
    """CLI entry point: open the database and print today's report."""
    try:
        engine = get_engine()
        init_db(engine)
        with get_session_factory(engine)() as session:
            print(render_report(session), end="")
        return 0
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"report aborted (database error): {' '.join(str(exc).split())}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
