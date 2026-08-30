# repuestos-radar

[![CI](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml)

> 🇦🇷 [Leé esto en español](README.es.md)

Market intelligence for phone repair shops: tracks prices of phone spare parts and used phones
across MercadoLibre and local resellers, stores the history in Postgres, and serves a dashboard
that answers real pricing questions.

## What it is

repuestos-radar is built for a real client: a phone repair shop in Rosario, Argentina. The shop
buys screens, batteries, and other spare parts — and buys and sells used phones — in a market
where prices move constantly and quoting a repair means checking half a dozen sources by hand.

This project automates that: it watches the parts and phones the shop cares about, records prices
daily from multiple sources, and turns the history into answers — what does this screen cost today,
who has it cheapest, is this used phone offer above or below market, and what margin does a repair
leave at current prices.

## How it works

1. **Daily multi-source ingestion.** A GitHub Actions cron job runs once a day and fetches current
   listings for every tracked search item — from the MercadoLibre official API (OAuth app
   credentials) and from vetted local reseller storefronts.
2. **Postgres price history.** Every listing is normalized into a common schema and appended to a
   hosted Postgres database (Neon/Supabase free tier), building a price history over time.
3. **Streamlit dashboard.** A Spanish-first dashboard (with an EN/ES toggle) shows current prices,
   history, and a margin calculator. It is public read-only, except for a password-protected admin
   page where the client manages the watchlist — the list of tracked search items lives in a
   database table, so the client adds or removes items without touching code.

## Data sources and trust policy

Prices are only as good as their sources, so every source carries trust metadata: physical address,
Google rating, and — for MercadoLibre sellers — the seller's platform reputation. A source is only
added after a documented vetting checklist: it must be an established business with a verifiable
address or a strong platform reputation, public prices, and consistent stock.

Current sources:

| Source | Type | How we read it |
| --- | --- | --- |
| MercadoLibre | Marketplace (Argentina-wide) | Official API, OAuth app credentials |
| Novocell (Rosario) | Local reseller storefront (Wix) | Polite scraping |
| Tienda Móvil (Rosario) | Local reseller storefront (WooCommerce) | Polite scraping |

## Scraping courtesy policy

Local storefronts are scraped politely, and this is non-negotiable:

- **Once per day** — a single scheduled run, never more.
- **robots.txt is honored** — disallowed paths are never fetched.
- **Honest user-agent** — requests identify the project; no browser impersonation.
- **Backoff on errors** — failing sources are retried gently, then skipped for the day.
- **Bot-blocking sites are skipped** — if a site signals it does not want automated access, it is
  removed from the rotation rather than worked around.

## Architecture overview

Each source is a self-contained adapter behind a common interface: an adapter knows how to fetch
and parse one source, and emits listings in a single normalized schema — **item, price, condition,
source, date**. The ingestion job runs every adapter, collects normalized listings, and writes them
to Postgres. Everything downstream (analysis, dashboard, alerts) only ever sees the normalized
schema, so adding a source never touches the rest of the system.

```
MercadoLibre API ──┐
Novocell (Wix) ────┼──► per-source adapters ──► normalized listings ──► Postgres ──► dashboard
Tienda Móvil ──────┘        (fetch + parse)     (item, price, condition,              analysis
                                                 source, date)                        alerts
```

## Roadmap

- **M0 — Scaffold** *(this PR)*: project layout, CI, docs.
- **M1 — Ingestion**: MercadoLibre API adapter and storefront adapters, normalized listing schema,
  Postgres writes.
- **M2 — Daily automation**: GitHub Actions cron, error handling, backoff, run reports.
- **M3 — Analysis layer**: price history queries, best-price and trend calculations, margin math.
- **M4 — Dashboard + admin page**: Streamlit app (ES-first, EN/ES toggle), public read-only views,
  password-protected watchlist management.
- **M5 — Alerts and forecasting**: price-drop alerts and simple trend forecasts.

## Dev setup

Requires Python 3.12+.

```bash
git clone https://github.com/ZahirJacob/repuestos-radar.git
cd repuestos-radar
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Configure environment variables by copying the template and filling in real values (never commit
them — `.env` is gitignored):

```bash
cp .env.example .env
```

Run the checks:

```bash
ruff check .
ruff format --check .
pytest
```
