# repuestos-radar

[![CI](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/ZahirJacob/repuestos-radar/actions/workflows/ci.yml)

> 🇦🇷 [Leé esto en español](README.es.md)

Market intelligence for phone repair shops: tracks prices of phone parts and used phones
across vetted local resellers, stores the history in Postgres, and serves a dashboard
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
   listings for every tracked search item from the vetted local reseller storefronts in the
   source registry.
2. **Postgres price history.** Every listing is normalized into a common schema and appended to a
   hosted Postgres database (Neon free tier), building a price history over time.
3. **Streamlit dashboard.** A Spanish-first dashboard (with an EN/ES toggle) shows current prices,
   history, and a margin calculator. It is public read-only, except for a password-protected admin
   page where the client manages the watchlist — the list of tracked search items lives in a
   database table, so the client adds or removes items without touching code.

## Data sources and trust policy

Prices are only as good as their sources, so every source carries trust metadata: physical address,
Google rating and reviews, and company registration, where known. A source is only added after it
passes a documented vetting checklist: it must be an established business with a verifiable
address, public prices, and consistent stock. The registry lives in [`sources.yaml`](sources.yaml).

Current sources (all established storefronts in Rosario):

| Source | Platform | Address | How we read it |
| --- | --- | --- | --- |
| Novocell | Wix | Av. Pellegrini 356 | Polite scraping |
| Tienda Móvil | WooCommerce | Mendoza 1209 | Polite scraping |
| Evophone | WooCommerce | Av. Pellegrini 4041 | Polite scraping |
| Celuphone | WooCommerce | Santa Fe 4245 | Polite scraping |

**Why not MercadoLibre?** Its listing-search API is restricted to certified partners (regular app
and user credentials get 403s), and its listing pages redirect automated requests — even with an
honest user-agent — to a verification wall. Our own courtesy policy says sites that decline
automated access get skipped, not worked around, so we do not ingest MercadoLibre prices. The ML
credentials stay in use only for the product-catalog API (product-name normalization, in a later
milestone).

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
Novocell (Wix) ─────┐
Tienda Móvil (Woo) ─┼──► per-source adapters ──► normalized listings ──► Postgres ──► dashboard
Evophone (Woo) ─────┤        (fetch + parse)     (item, price, condition,             analysis
Celuphone (Woo) ────┘                             source, date)                       alerts
```

## Roadmap

- **M0 — Scaffold** *(this PR)*: project layout, CI, docs.
- **M1 — Ingestion**: storefront adapters for the vetted sources, normalized listing schema,
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

## Running an ingestion

The ingestion runner fetches current listings from every vetted source for every active tracked
item, labels them with the relevance filter, and stores them as daily snapshots. It needs
`DATABASE_URL` set in the environment (or in `.env`) — a Postgres or SQLite URL; tables are
created automatically if missing.

```bash
python -m repuestos_radar.ingest
```

The run report goes to stdout as grep-able `key=value` lines — per source: tracked items queried,
listings fetched, malformed products skipped, rows inserted vs already stored for the day, the
relevance breakdown (`match` / `low_confidence` / `reject`), and the failure message if the source
was unreachable — plus a summary line. A failing source never aborts the run: it is reported and
the rest continue. The exit code is 0 when at least one source succeeded (a run with no active
tracked items is a successful no-op) and 1 when every source failed or the run could not start.
Progress is committed after each source/item save and storage is idempotent per day, so re-running
after a crash is safe. This is the job the [daily automation](#daily-automation) workflow runs.

## Daily automation

A GitHub Actions workflow ([`ingest.yml`](.github/workflows/ingest.yml)) runs the ingestion once a
day at 09:00 UTC (06:00 in Argentina) — exactly one scheduled run per day, as the scraping courtesy
policy requires. The job checks out the repo, installs the locked dependencies with uv
(`uv sync --locked`, against the committed `uv.lock`), and runs `python -m repuestos_radar.ingest`;
the run report shows up in the workflow log. A concurrency
group keeps runs from ever overlapping, the job times out after 15 minutes, and it is never retried
automatically.

It needs the `DATABASE_URL` repository secret (Settings → Secrets and variables → Actions) with the
Postgres connection string. Until the secret is set, runs abort at startup with
`ingestion aborted (database error)` and a red run — visible, harmless, and fixed by adding the
secret.

To trigger a run manually: Actions → "Daily ingestion" → "Run workflow", or
`gh workflow run ingest.yml`.
