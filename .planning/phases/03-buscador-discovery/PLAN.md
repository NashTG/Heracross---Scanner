# PHASE 3 — Buscador Extension: Oldest-Market Discovery

## Goal
Turn `buscador.py` from a sample-driven exploration script into an authoritative sweep of every (asset × interval) combo we care about. Output a structured table of the **oldest** market we can find per combo. Phase 4 (slug resilience + diagnostics) consumes the table.

## Context (from research)
- `polymarket-api-resilience.md` — slug-template fragility motivates the sweep: we want a real, recent slug per combo so diagnostics don't rot.
- `polymarket-market-coverage.md` — earlier (deleted `test.r/test.sh`) heredoc claimed 5m/4h "never launched"; user has refuted that. The new sweep replaces that snapshot.

## Decisions (from discuss)
- **Coverage:** full Cartesian 4 assets × 5 intervals = **20 combos** (BTC/ETH/SOL/XRP × 5m/15m/1h/4h/1d).
- **Pattern matching:** cover all known slug shapes per interval (see Pattern Map below), not just `-updown-` and `-up-or-down`.
- **Tests:** none for buscador this phase — heavy network/async, low ROI vs. a manual smoke run. Phase 4 will exercise the data via `run_diagnostics`.

## Pattern Map (what we sweep for)

| Interval | Pattern(s) | Example |
|---|---|---|
| `5m`  | `{short}-updown-5m-{unix_ts}` | `btc-updown-5m-1758283200` |
| `15m` | `{short}-updown-15m-{unix_ts}` | `eth-updown-15m-1758285900` |
| `4h`  | `{short}-updown-4h-{unix_ts}` | `xrp-updown-4h-1777924800` |
| `1h`  | `{full}-up-or-down-{month}-{day}-{hour}{ampm}-et` | `bitcoin-up-or-down-november-20-9am-et` |
| `1d`  | `{full}-up-or-down-on-{month}-{day}-{year}` AND `{full}-up-or-down-on-{month}-{day}` AND `{short}-updown-1d-{unix_ts}` | `bitcoin-up-or-down-on-march-13-2026` |

`{short}` = `btc/eth/sol/xrp`, `{full}` = `bitcoin/ethereum/solana/xrp`.

The Gamma `/markets` endpoint supports `slug_contains` filtering — we feed each pattern stem (e.g., `btc-updown-5m-`, `bitcoin-up-or-down-`) and paginate.

## Deliverables

### Code changes — `buscador.py`
- New constant `SWEEP_COMBOS = [(asset, tf) for asset in ASSETS for tf in ["5m", "15m", "1h", "4h", "1d"]]`.
- New helper `pattern_stems(asset, tf) -> list[str]` returns all slug-prefix candidates for that combo (per the table above).
- Refactor `fetch_all_markets_for_pattern` to consume `pattern_stems(asset, tf)` instead of inlining.
- New top-level coroutine `discover_oldest_per_pattern(explorer)`:
  - Iterates `SWEEP_COMBOS`.
  - For each, calls `find_oldest_for_combo` (already exists).
  - Aggregates into `results[(asset, tf)]`.
  - Returns the result dict.
- New writer functions:
  - `write_json(results, path)` — full structured dump.
  - `write_markdown(results, path)` — human table for inclusion in `.planning/research/`.
- CLI args via `argparse`:
  - `--mode {sweep,sample}` — `sweep` runs the full 20-combo Cartesian; `sample` keeps current sample-slug behavior. Default: `sweep`.
  - `--out-json PATH` — defaults to `.planning/research/oldest-markets.json`.
  - `--out-md PATH` — defaults to `.planning/research/oldest-markets.md`.
  - `--quiet` — suppress per-combo progress prints.
- `if __name__ == "__main__":` switches on `--mode`.

### Cleanup
- Delete unused `SAMPLE_SLUGS` if `--mode sample` is removed; keep otherwise.
- Remove the legacy `polymarket_oldest.json` write-path inline in `run()`; use the new writers.

### Output deliverables
- `.planning/research/oldest-markets.json` — generated artifact, **committed** so Phase 4 can read it without rerunning the network sweep.
- `.planning/research/oldest-markets.md` — human-readable table.

## Output Schema (oldest-markets.json)
```jsonc
{
  "generated_at": "2026-05-07T12:34:56Z",
  "combos_swept": 20,
  "combos_found": 17,
  "results": {
    "BTC": {
      "5m":  { "found": true, "slug": "btc-updown-5m-1758283200", "created_at": "2025-09-19T...", "condition_id": "0x...", "total_found": 1234 },
      "15m": { ... },
      "1h":  { ... },
      "4h":  { ... },
      "1d":  { ... }
    },
    "ETH": { ... },
    "SOL": { ... },
    "XRP": { ... }
  },
  "missing": [["ETH", "1h"]]   // combos where no markets returned
}
```

## Tasks

### T1. Add `pattern_stems(asset, tf)` helper
- Pure function on the explorer class (or module-level).
- Returns the list of slug-prefix candidates for that combo.
- Covers the 5 patterns in the Pattern Map.

### T2. Refactor `fetch_all_markets_for_pattern`
- Accept `(asset, tf)` and internally call `pattern_stems`.
- Existing dedup-by-condition_id logic preserved.
- Existing pagination preserved.

### T3. Add `discover_oldest_per_pattern`
- Iterates `SWEEP_COMBOS`.
- Calls `find_oldest_for_combo`.
- Returns `{asset: {tf: entry_or_None}}` dict.
- Records combos with no result for the `missing` field.

### T4. Add `write_json` and `write_markdown`
- JSON matches the schema above.
- Markdown is a single 5×4 grid plus a "Details" section with slug + created_at per cell.
- Both create parent dirs if needed.

### T5. CLI rewrite
- `argparse` with `--mode`, `--out-json`, `--out-md`, `--quiet`.
- `--mode sweep` is the new default.
- `--mode sample` preserved for backward compatibility (calls existing `discover_from_sample_slugs` flow).

### T6. Run the sweep, commit the artifact
- `python buscador.py --mode sweep` from repo root.
- Commit the generated JSON + MD into `.planning/research/`.
- Note any combos that came back empty (`missing` field) — they motivate the search-then-derive fallback in Phase 4.

## Out of Scope (this phase)
- Slug-resilience runtime fallback in `polymarket_core.fetch_market` (Phase 4).
- `run_diagnostics` rewrite to consume oldest-markets.json (Phase 4).
- Tests for buscador (deferred — too much network surface, low ROI).
- A continuous refresh job. The sweep is run on-demand or once per milestone.
- Token-id discovery beyond the oldest market (already captured in `tokens` list per entry).

## Risks / Notes
- **Rate limits.** Gamma's pagination is generous but a 20-combo sweep with possibly 1k+ markets per combo can be heavy. The existing `aiohttp` client with 15s total timeout is the right tool. If we hit 429s, add a per-page sleep (0.2s) — defer until observed.
- **Slug-prefix matches may overlap.** `bitcoin-up-or-down-` matches both 1h *and* 1d markets. The sweep dedupes by condition_id, but 1h and 1d combos will both pull the union and find the oldest among them. We post-filter by checking the slug **shape** before counting it for a combo (e.g., a slug containing `-9am-et` belongs to `1h`, not `1d`).
- **`condition_id` field name varies.** Existing code already handles `condition_id` / `conditionId` / `id`. Keep the same coalescing.
- **Empty combos are a finding, not a failure.** Phase 4 will use the `missing` list to know where to fall back to live search per fetch.

## Success Criteria (verifier checklist)
- [ ] `python buscador.py --mode sweep --out-json /tmp/x.json --out-md /tmp/x.md` exits 0 and writes both files.
- [ ] JSON structure matches the schema (top-level keys: `generated_at`, `combos_swept`, `combos_found`, `results`, `missing`).
- [ ] At least one of (5m, 4h) returns a non-empty result for at least one asset (per user expectation that they exist).
- [ ] `combos_swept == 20`.
- [ ] Markdown rendering has a 5-row × 4-col table.
- [ ] Phase 1 + 2 tests still pass: `pytest tests/ -q` shows the same count as before.
- [ ] Generated `.planning/research/oldest-markets.json` is committed.
- [ ] No new files written to repo root (output goes to `.planning/research/`).

## Estimated effort
~1 focused session. Most code paths exist; this is mostly pattern-stem expansion, a CLI wrapper, JSON/MD writers, and one network sweep run.

## After this phase
Phase 4 reads `.planning/research/oldest-markets.json` to:
1. Auto-pick recent probe slugs in `run_diagnostics` (R2).
2. Decide which combos need search-then-derive fallback at runtime in `fetch_market` (R4).
