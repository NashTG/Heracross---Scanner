# PHASE 1 — Test Foundation

## Goal
Establish a pytest regression net over the **pure-function** core of `polymarket_core.py` so subsequent phases (error hardening, slug resilience, strategy changes) can refactor with confidence.

**Tight scope:** pure functions only. Cache layer and `validate_oos` deferred (they need DB or composed-function fixtures and add little safety margin per unit time invested at this stage).

## Coverage Target (this phase)
- `slug_from_ts` — all 5 intervals × 4 assets, edge cases (DST boundary, year transition, lowercase month).
- `generate_range_timestamps` — alignment per interval, single-day vs multi-day ranges, deduplication of overlap.
- `calc_net_pnl` — fee math at boundaries (0c, 100c, 50c entry/exit, fractional cents).
- `closing_outcome` — UP/DOWN/None for empty snapshots.
- `_get_usable` — filters out errored, empty-snapshot, no-outcome rows.
- `_range_cents` — empty list and single-point edge cases.
- `analyze` — synthetic markets dataset, asserts on:
  - up_closes / down_closes counts
  - correlation sign with planted BTC delta
  - tod_segments produces 6 buckets covering 24h
  - outcome insight emerges when planted scenario meets 70% gate
  - profit insight emerges when planted scenario has positive net
  - sort order (confidence desc, EV desc tiebreak)
  - top-8 cap on each list
- `simulate_strategy` — outcome and volatility insight types:
  - hit_rate / wins / losses arithmetic
  - max_drawdown tracks peak-to-trough
  - bet_size scales total_pnl linearly
  - filter conditions (threshold for outcome, entry_cap for volatility) skip non-matching markets

**Deferred to a later phase:**
- `cache_get/put/count/clear` (DB tests).
- `fetch_market`, `fetch_range`, `resolve_slug`, `fetch_price_history`, `fetch_asset_candles` (HTTP integration).
- `run_diagnostics` (will be rewritten in Phase 4).
- `validate_oos` (composes `analyze`; covered transitively).
- `save_snapshot` / `load_snapshots` / `export_markets` / `import_markets`.

## Deliverables

### 1. `requirements-dev.txt`
```
pytest>=7.4
responses>=0.24
requests>=2.31
```
Note: this is **dev-only**, not the deferred runtime `requirements.txt` (out of milestone scope).

### 2. `tests/` directory
```
tests/
├── __init__.py            (empty, makes tests a package)
├── conftest.py            (shared fixtures)
├── fixtures/
│   ├── gamma_event.json   (one canned Gamma event response)
│   ├── clob_history.json  (one canned CLOB prices-history)
│   └── binance_klines.json (one canned Binance kline page)
├── test_slugs.py          (slug_from_ts + generate_range_timestamps)
├── test_pnl.py            (calc_net_pnl)
├── test_filters.py        (closing_outcome, _get_usable, _range_cents)
├── test_analyze.py        (analyze synthetic dataset)
└── test_simulator.py      (simulate_strategy)
```

### 3. Synthetic dataset helper
`tests/conftest.py` exposes:
- `make_market(slug, ts, up_prices, down_prices, btc_deltas, ...)` — builds a market dict with the schema `analyze` expects.
- `synthetic_dataset(n_markets, asset, interval, scenarios)` — composes N markets to drive specific test scenarios (e.g., "20 markets where BTC is +$50 by minute 1 and UP wins 80% of the time" → asserts an outcome insight emerges).

### 4. Acceptance
- `pytest` from repo root: green, **zero network calls** (verified via `responses.activate` strict mode where applicable; pure-function tests don't need it).
- Each test file < 200 lines.
- No flakiness (deterministic synthetic data, fixed seeds where randomness used).

## Tasks

### T1. Add dev dependencies
- Create `requirements-dev.txt` at repo root with the three pinned dev libs.
- One-line `README.md` snippet (or comment in conftest) documenting `pip install -r requirements-dev.txt && pytest` to run.
- **No** changes to runtime imports.

### T2. Scaffolding
- Create `tests/__init__.py` (empty).
- Create `tests/conftest.py` with:
  - `make_market(...)` factory with sensible defaults.
  - `synthetic_dataset(...)` builder.
  - Shared fixture for ET timezone reference timestamps.
- Add `tests/fixtures/*.json` with minimal valid responses (we may not use them in this phase but they belong here for Phase 2/4).

### T3. `test_slugs.py`
- Parametrized over all 4 assets × 5 intervals: `slug_from_ts(ts, asset, interval)` matches expected pattern (regex check, since exact strings depend on date math).
- Specific cases: ET-midnight day flip, DST spring-forward / fall-back, year boundary (Dec 31 → Jan 1).
- `generate_range_timestamps`: single-day 5m → 288 timestamps; 4h alignment to UTC; 1h alignment to ET; 1d alignment to noon-ET; multi-day dedup.

### T4. `test_pnl.py`
- `calc_net_pnl(entry, exit)` table-driven:
  - Win at entry=50, exit=100 → expected `100*0.99 - 50*1.082 = 99 - 54.1 = 44.9`.
  - Loss at entry=50, exit=0 → `0 - 50*1.082 = -54.1`.
  - Break-even hunt: entry where exit=100 yields zero net (≈ 91.5c).
  - Fractional and zero edges.

### T5. `test_filters.py`
- `closing_outcome` for: missing up, missing down, both empty, UP > DOWN, DOWN > UP, equal (current code: equal → DOWN since `>` not `>=` — assert observed behavior, document if surprising).
- `_get_usable` filters: errored, empty up, empty down, no outcome.
- `_range_cents` for empty list (returns 0), one point (returns 0), multi-point spread.

### T6. `test_analyze.py`
- Synthetic dataset of 50 markets with planted patterns.
- Assert: counts, correlation sign, ToD segments shape, expected insight surfaces, sort order, top-8 truncation.
- Assert: synthetic dataset producing zero qualifying scenarios → empty `outcome_insights` and `profit_insights` (no spurious findings).

### T7. `test_simulator.py`
- Outcome insight: build a market list where the threshold filter selects exactly 10, of which 7 win → assert wins=7, losses=3, hit_rate=70.
- Volatility insight: build markets where future max > entry for 6/10 → assert wins=6, hit_rate=60.
- Bet-size scaling: same dataset, bet_size=100 vs 200 → total_pnl ratio = 2.0 ± float tolerance.
- Drawdown: planted loss-streak → max_drawdown matches expected peak-to-trough.

## Out of Scope (this phase)
- HTTP-mocked tests for `fetch_market` etc. (deferred until Phase 2 needs them for error-path coverage).
- CI configuration.
- Coverage reporting / thresholds.
- `requirements.txt` runtime pin.
- Type hints rollout.

## Risks / Notes
- `analyze` is large (140+ lines) — the synthetic-dataset approach is more maintainable than freezing exact numeric outputs. Tests assert *structural* properties (an insight exists with X confidence, sort order is correct) rather than exact float equality.
- `equal close` edge in `closing_outcome` — the `>` (not `>=`) means a tied close returns `"DOWN"`. Test will pin this behavior; if user wants different semantics, that's a Phase 2 fix on top of a now-trusted test.
- Slug regex tests are tolerant of whitespace and case (lowercase month name); we don't assert exact integer values for timestamp-based slugs (5m/15m/4h) since those are derived from the input ts and would be circular to assert.

## Success Criteria (verifier checklist)
- [ ] `pip install -r requirements-dev.txt` works on a clean venv.
- [ ] `pytest` from repo root: all tests pass, zero network connections (verify by disabling network or via `responses` strict mode).
- [ ] Test files cover the in-scope functions listed above.
- [ ] Tests are deterministic (run 3× → identical pass).
- [ ] No imports from `app.py` (Flask app should not be required to run tests).
- [ ] No mutation of `polymarket_core.py` source under test (refactors are out of scope this phase).

## Estimated effort
~1 focused session. Synthetic dataset helper is the longest pole (~30% of effort). Six small test files + one fixture file otherwise.

## After this phase
- Phase 2 (Error Hardening) can lean on these fixtures + add HTTP-mocked tests for `fetch_market` error branches.
- Subsequent phases can refactor `analyze` / `simulate_strategy` with regression confidence.
