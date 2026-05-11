# ARCHITECTURE

## Overview
Single-process Flask web app. Fetches Polymarket up/down binary markets + Binance reference prices, caches in SQLite, runs strategy discovery and backtests, renders a single-page HTML UI.

## Components

### `app.py` — Web layer
- Flask routes serving inline HTML and JSON APIs.
- In-process state: `progress_state`, `last_results`, `last_analysis` (module-level globals; **single-user, not thread-safe**).
- Routes:
  - `GET /` — single-page UI.
  - `GET /api/progress` — fetch progress polling.
  - `POST /api/fetch` — kick off range fetch + analysis + snapshot save.
  - `GET /api/diagnose` — probe one slug per asset×interval.
  - `GET /api/oos` — split-half train/validate.
  - `POST /api/simulate` — backtest a chosen insight.
  - `GET /api/export`, `POST /api/import` — JSON dataset transfer.
  - `GET /api/snapshots`, `POST /api/clear_cache`.

### `polymarket_core.py` — Domain library (canonical)
- HTTP: `fetch_json` (retry wrapper).
- Slug logic: `slug_from_ts`, `generate_range_timestamps` (interval-specific alignment: 5m/15m to ET-midnight, 4h to UTC, 1h to ET hour, 1d to noon ET).
- API: `resolve_slug`, `fetch_price_history`, `fetch_asset_candles`.
- Cache: `cache_get/put/count/clear` (SQLite).
- Pipeline: `fetch_market` → `fetch_range` (sequential, sleep 0.5s).
- Analysis: `analyze` (correlation, time-of-day, outcome strategies, volatility strategies), `validate_oos`, `simulate_strategy`.
- Persistence: `save_snapshot`, `load_snapshots`, `export_markets`, `import_markets`.
- Diagnostics: `run_diagnostics`.

### `analyzer.py` — Legacy/duplicate library
- Older standalone version of the same domain logic. **Not imported by `app.py`.** Likely predecessor.

### `buscador.py` — Slug discovery tool
- Async (aiohttp) Polymarket slug explorer.
- Standalone script — not wired into `app.py`.

## Data Flow (Fetch → Analyze)
1. UI POSTs `/api/fetch` with asset, interval, date range.
2. `generate_range_timestamps` enumerates aligned window starts.
3. For each ts: `slug_from_ts` → `cache_get` (SQLite) → on miss, `resolve_slug` (Gamma) + `fetch_price_history` ×2 (CLOB) + `fetch_asset_candles` (Binance) → `cache_put`.
4. `analyze` computes UP/DOWN closes, BTC↔price correlation, ToD segments, scans threshold/minute combos for outcome rules and entry-cap combos for volatility rules.
5. `save_snapshot` appends meta to `analysis_snapshots.json`.
6. JSON returned; UI renders strategies, samples, ToD breakdown.

## Concurrency Model
- Synchronous, single-threaded request handling (Flask dev server, `debug=False`).
- Globals shared across requests — concurrent `/api/fetch` calls would corrupt `last_results` / `progress_state`.

## Entry Points
- `python app.py` → http://127.0.0.1:8080.
- `analyzer.py` and `buscador.py` are not orchestrated.
