"""The calendar day in Argentina — the dashboard's "today", not UTC."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def argentina_today() -> date:
    return datetime.now(ARGENTINA_TZ).date()
