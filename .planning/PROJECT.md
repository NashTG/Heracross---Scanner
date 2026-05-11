# PROJECT: AnalyzerDiagnosis

## What
Polymarket Up/Down binary-market analyzer. Flask single-page app fetches per-window markets (BTC/ETH/SOL/XRP × 5m/15m/1h/4h/1d), enriches with Binance candles, caches in SQLite, and discovers fee-adjusted outcome and volatility strategies. Backtests strategies against historical fetches.

## Why
Local research tool. Surface profitable, fee-aware patterns in Polymarket's high-frequency up/down markets. Validate with out-of-sample splits before risking capital.

## Current State (Brownfield)
- `app.py` (374 lines) — Flask + inline HTML/JS UI.
- `polymarket_core.py` (600 lines) — canonical domain library.
- `analyzer.py` (720 lines) — **legacy duplicate**, unused.
- `buscador.py` (377 lines) — async slug-discovery tool, standalone.
- SQLite cache + JSON snapshots.
- **No tests, no requirements.txt, no CI.**

## Milestone Goal
**Stabilize + improve.** Reduce refactor risk on the analyzer, harden against upstream Polymarket drift, and upgrade strategy discovery from hit-rate-only to expected-value-aware.

## In Scope
1. **Tidy** — delete `analyzer.py` (legacy), `test.r`/`test.sh` (heredocs), buscador json outputs, `__pycache__`. Add `.gitignore`. **(done at project init.)**
2. Pytest suite for core domain logic (slug, fee math, analyze, simulate).
3. Fix hardcoded `run_diagnostics` timestamps — auto-pick recent valid probe per asset/interval via Gamma search.
4. Harden errors and input validation (date parse, `/api/import` schema, no leaked tracebacks).
5. Slug-schema resilience — search-then-derive fallback (Gamma `/events` filter) when constructed slug 404s. Eliminates per-interval template fragility.
6. **Extend `buscador.py`** to discover the first (oldest) market of every recurring crypto slug pattern across all asset × interval combinations. Feeds slug-resilience and diagnostics work.
7. Improve strategy finder across three threads:
   - Expand parameter grid (configurable minutes/thresholds/entry caps).
   - Add new strategy classes (momentum streaks, cross-asset lead/lag, volatility regimes).
   - Better statistical rigor (walk-forward validation, CIs, multiple-test correction).
8. Replace 70% hit-rate gate with Sharpe / expected-value gate.

## Out of Scope (this milestone)
- requirements.txt + Python version pin.
- Multi-user / WSGI productionization.
- Authentication, CSRF.
- Live monitoring / alerts.
- Concurrency-safe global state rewrite.

## Constraints
- Single-user local tool. No deployment target.
- Python 3.13/3.14, Flask, requests, sqlite3 — keep stack.
- Existing SQLite cache must remain readable post-refactor.
- Slug-format coupling is fragile (Polymarket changes break everything) — design for resilience.

## Success
- `analyzer.py` removed or fully merged; one canonical core.
- `pytest` green on core logic; CI not required, but suite runnable locally.
- Diagnose tab works without code edits as months pass.
- Strategy finder ranks by expected value and reports walk-forward stats.
- `/api/fetch` / `/api/import` reject malformed input cleanly.

## References
- Codebase map: `.planning/codebase/`
- Research: `.planning/research/`
