# PHASE 4 — Self-Updating Diagnostics + Slug Resilience

## Goal
Two failure modes go away:
1. `run_diagnostics` no longer rots — it always probes the most recent known-good slugs from Phase 3's sweep.
2. A single slug-template change in one interval no longer kills the fetch pipeline — `fetch_market` searches Gamma for the real slug when the constructed one 404s, then caches the alias.

## Context (from research + decisions)
- `polymarket-api-resilience.md` — slug schemas live in Polymarket's editorial layer; we cannot make `slug_from_ts` drift-proof. Mitigation: keep template as fast path, add search-then-derive fallback.
- `oldest-markets.json` (Phase 3) — full 20-combo Cartesian sweep, real slugs + condition_ids. Phase 4 consumes this.
- STATE.md decision (2026-05-07) — "Slug resilience uses search-then-derive fallback, not full template rewrite."
- Discuss-phase decisions:
  - Diagnostics probe source: **read `oldest-markets.json`** (no live network).
  - Fallback policy: **search Gamma + cache alias** in SQLite.
  - Test scope: **mock 404 then search hit** (full coverage on the new path).

## Decisions (from discuss)
- **Cache schema:** new `slug_aliases (derived_slug TEXT PK, real_slug TEXT, asset TEXT, interval TEXT, window_ts INT, resolved_at INT)` table in same `polymarket_cache.db`. Backwards compatible — existing `markets` table untouched.
- **Search query:** `GET /events?slug_contains={pattern_stem}&start_date_min=<window-1d>&start_date_max=<window+1d>&closed=true&limit=50`. Pattern stem is reused from `buscador.pattern_stems`.
- **Match selection:** of returned events, pick the one whose first market's `startDate` is closest to `window_ts`. If none within ±1d, return `None` (treated as fetch error, no infinite search loop).
- **Fallback metrics:** module-level `_fallback_stats = {"hits": 0, "misses": 0, "by_combo": {}}` counter. `/api/fetch` response gets a new `fallback_hits` field. UI display deferred — counter visible via dev console for now.
- **Diagnostics:** load `.planning/research/oldest-markets.json` once at start; for each combo with `found: true`, probe the cached slug through `resolve_slug` + `fetch_price_history`. Combos with `found: false` reported as warnings, not errors.

## Deliverables

### Code changes — `polymarket_core.py`

#### Cache layer (new functions)
- `_init_alias_table(conn)` — `CREATE TABLE IF NOT EXISTS slug_aliases (...)` called from `get_db()`.
- `alias_get(derived_slug) -> Optional[str]` — return real_slug if present.
- `alias_put(derived_slug, real_slug, asset, interval, window_ts)` — insert-or-replace; sets `resolved_at = int(time.time())`.

#### Search-then-derive (new function)
- `search_market_by_window(asset, interval, window_ts) -> Optional[dict]`:
  - Build pattern_stems from `buscador.pattern_stems(asset, interval)` (import at module top).
  - For each stem, call `fetch_json(GAMMA_API + "/events", {"slug_contains": stem, "start_date_min": ..., "start_date_max": ..., "closed": "true", "limit": 50})`.
  - Filter results: keep events whose first market `startDate` is within ±86400s of `window_ts`.
  - Return the closest-matching event's market dict (slug, token_ids, outcomes, question), or `None`.

#### `fetch_market` rewrite
- After computing `start_ts`, call `alias_get(slug)` first. If hit, swap `slug` to alias, increment `_fallback_stats["hits"]`, continue with normal path.
- If `resolve_slug(slug)` returns `None`:
  - Call `search_market_by_window(asset, interval, start_ts)`.
  - On hit: `alias_put(...)`, set `slug` to real one, increment `_fallback_stats["hits"]`, continue.
  - On miss: increment `_fallback_stats["misses"]`, set `result["error"] = "Slug not found and search failed"`, return.
- The `result` dict gets two new fields: `derived_slug` (original constructed) and `real_slug` (post-fallback) so callers can see what happened.

#### Fallback stats accessors (new functions)
- `get_fallback_stats() -> dict` — returns shallow copy of `_fallback_stats`.
- `reset_fallback_stats()` — zeroes counters. Called at start of `fetch_range`.

#### `run_diagnostics` rewrite
- Load `.planning/research/oldest-markets.json` once.
- For each (asset, interval) in the cross-product:
  - If JSON has `found: true` → probe its `slug` via `resolve_slug` + `fetch_price_history`.
  - If `found: false` → mark as `warning`, message `"No market in Phase 3 sweep"`.
- Return list of dicts with new `source` field: `"sweep"` or `"none"`.
- If JSON missing or unreadable → fall back to one-shot warning row per combo, no hardcoded ts.

### Code changes — `app.py`
- `/api/fetch` response: include `fallback_hits` and `fallback_misses` from `get_fallback_stats()` in the summary.
- Wrap `fetch_range` in `reset_fallback_stats()` → `fetch_range(...)` → `get_fallback_stats()` so each fetch session reports its own counts.

### Tests — `tests/test_resilience.py` (new file)
- `test_alias_get_miss_returns_none`
- `test_alias_put_then_get_roundtrip`
- `test_search_finds_market_in_window` (mock 1 stem × 1 hit)
- `test_search_returns_none_when_outside_window` (mock returns event with startDate far from window_ts)
- `test_search_returns_none_when_no_matches` (mock empty)
- `test_fetch_market_uses_alias_when_present` (pre-populate alias, assert no /events call for derived slug)
- `test_fetch_market_falls_back_on_resolve_miss` (mock resolve_slug 404 → search hit → caches alias)
- `test_fetch_market_returns_error_on_search_miss` (mock both miss → result.error set)
- `test_fallback_stats_increments` (one fetch with fallback, assert stats hits=1)
- `test_run_diagnostics_uses_sweep_json` (write a tmp oldest-markets.json, monkeypatch path, assert probes use those slugs)
- `test_run_diagnostics_handles_missing_json` (delete file, assert graceful warning rows)

### Tests — `tests/test_app_errors.py` (extension)
- `test_fetch_response_includes_fallback_hits` — call `/api/fetch` with mocked successful range, assert `fallback_hits` key in response.

## Tasks

### T1. Slug-alias cache layer
- Add `slug_aliases` table creation to `get_db()`.
- Add `alias_get`, `alias_put` functions next to existing `cache_get`/`cache_put`.
- 2 tests: miss-returns-none, put-then-get roundtrip.

### T2. `search_market_by_window` helper
- Import `pattern_stems` from `buscador` (or duplicate the small mapping into `polymarket_core` to avoid cross-module import — pick whichever the tests pass cleanly).
- Implement search loop with window filter.
- 3 tests: hit-in-window, miss-outside-window, miss-no-matches.

### T3. `fetch_market` fallback wiring
- Insert alias-check before `resolve_slug`.
- On `resolve_slug` None: call `search_market_by_window`, persist alias, retry.
- Add `derived_slug`, `real_slug` fields to result.
- 3 tests: alias-hit-skips-search, fallback-on-resolve-miss, error-on-search-miss.

### T4. Fallback stats counters
- Module-level `_fallback_stats` dict.
- `get_fallback_stats`, `reset_fallback_stats` accessors.
- Increment on every fallback path branch (alias hit, search hit, search miss).
- 1 test: stats increment correctness.

### T5. `run_diagnostics` rewrite
- Load oldest-markets.json (path: `.planning/research/oldest-markets.json` resolved relative to `polymarket_core.py`'s parent dir).
- Probe each combo's cached slug.
- Return rows with `source` field.
- 2 tests: uses-sweep-json, handles-missing-json.

### T6. Wire fallback stats into `/api/fetch`
- `reset_fallback_stats()` at start of fetch handler (or wrap fetch_range).
- Include `fallback_hits` / `fallback_misses` in response.
- 1 test: response includes the keys.

### T7. Verification run
- `pytest tests/ -q` — should show 141 + ~12 = ~153 tests, all green.
- Manual smoke: visit `/diagnostics` in the running Flask app, confirm rows render with the new `source` column reflecting sweep data (or warnings).
- Manual smoke: trigger a fetch with a deliberately stale slug pattern (mutate one entry of `slug_from_ts` to wrong format temporarily, verify the fallback recovers — then revert).

## Out of Scope (this phase)
- UI surfacing of `fallback_hits` (defer until Phase 5; counter is in API response, that's enough for now).
- Active-market discovery for *future* windows (only past/closed markets supported via Gamma `closed=true` filter).
- Cache invalidation policy for `slug_aliases` (TTL, prune-old-rows). Aliases are append-mostly; revisit if table bloats >10k rows.
- CLOB 12h-resolution cliff for resolved markets — documented in research, not actioned this phase. Add UI warning only when it surfaces as a user complaint.
- Concurrency/locking on the alias table — single-user app, sqlite default isolation is sufficient.

## Risks / Notes
- **Pattern-stem coupling.** `polymarket_core.py` will import or duplicate `buscador.pattern_stems`. Duplication is the simpler path and avoids a circular dependency risk; we already keep `ASSET_SLUG_SHORT/FULL` constants in `polymarket_core`.
- **Window tolerance.** ±86400s is generous. Markets resolve at fixed minute/hour boundaries, but `startDate` from Gamma can drift a few minutes; ±1d is safe without picking up neighbors. Tighten only if false-positives appear.
- **Test fixtures.** Reuse `tests/fixtures/gamma_event.json` for the resolve-slug stub; add `tests/fixtures/gamma_search.json` for the /events search response shape.
- **JSON path resolution.** `run_diagnostics` resolves `.planning/research/oldest-markets.json` relative to `Path(__file__).parent` so tests can monkeypatch via a fixture and the production path Just Works.
- **Empty-combo handling.** If Phase 3 sweep had any missing combos (`found: false`), diagnostics shows a warning row, not an error. Acceptable per acceptance criteria — diagnose tab still passes.

## Success Criteria (verifier checklist)
- [ ] All Phase 1+2+3 tests still pass: `pytest tests/ -q` shows ≥141 prior tests.
- [ ] `tests/test_resilience.py` exists with ≥10 tests, all green.
- [ ] `slug_aliases` table created on first `get_db()` call (idempotent — running tests twice doesn't error).
- [ ] `fetch_market` returns dict with `derived_slug` and `real_slug` keys after a fallback run.
- [ ] `get_fallback_stats()` returns `{"hits": int, "misses": int, "by_combo": {}}`-shape dict.
- [ ] `run_diagnostics` reads `.planning/research/oldest-markets.json` on each call (verified via test using monkeypatched path).
- [ ] `/api/fetch` response JSON contains `fallback_hits` and `fallback_misses` keys.
- [ ] Hardcoded `ts = 1776960000` removed from `run_diagnostics`.
- [ ] No new files written to repo root.
- [ ] Manual smoke: deleting last-month's hardcoded ts has zero effect (it's already removed); diagnose tab still passes.

## Estimated effort
~1.5 focused sessions. Heavier than Phase 3 because it touches `fetch_market` (the hot path) and adds a new sqlite table; tests are straightforward but cover several branches.

## After this phase
Phase 5 (EV/Sharpe Gate + Configurable Grid) can ship without the slug-drift risk hanging over it. The strategy-finder rewrites no longer have to worry that a Polymarket schema change kills end-to-end fetches mid-experiment.
