# ROADMAP — Stabilize + Improve Strategy Finder

Sequenced so each phase reduces risk for the next. Stabilization (tests + hardening) lands before strategy changes so we have a regression net before mutating analysis logic.

---

## Phase 1 — Test Foundation
**Covers:** R1
**Goal:** Pytest suite green on all of `polymarket_core.py` core logic before any refactor.

- Add `pytest` + `requests` to a `requirements-dev.txt` (lightweight; no full env pin yet).
- Create `tests/` with fixtures for Gamma, CLOB, Binance JSON shapes.
- Tests for: `slug_from_ts`, `generate_range_timestamps`, `calc_net_pnl`, `closing_outcome`, `_get_usable`, `analyze`, `simulate_strategy`, `validate_oos`, cache layer (temp DB).
- HTTP mocked via `responses` library or pytest-httpserver.
- Acceptance: `pytest` from repo root, all green, no network.

---

## Phase 2 — Error Hardening + Input Validation
**Covers:** R3
**Goal:** No raw exceptions reach UI; malformed input is rejected cleanly.

- `/api/fetch` and `/api/import` validate inputs.
- Flask error handler sanitizes responses.
- `fetch_market` catches broader `requests` exception family.
- Tests in Phase 1 extended for new validation paths.
- Acceptance: bad date / bad import payload returns 400 + clean message; no traceback ever serialized to the client.

---

## Phase 3 — Buscador Extension: Oldest-Market Discovery
**Covers:** R5
**Goal:** Authoritative table of oldest markets per asset × interval.

- Extend `buscador.py` with a `discover_oldest_per_pattern()` mode/CLI.
- Iterates Gamma pagination; identifies oldest matching slug per (asset, interval).
- Outputs `.planning/research/oldest-markets.json` (and a markdown rendering).
- Re-verifies the cases the prior `test.r/test.sh` heredoc had marked "never launched" (especially 5m / 4h, which user confirms exist).
- Acceptance: table exists for all 20 (asset × interval) combos; consumed by Phase 4.

---

## Phase 4 — Self-Updating Diagnostics + Slug Resilience
**Covers:** R2, R4
**Goal:** Diagnose tab works without code edits as time passes; slug-template drift no longer breaks pipeline.

- `run_diagnostics` discovers a recent valid market via Gamma search per asset×interval.
- Search-then-derive fallback in `fetch_market`: on constructed-slug 404, query Gamma `/events` filtered by asset and window timestamp; cache real-slug mapping.
- Structured log of fallback hits (counted in summary).
- Acceptance: deleting last-month's hardcoded ts has zero effect; Diagnose tab still passes; slug-format change in one interval doesn't kill the whole fetch.

---

## Phase 5 — Strategy Finder: EV/Sharpe Gate + Configurable Grid
**Covers:** R6, R9
**Goal:** Strategies ranked by fee-adjusted expected value, not raw hit-rate. Parameter grid configurable.

- `analyze` accepts grid params (minutes, deltas, entry caps); UI exposes them in collapsible "Advanced" section.
- Compute EV (mean net P&L per trade in cents) and Sharpe-like (mean / stdev) on each candidate.
- Filter and sort by EV (descending), with stdev/Sharpe as tiebreakers; legacy hit-rate threshold becomes a configurable knob, default off.
- UI insight cards show EV, Sharpe alongside confidence.
- Snapshot row records the grid params used.
- Acceptance: same dataset re-run reproduces results from snapshot params; insights ranked by EV.

---

## Phase 6 — Statistical Rigor: Walk-Forward + CIs + Multiple-Test Correction
**Covers:** R8
**Goal:** Survivors of the strategy grid are statistically defensible.

- Walk-forward (≥3 folds when data permits) replaces or augments single-split OOS.
- Bootstrap (or Wilson) 95% CI on each insight's hit-rate and EV.
- Benjamini–Hochberg correction on the full candidate grid; only surface survivors.
- UI shows in-sample vs out-of-sample numbers per insight.
- Acceptance: dropping the BH correction would surface markedly more (mostly noisy) strategies; with it, survivors hold up across folds.

---

## Phase 7 — New Strategy Classes
**Covers:** R7
**Goal:** Beyond minute/threshold scans — momentum, cross-asset, regimes.

- Implement at least two of: momentum streak, cross-asset lead/lag (BTC → SOL/ETH/XRP), volatility regime conditional.
- Each emits the standard insight dict; UI/simulator changes are zero.
- Toggle on/off in UI.
- Acceptance: each new class produces at least one finding on a sufficiently large fetch; simulator handles them unchanged.

---

## Out of Scope (this milestone)
- `requirements.txt` + Python version pin.
- WSGI productionization, multi-user concurrency.
- Auth, CSRF.
- Live monitoring / alerts UI.

## Risks
- **Slug schema changes mid-milestone** — Phase 4 mitigates partially; long-term unfixable from our side.
- **CLOB 12h-resolution cliff for resolved markets** — affects re-fetches of historical fine-grained data; document in UI during Phase 4.
- **Walk-forward fold count** depends on dataset size; small ranges may degrade to single-split.

## Phase Order Rationale
1. Tests first → safety net for everything else.
2. Error hardening → low-risk, high-value, builds on test fixtures.
3. Buscador discovery → produces data Phase 4 needs.
4. Resilience layer → uses Phase 3 output, makes the rest of the project durable.
5. EV gate before stat rigor → easier to layer CIs and correction on top of an EV-ranked candidate set.
6. Stat rigor → tightens the candidate set before adding new classes (so we don't bake noise into new strategies).
7. New strategy classes last → bigger surface area, lower priority than fixing the analytical foundation.
