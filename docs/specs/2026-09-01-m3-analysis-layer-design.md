# M3 — Analysis Layer: Design

Date: 2026-09-01
Status: approved by Zahir (chat), pending final spec review
Scope: analysis engine + internal report CLI. The end-user surface is the M4
dashboard; nothing in M3 is aimed directly at the end user.

## Purpose

Turn the listings the ingestion pipeline already stores into the answers
Activcelu actually needs, in this priority order (Zahir, 2026-09-01):

1. **Best place to buy** a part today.
2. **Fair price** for a part today.
3. **Margin** on a repair (part cost vs. what Activcelu charges).
4. Price history / trends (lower priority, included because it is cheap).

## Hard constraints

- **End users are not programmers.** The real user (Zahir's dad) uses a
  cellphone first, PC second. Every CLI in this milestone is an internal team
  tool; the user-facing surface is the M4 dashboard (phone-first, Spanish).
  M3's outputs must map 1:1 onto that dashboard's main screen: search a part →
  best store per quality tier, fair price, margin, warnings, in plain Spanish.
- **Architecture decision (approved): compute on demand.** Analysis is pure
  functions over the existing `listings` + `tracked_items` tables. No
  precomputed stats tables, no SQL views. Rationale: daily data volume is tiny
  (hundreds of rows), computation is sub-second, and rule changes (word lists,
  outlier thresholds) must re-apply to all history for free. The only new
  table is the repair price list (below).
- Nothing in M3 makes HTTP requests. All inputs come from the database.
- Language conventions unchanged: code/comments/commits in English; report
  output in Spanish (Rioplatense-professional, Mo reviews).

## 1. Quality tiers (`quality.py`)

Every relevant listing gets exactly one tier label, computed at analysis time
from words in its title — never stored, so tuning the word list relabels all
history automatically.

Part tiers, from humbler to better:

| Tier | Title signals (accent/case-insensitive, same `normalize()` machinery as relevance) |
|---|---|
| `incell` | "incell", "in-cell", "tft" |
| `oled` | "oled", "amoled" |
| `original` | "original", "service pack", "genuine" |
| `unlabeled` | none of the above |

- **Frame detail**: "con marco" / "sin marco" is captured as a separate
  boolean-ish detail (`frame: with | without | unknown`), not a tier — it
  modifies price within a tier.
- **Conflict rule**: if a title matches signals from two tiers ("pantalla OLED
  calidad original"), the humbler tier wins (here `oled`). Sellers oversell;
  trust the humbler word. This rule is deterministic and tested.
- `unlabeled` is a first-class group: always shown, never merged into other
  tiers, never hidden.
- **Whole devices** (GoFix / One Store listings) use a parallel two-value
  scheme instead: `nuevo` / `reacondicionado` (title signals: "reacondicionado",
  "refurbished", "usado" → `reacondicionado`; "nuevo", "sellado", "caja
  sellada" → `nuevo`; neither → `unlabeled`). Title signals only — no
  per-store defaults. Part tiers and device tiers never mix.
- Word lists live as module-level constants (same style as the relevance
  blocklists) so "why did this get this label?" is always answerable.

## 2. Best place to buy (`analysis.py`)

Per (tracked item, tier), over the **latest ingestion day** only, listings
classified `match` or `low_confidence`:

- Per store, only its cheapest matching listing competes.
- Output: ranked list of stores — price, listing title, URL, relevance flag.
  Rank 1 is "the winner"; the full ranked list is what the M4 dashboard shows
  (and where M4's distance column will attach).
- `low_confidence` listings are included but carry a visible "revisar" flag;
  they are never silently mixed with sure matches.
- Outlier-flagged listings (see §4) are shown in the ranking with their flag
  but excluded from §3's fair-price math.

## 3. Fair price (`analysis.py`)

Per (tracked item, tier), same input set minus outliers:

- **Fair price = median** of the per-store cheapest prices. Median, not mean:
  one wild price must not drag the number.
- **Small-sample honesty**: the result always carries `store_count` and the
  min–max range. Consumers (report, dashboard) must render the range alongside
  the median when `store_count <= 3`.
- With one store there is no "fair price" — the output says so explicitly
  (`basis: single-store`) rather than dressing one price up as a market
  statistic.

## 4. Outlier detection (`analysis.py`)

Deliberately simple and conservative (groups are small):

- Within a (tracked item, tier) group, a per-store cheapest price is flagged
  when it is **< 0.5× or > 2× the group median** (median computed over the
  group including the candidate, standard leave-nothing-out median — with
  groups this small, fancier schemes overfit).
- Groups with **fewer than 4 stores are never flagged** — too little data to
  call anything weird.
- Flagged listings: excluded from fair-price math, still shown everywhere
  else with a human explanation ("precio muy bajo para calidad OLED —
  revisar: puede ser error, calidad mal etiquetada o una oferta real").
  Nothing is ever dropped; the human decides.

## 5. Margin calculator (`margin.py` + new table)

- **New table `service_prices`**: one row per repair Activcelu offers —
  `id`, `tracked_item_id` (FK), `label` (e.g. "Cambio módulo A32"),
  `price_ars` (Decimal), `updated_at`. Created via the same metadata/DDL path
  as existing tables. This is the only schema change in M3.
- **Margin per (repair, tier)** = service price − cheapest non-outlier part
  price in that tier, so the answer is naturally tier-aware: "con incell
  ganás $54.300; con OLED $41.200".
- Amendment (PR #17): the margin basis must also be a sure match
  (`relevance = match`) — a low-confidence listing is never the basis of a
  margin, since the margin line carries no "revisar" flag.
- Maintained now via an internal CLI (`python -m repuestos_radar.services`
  add/list/set-price/remove — mirroring the `tracked` CLI patterns); the
  user-facing editor is the M4 admin page (phone-first). The CLI is a team
  tool, not dad's interface.

## 6. Price history (`analysis.py`)

- Per (tracked item, tier): fair price today vs. 7 and 30 days ago (nearest
  stored day within a small tolerance window), with direction `↑ ↓ =` and the
  percent change. No forecasting, no charting in M3 (charts are M4).

## 7. Daily report CLI (`report.py`, internal tool)

`python -m repuestos_radar.report` prints the day's summary in Spanish:

- One section per tracked item; within it, per tier: best store (name, not
  slug), fair price with range when `store_count <= 3`, margin when a service
  price exists, trend arrows, and warnings spelled out in words.
- Prices formatted Argentine-style (`$20.700`).
- Purpose: (a) lets the team and Zahir use M3 daily before M4 exists;
  (b) forces the analysis outputs to be renderable for humans — the same
  structures the dashboard will consume. It is NOT the end-user product.
- Report copy is user-facing text → Mo reviews it.

## Module layout

```
src/repuestos_radar/
  quality.py    # tier + device-condition labeling (pure functions)
  analysis.py   # best-place, fair price, outliers, history (pure functions over rows)
  margin.py     # service_prices access + margin math
  report.py     # Spanish rendering + __main__ entry
  services.py   # python -m repuestos_radar.services CLI (price-list management)
```

`analysis.py` functions take plain sequences of listing rows (or a Session) and
return small dataclasses — no printing, no formatting. `report.py` owns all
Spanish text. The M4 dashboard imports `analysis`/`margin`/`quality` directly
and never parses report text.

## Error handling

- Empty groups, missing days, no service price, single-store groups: all are
  ordinary, explicitly-represented outcomes (fields like `basis`,
  `store_count`), never exceptions.
- The report renders whatever exists and says plainly what is missing
  ("sin datos de hoy para <item>") instead of failing.

## Testing

- Unit tests with hand-built rows where the right answer is known by hand:
  tier labeling (incl. the conflict rule and accents), median/fair price,
  small-sample range behavior, outlier thresholds and the <4-store guard,
  margin per tier, history windows, report sections and Argentine price
  formatting.
- A fixture reproducing the real A32 two-tier spread (ARS 20.7k–58.7k,
  2026-08-31 data) as an end-to-end sanity case.
- No network access in any test. CI stays ruff + pytest.

## Out of scope for M3 (explicitly)

- Dashboard/UI of any kind (M4); distance feature (M4, approved separately).
- WhatsApp/alerts (M5).
- Forecasting; automatic price+distance "best option" scoring (rejected).
- Storing tier labels or precomputed stats in the DB.

## Delivery

Same team workflow: small PRs (suggested split — 1: quality tiers; 2: analysis
core: best-place + fair price + outliers; 3: service_prices + margin + CLI;
4: history + report), Mati codes with TDD, Lara reviews all, Mo reviews the
report's Spanish, Zahir merges each.
