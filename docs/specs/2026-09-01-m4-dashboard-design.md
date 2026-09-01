# M4 — Dashboard + Admin: Design

Date: 2026-09-01
Status: approved by Zahir (chat), pending final spec review
Scope: the end-user surface. Streamlit app (phone-first, Spanish), quick-search
mode for the adapters, distance feature, admin page, hosting and auth.

## Purpose

Give Activcelu (Zahir's dad, a non-programmer, phone-first) the M3 answers on a
screen he can actually use: open the app, tap a part, see where to buy it today,
at what price, with what margin — and, when a customer is waiting, refresh the
prices for one part in about a minute.

## Hard constraints

- **Phone-first.** Single-column layouts, big tap targets, no wide tables. The
  PC gets the same pages; the phone drives every layout decision.
- **Spanish only on screen** (Rioplatense-professional, Mo reviews). All screen
  text lives in one module (`dashboard/text_es.py`) so an English mode later is
  a small PR, not a rewrite.
- **Courtesy policy unchanged.** The 1-second per-store delay is never bypassed,
  robots.txt honored, honest UA, front-door only. Quick search adds at most 10
  small manual runs per day (~20 requests each across all stores) on top of the
  single daily crawl.
- **The app never picks "the best option."** It shows price and distance side by
  side; the human decides (per the approved M4 distance decision).
- Analysis stays in `analysis.py`/`margin.py`/`quality.py` — the dashboard
  imports them and renders; it never reimplements math or parses report text.

## 1. App shape

Three surfaces behind one login:

1. **Precios** (home) — card list of tracked parts.
2. Part **detail** page (tap a card).
3. **Ajustes** (admin) — repair prices, tracked parts, quick search.

Every page footer shows data freshness: "Actualizado hoy 09:15" (or the real
date when older). Navigation is Streamlit's standard page menu.

## 2. Home (Precios)

One card per tracked item, single column, in tracked-list order (which the team
controls). Each card:

- Part name, prominent.
- Best price today with store and tier: "Mejor: $20.700 — RepuestosFix (incell)".
- Margin when a service price exists: "Ganás $54.300" (green; red if negative).
- A warning dot when any listing for the part carries a flag (outlier or
  low-confidence).
- "Sin datos de hoy" when the latest day has nothing for the part — shown, not
  hidden.

No search box: the tracked list is small (9 items) and tapping beats typing on
a phone.

## 3. Part detail

Content in M3's priority order:

1. **Store ranking grouped by tier** (humbler tier first). Row: store name,
   price, distance, warning text in words when flagged ("precio muy bajo,
   revisar…", "revisar: puede ser otro modelo"). Rows link to the store's real
   listing URL. Sort toggle at the top: **Precio | Distancia** (price default);
   both sorts always display both values.
2. **Fair price per tier**, with the min–max range whenever `store_count <= 3`,
   and the explicit single-store wording when `basis: single-store`.
3. **Margin per tier**: "Cambio módulo A32 ($85.000): ganás $64.300 con incell,
   $41.200 con OLED". (Margin basis is always a sure match — M3 spec §5.)
4. **Trend, last and small**: "↓ 5% vs hace 7 días" arrows, plus a simple
   30-day line chart collapsed by default.

## 4. Distance

- **Store positions** are hand-entered once by the team in `sources.yaml`
  (optional `lat`/`lon` per source). No admin UI for them. A store without
  coordinates shows no distance and sorts last under the distance sort.
- **Math**: haversine straight-line distance, pure function in
  `dashboard/distance.py`. Display rounded: "850 m", "2,1 km" (Argentine comma).
- **Reference point**: defaults to the Activcelu shop (coordinates in config).
  A "Usar mi ubicación" button (browser geolocation, one tap after a one-time
  permission prompt) switches it to the user's current position for that visit
  only — nothing stored, nothing tracked, resets to the shop next visit. A
  small "volver al local" control undoes it. If permission is denied, the app
  says so plainly and stays on the shop.

## 5. Quick search ("Buscar precios ahora")

The counter-moment feature: fresh prices for one part in ~30–60 seconds.

- **New adapter mode**: instead of crawling catalogs, each adapter queries the
  store's own search page (e.g. `?q=<terms>`) with the tracked item's search
  terms, fetching only the first few result pages. A source with no usable
  search endpoint is skipped in quick mode with a visible note.
- **Parallel across stores, polite within each store**: all sources run
  concurrently, but each source keeps its own sequential 1-second-delay
  fetching. Total wall time ≈ the slowest store.
- Runs **inside the app process** with a live progress indicator
  ("Consultando MD Repuestos… 5/8"). Results are normalized and stored through
  the same storage path as the daily crawl, so analysis/margins pick them up
  immediately.
- **Caps**: max 10 quick searches per calendar day (shared counter, stored in
  the DB), one at a time (button disabled while running). The daily deep crawl
  is unchanged and remains the source of complete history.
- Lives in Ajustes and is also offered right after adding a new tracked part
  ("Se agregó. ¿Buscar precios ahora?").

## 6. Admin (Ajustes)

Two blocks plus the quick-search button. All writes go to the same tables the
team CLIs use (`service_prices`, `tracked_items`) with the same validation
rules (finite positive prices, quantized to centavos; non-empty labels). No
restart or deploy needed — changes show on next render.

- **Repair prices**: list with per-row Editar (one number field + Guardar),
  Agregar reparación (tracked-part dropdown + label + price), remove behind a
  "¿Seguro?" confirmation.
- **Tracked parts**: list, Agregar repuesto (name + search terms, with a plain
  Spanish hint and example of what search terms are), remove with the warning
  that history is kept but watching stops. After adding, the app offers quick
  search (see §5) so a new part is usable within a minute, not tomorrow.

## 7. Auth and hosting

- **Streamlit Community Cloud** (free), deployed from `main`; merges go live
  automatically. `DATABASE_URL`, the app password, and the GitHub token (if any
  future cloud-trigger needs one) live in Streamlit secrets, never in the repo.
- **One shared password** for the whole app (margins are business-sensitive).
  Constant-time comparison against the secret; a cookie remembers the login for
  ~30 days so the phone rarely re-asks.

## 8. Portfolio (explicit)

The repo doubles as Zahir's portfolio. Inside M4:

- README updated with **screenshots of the finished app** and an accurate M4
  section (the EN/ES toggle promise is replaced by the plan below).
- All screen text centralized in `text_es.py` (English-ready structure).

Committed follow-up after M4 (not gating the tool): a **public demo** — sample
data, no password, EN/ES toggle — so portfolio visitors can click and try it
without seeing Activcelu's real prices and margins.

## Module layout

```
src/repuestos_radar/
  dashboard/
    app.py          # Streamlit entry: login gate + navigation
    text_es.py      # every user-visible string
    home.py         # Precios page
    detail.py       # part detail page
    admin.py        # Ajustes page
    distance.py     # haversine + formatting (pure functions)
    quicksearch.py  # orchestrates parallel quick-search runs + daily cap
  adapters/…        # each adapter gains a search-mode entry point
```

Pages render dataclasses returned by `analysis`/`margin`/`quality`; formatting
helpers shared with `report.py` where sensible (e.g. `format_ars`).

## Error handling

- No data for a day/part/tier, missing coordinates, denied geolocation, quick
  search cap reached, a store failing mid-quick-search: all ordinary states
  rendered in plain Spanish ("sin datos de hoy", "no pudimos consultar
  Celuphone esta vez") — never stack traces, never hidden.
- Quick search failures for one store never abort the others; partial results
  are stored and labeled.

## Testing

- Pure logic gets unit tests: distance math and formatting, quick-search cap
  logic, adapter search-mode parsing (fixture HTML, no network), admin
  validation rules.
- Analysis math is already covered by M3's suite; the dashboard renders it.
- UI smoke: Streamlit pages import and render against a seeded in-memory DB
  (streamlit's AppTest), no pixel testing.
- No network access in any test. CI stays ruff + pytest.

## Out of scope for M4 (explicitly)

- Routing/maps APIs, live GPS tracking, automatic price+distance scoring
  (rejected).
- WhatsApp/alerts, forecasting (M5).
- The public portfolio demo (committed follow-up, after M4).
- Editing store coordinates from the admin page.

## Delivery

Same team workflow — small PRs, Mati codes with TDD, Lara reviews, Mo reviews
Spanish copy, Zahir merges each:

1. **PR 1**: adapter quick-search mode + parallel orchestrator + daily cap.
2. **PR 2**: distance module + store coordinates in `sources.yaml` + shop
   reference point config.
3. **PR 3**: dashboard core — login, home, part detail (price sort only).
4. **PR 4**: admin page + distance toggle + quick-search button + README
   screenshots.
