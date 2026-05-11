# CONCERNS

## Code Duplication / Dead Code
- **`analyzer.py` (720 lines) duplicates `polymarket_core.py` (600 lines).** Not imported by `app.py`. Likely a legacy predecessor. Drift risk: bug fixes applied to one will not propagate to the other.
- **`buscador.py` (377 lines)** is standalone — not wired into the app. Status unclear: useful tool or abandoned spike.

## Concurrency / Correctness
- `app.py` uses **module-level globals** (`progress_state`, `last_results`, `last_analysis`). Two simultaneous `/api/fetch` calls will clobber each other. OK for single-user dev; not safe for multi-user.
- Flask runs with `debug=False` and the dev server (`app.run(...)`) — not a production WSGI server.
- `progress_cb` writes to a global dict without locking.

## Caching Logic
- `cache_get` returns `None` when `up_snapshots` or `down_snapshots` are empty, **but `cache_put` only stores when both are non-empty** (`polymarket_core.py:238`). The "skip cache if empty" branch in `cache_get` is now defensive against legacy DB rows. Mostly fine, but the asymmetry is a footgun.
- `analyzer.py` (legacy) caches **errored** rows under the same `markets` table — if anyone runs that file, it pollutes the shared `polymarket_cache.db`.

## Time / Slug Logic
- Hardcoded year defaults in UI: `value="2026-04-17"` / `2026-04-18` (`app.py`).
- `run_diagnostics` hardcodes `ts = 1776960000` (April 17 2026). Will go stale as Polymarket prunes old markets — diagnostics will start failing not because of bugs but because the probe target was deleted.
- Slug-format coupling: changes to Polymarket's slug scheme (`slug_from_ts`) silently break the whole pipeline. No version pin or content-type check.

## HTTP / Reliability
- Linear backoff (`time.sleep(2.0 * attempt)`), max 3 retries, 30s timeout. Reasonable but not exponential; backoff is short for sustained 429s.
- `fetch_range` is sequential with 0.5s sleeps. A 1-day 5m run = 288 markets × ~2s ≈ 10 min minimum. No parallelism, no ETA shown beyond raw count.
- Binance and CLOB share a single retry function — a 503 on Binance triggers the same backoff as on Gamma.

## Error Surface
- `except RuntimeError` in `fetch_market` catches the wrapper but **misses** `requests.HTTPError`/other exceptions raised by `resp.raise_for_status()` outside the retry path. Some failure modes propagate to the Flask route and become a 500.
- Flask route `/api/fetch` returns `str(e)` directly — leaks internal exception text to the client.

## Security
- No authentication on any route. Bound to `127.0.0.1` by default — fine for local use, dangerous if anyone changes it to `0.0.0.0`.
- `/api/import` accepts arbitrary JSON via `request.get_json()` and stores it in `last_results`. Downstream functions assume schema. Malformed input could cause cryptic 500s but no RCE risk.
- `cdn.tailwindcss.com` loaded over HTTPS — third-party JS in app context. Acceptable for an internal tool, not for anything serving real users.
- No CSRF protection. State-changing endpoints (`POST /api/fetch`, `/api/clear_cache`, `/api/import`, `/api/simulate`) are unprotected.
- No input validation on `start_date` / `end_date` — `datetime.strptime` will raise `ValueError` and 500.

## Data / Persistence
- `polymarket_cache.db` and `analysis_snapshots.json` are committed-tracked (per git status, `polymarket_cache.db` is modified). May bloat the repo. No retention policy.
- `save_snapshot` rewrites the whole `analysis_snapshots.json` each call — fine at this scale, O(n²) over time.
- Exported JSONs (`btc-5min-April.json`, `sol-5min-April.json`) are untracked — may contain large datasets.

## Operational
- No logging framework. `print(...)` only when `progress_cb` is `None`.
- No structured error capture, no monitoring hook.
- No `requirements.txt` — onboarding requires reading imports.
- Two Python versions cached (`cpython-313`, `cpython-314`) — environment hygiene.

## TODOs / Backlog
- No `TODO`/`FIXME` comments found in source.
- The "Backlog" tab in the UI (`app.py`) lists Done items and Someday items — informal product backlog embedded in the UI.

## Risk Summary
- **High:** No tests + duplicated domain logic. Any refactor is high-blast-radius.
- **Medium:** Hardcoded diagnostic timestamps will go stale. Globals-as-state limits scaling.
- **Low (current single-user usage):** Auth, CSRF, input validation gaps.
