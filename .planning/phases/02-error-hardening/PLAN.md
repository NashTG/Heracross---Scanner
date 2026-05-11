# PHASE 2 — Error Hardening + Input Validation

## Goal
Replace the current "everything bubbles to a 500 with `str(e)`" pattern with deliberate input validation at the Flask boundary and structured exception handling at the HTTP-fetch boundary. No raw tracebacks reach the UI.

**Foundation:** Phase 1 test suite green. We add tests *before* code changes where it makes sense (TDD-style for the validators) so coverage doesn't regress.

## Decisions (from discuss)
- **Logging:** plain `print(..., file=sys.stderr)`. Lightest touch, matches existing style.
- **Error shape:** flat `{"error": "<message>"}` — matches what `app.py` JS already reads (`if(d.error)`).
- **fetch_market exception scope:** widen to `requests.RequestException` (umbrella covering ConnectionError, Timeout, HTTPError, JSONDecodeError-via-requests).

## Coverage Target

### Validators (`polymarket_core.py`)
- `validate_date_range(start, end)` — `YYYY-MM-DD` regex, parseability, `start <= end`. Raises `ValueError` with a user-safe message.
- `validate_import_payload(data)` — must be a list; each item a dict with at minimum `slug: str`. Snapshot fields optional but, when present, must be lists. Raises `ValueError`.

### Flask error handling (`app.py`)
- Wrap each route's exception path in a helper that:
  - Logs full traceback to stderr (`traceback.print_exc()` or `print(repr(e), file=sys.stderr)`).
  - Returns `{"error": "<sanitized message>"}` with the right HTTP code (400 for validation, 500 for unexpected).
- `/api/fetch`, `/api/import`, `/api/simulate` go through it.

### `fetch_market` (`polymarket_core.py`)
- `except requests.RequestException as e: result["error"] = str(e)` replaces the narrow `RuntimeError` catch.
- `RuntimeError` (raised by our retry wrapper) is a subclass-of-Exception path — keep handling it too.

## Deliverables

### Code changes
- `polymarket_core.py`:
  - Add `validate_date_range(start, end) -> tuple[str, str]`. On failure raises `ValueError`.
  - Add `validate_import_payload(data) -> list[dict]`. On failure raises `ValueError`.
  - Widen `fetch_market` exception handler: `except (RuntimeError, requests.RequestException)`.
- `app.py`:
  - Add `_error_response(exc, status=500, public_msg=None)` helper.
  - `/api/fetch`: call `validate_date_range` before `fetch_range`. Return 400 on `ValueError`, 500 on others.
  - `/api/import`: call `validate_import_payload`. Return 400 on `ValueError`.
  - `/api/simulate`: validate `bet_size > 0` and that `insight` is a dict with required keys. 400 on missing/invalid.
  - All `except Exception as e: ... str(e)` paths route through `_error_response` and return a generic `"Internal error"` to the client; details only to stderr.

### Tests added (`tests/`)
- `test_validators.py` — covers `validate_date_range` and `validate_import_payload` directly. Pure-function, no Flask needed.
- `test_app_errors.py` — Flask test client; covers:
  - `/api/fetch` with bad date → 400, clean error.
  - `/api/fetch` with start > end → 400.
  - `/api/import` with non-list body → 400.
  - `/api/import` with list of non-dicts → 400.
  - `/api/import` with missing `slug` → 400.
  - `/api/simulate` with no insight → 400 (existing behavior preserved).
  - `/api/simulate` with `bet_size=0` or negative → 400 (new).
- Extend `test_simulator.py` and `test_filters.py` only if a behavior change requires it (none expected).

## Tasks

### T1. Add validators to `polymarket_core.py`
- `validate_date_range(start, end)`:
  - Regex `^\d{4}-\d{2}-\d{2}$` on both inputs.
  - `datetime.strptime(..., "%Y-%m-%d")` to confirm parseability.
  - Compare as dates; raise `ValueError("start_date must be on or before end_date")` if reversed.
  - Returns `(start, end)` on success.
- `validate_import_payload(data)`:
  - Top-level must be `list`.
  - Each element must be `dict` with a non-empty `slug` string.
  - If `up_snapshots` / `down_snapshots` / `btc_snapshots` are present, must be `list` (allow empty).
  - Returns the list on success.

### T2. Add `tests/test_validators.py`
- Cover happy path + each failure mode of both validators.
- No Flask dependency.

### T3. Widen `fetch_market` exception scope
- Change `except RuntimeError as e:` to `except (RuntimeError, requests.RequestException) as e:`.
- Keep recording the error string on the result dict (existing pattern).
- Add a stderr `print(f"fetch_market error for {slug}: {e!r}", file=sys.stderr)` — no traceback dump (would be too noisy when 200+ markets fail in a sweep), just one-liner per failed market.

### T4. Add `_error_response` helper + apply across routes in `app.py`
- Helper signature: `_error_response(exc, status=500, public_msg=None)`.
  - Logs `traceback.print_exc()` to stderr.
  - Returns `(jsonify({"error": public_msg or "Internal error"}), status)`.
- Routes updated:
  - `/api/fetch` — `validate_date_range` first, catch `ValueError → 400`, others → 500.
  - `/api/import` — `validate_import_payload`, catch `ValueError → 400`.
  - `/api/simulate` — validate `bet_size > 0`, validate `insight` dict shape, catch `ValueError → 400`.
  - `/api/oos` — wrap in try/except for unexpected.
- The existing `progress_state["running"] = False` cleanup in the fetch route stays (still in the success and exception paths).

### T5. Add `tests/test_app_errors.py`
- Flask test client (`app.test_client()`).
- Each test asserts: status code, response JSON has `error` key, response is **not** a stack trace string.

### T6. UI message touch-up (small)
- `app.py` JS currently does `log('ERROR: '+d.error)`. No change needed — flat shape preserved.
- Verify by manual smoke (out of strict acceptance, but document).

## Out of Scope (this phase)
- Slug-resilience search-then-derive (Phase 4).
- HTTP retry/backoff redesign (Phase 4).
- Concurrency-safe globals (deferred milestone-out-of-scope).
- CSRF / auth (out of milestone).
- Replacing globals with per-request context (out of milestone).

## Risks / Notes
- Flask `app.test_client()` interacts with module-level globals in `app.py` (`progress_state`, `last_results`). Tests must reset these between runs or use ordered fixtures. We isolate via a `pytest` fixture that resets them.
- `traceback.print_exc()` only works inside an `except` block — confirm helper is only called from there.
- A bad `/api/fetch` payload should **not** flip `progress_state["running"]` to True — validate **before** mutating state.
- `validate_import_payload` is intentionally permissive on snapshot content (no deep validation). Phase 4 will add slug-format correctness checks if needed.

## Success Criteria (verifier checklist)
- [ ] `pytest tests/` — all green (Phase 1's 101 tests + new ones).
- [ ] `/api/fetch` with `start_date="not-a-date"` returns HTTP 400, body `{"error": "<clean message>"}`.
- [ ] `/api/fetch` with `start_date="2026-04-18"` and `end_date="2026-04-17"` returns 400.
- [ ] `/api/import` with body `"not-a-list"` returns 400.
- [ ] `/api/import` with `[{"foo": "bar"}]` (no slug) returns 400.
- [ ] `/api/simulate` with `bet_size=-1` returns 400.
- [ ] No 500 response contains `Traceback` or any internal stack frame text.
- [ ] Invalid `/api/fetch` does not set `progress_state["running"] = True`.
- [ ] `fetch_market` returns `{"slug": ..., "error": "..."}` for `requests.ConnectionError` (not raised).

## Estimated effort
~1 focused session. Validators + tests are small. Most of the time is wiring the helper through every Flask route and the test fixtures for the test client.

## After this phase
Phase 3 (buscador oldest-market discovery) is independent of this work. Phase 4 (slug resilience + diagnostics) builds on the widened exception handling here.
