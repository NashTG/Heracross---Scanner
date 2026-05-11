# REQUIREMENTS — Stabilize + Improve Strategy Finder

## R1. Test suite for `polymarket_core.py`
- **Must:** pytest suite covers `slug_from_ts` (all 5 intervals × 4 assets), `generate_range_timestamps` (alignment correctness), `calc_net_pnl`, `closing_outcome`, `_get_usable`, `analyze` (synthetic fixtures), `simulate_strategy` (synthetic insights), `validate_oos`.
- **Must:** offline — no real network calls. Use response fixtures or `responses`/`pytest-httpserver`.
- **Must:** runnable via `pytest` from repo root with no setup beyond `pip install pytest requests`.
- **Should:** covers cache layer (`cache_get`/`cache_put`/`cache_clear`) using a temp DB.
- **Out:** integration tests against live Polymarket. Coverage targets / CI.

## R2. Self-updating diagnostics
- **Must:** `run_diagnostics` no longer uses hardcoded `ts = 1776960000`.
- **Must:** discovers a recent valid market per asset×interval via Gamma `/events` filtering (e.g., `closed=true&order=endDate&ascending=false&limit=20` then filter slugs by asset/interval pattern), probes that one, reports OK/fail.
- **Must:** falls back gracefully when no recent market matches a given asset×interval (return "no recent market" rather than crash).
- **Should:** caches the chosen probe slug per asset×interval for the session to avoid redundant search.

## R3. Error hardening + input validation
- **Must:** `/api/fetch` validates `start_date` and `end_date` are `YYYY-MM-DD` and `start_date <= end_date`. Returns 400 with a clean message on failure.
- **Must:** `/api/import` validates body is a list of dicts with required keys (`slug`, `up_snapshots`, `down_snapshots`). Rejects malformed payloads with 400.
- **Must:** Flask routes do not return raw `str(e)` from arbitrary exceptions. Use a sanitized error message; log full details server-side.
- **Should:** `fetch_market` catches `requests.HTTPError` and other `requests` exceptions in addition to `RuntimeError`.
- **Should:** UI shows user-friendly error messages; raw stack traces never reach the client.

## R4. Slug resilience — search-then-derive
- **Must:** when `resolve_slug(constructed_slug)` returns no event, fall back to a Gamma `/events` filtered search keyed by asset and the target window timestamp (e.g., search for events whose start time falls within the window and whose slug contains the asset short-name).
- **Must:** if found, use the discovered slug and cache the mapping `(asset, interval, ts) → real_slug` for the session.
- **Must:** preserves existing fast path (constructed slug works → no search overhead).
- **Should:** structured logging of fallback hits so we can quantify upstream drift.

## R5. Buscador extension — first-market discovery
- **Must:** `buscador.py` exposes a function/CLI mode that, for every (asset ∈ {BTC, ETH, SOL, XRP}) × (interval ∈ {5m, 15m, 1h, 4h, 1d}), discovers the **oldest** market matching that recurring pattern.
- **Must:** outputs a structured table (JSON or markdown) with: asset, interval, oldest market slug, oldest start timestamp, status (found / not-found).
- **Should:** uses Gamma `/events` pagination + filters; respects rate limits.
- **Should:** consumed by `run_diagnostics` (R2) and the slug-resilience layer (R4) where useful.

## R6. Strategy finder — parameter grid expansion (configurable)
- **Must:** the minute set, delta thresholds, and entry caps used by `analyze` are no longer hardcoded — exposed as parameters with sensible defaults.
- **Must:** UI surfaces these parameters in the Analyze tab (collapsible "Advanced" section), defaulting to current values.
- **Should:** parameter changes are reflected in the snapshot row so historical runs are reproducible.

## R7. Strategy finder — new strategy classes
- **Must:** add at least two of:
  - **Momentum streak:** N consecutive UP (or DOWN) closes in prior windows → buy same side next.
  - **Cross-asset lead/lag:** BTC delta at minute M predicts SOL/ETH/XRP outcome.
  - **Volatility regime:** classify each window as low/med/high vol from Binance candle range; report strategy performance per regime.
- **Must:** new strategy classes follow the same insight-dict shape (`title`, `confidence`, `support`, `avg_entry`, `avg_net_pnl`, `bottomline`, `samples`) so UI/simulator handle them without changes.
- **Should:** each new strategy class can be toggled on/off to keep noise down.

## R8. Strategy finder — statistical rigor
- **Must:** OOS validation supports **walk-forward** in addition to single-split (≥3 folds when data permits).
- **Must:** report a 95% confidence interval (bootstrap or Wilson) on each insight's hit-rate / EV.
- **Must:** apply a multiple-test correction (Benjamini–Hochberg or Bonferroni) on the candidate-strategy grid before publishing the survivors.
- **Should:** out-of-sample numbers are shown alongside training numbers in the UI for each insight.

## R9. EV / Sharpe gate replaces 70% hit-rate
- **Must:** strategies are filtered/ranked by **expected value per trade** (in cents, fee-adjusted) and **per-trade Sharpe-like ratio** (mean / stdev of trade P&L), not raw hit rate.
- **Must:** UI surfaces EV and Sharpe on each insight in addition to confidence.
- **Must:** simulator uses the same gate by default.
- **Should:** legacy hit-rate threshold remains visible but is configurable, default off.

## Non-Functional
- Backwards-compatible with existing `polymarket_cache.db` rows.
- Backwards-compatible with existing `analysis_snapshots.json` (new fields additive).
- No regressions in current "happy path" Analyze flow.
- Single-user, local-only assumption preserved.
