# Spot Price Capture — Code Review

**Reviewer**: swarm code-review agent  
**Date**: 2026-05-15  
**Files reviewed**:
- `spot_asset_resolver.py` (~105 lines, new)
- `spot_price_poller.py` (~184 lines, new)
- `realtime_capture.py` (~945 lines, modified; diff only)

---

## Findings

### BLOCKER-1 — `asyncio.wait(FIRST_COMPLETED)` kills the WS loop when spot task exits normally

**File**: `realtime_capture.py:928`

`asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)` unblocks as soon as **any** task finishes — including `spot_task` finishing cleanly after exhausting its `max_restarts` inside `_resilient_task`. At that point the code immediately cancels `poly_task` (the Polymarket WS loop), shutting down data capture with no indication of why. Concretely: if `discovered_tokens.json` is absent, `spot_symbols` is empty, the spot task is never created, but the prune task sleeping 6 h is also not an issue — however if spot symbols resolve and the WS fails 5 times, `_resilient_task` returns cleanly, `asyncio.wait` returns, and Polymarket capture is cancelled.

**Fix**: separate the "keep running" task (Polymarket capture) from the "optional helpers" tasks. `asyncio.wait` should only be used to wait on `poly_task` alone, or use `asyncio.FIRST_EXCEPTION` and re-raise. Simplest fix:

```python
# Wait only on the Polymarket task; helpers run independently
await poly_task
```

Cancel helpers in `finally`. Or: use `return_when=asyncio.FIRST_EXCEPTION` and only break on actual exceptions, not on clean task exit.

---

### BLOCKER-2 — `orderbook_snapshots` pruning uses ISO cutoff but writer stores float-seconds — rows never pruned

**File**: `realtime_capture.py:791` (pruning) vs `realtime_capture.py:452,457` (writer)

The `save_orderbook` writer stores `timestamp` as `time.time()` — a float Unix epoch (e.g. `1747305600.123`). The pruning job deletes with:

```python
conn.execute("DELETE FROM orderbook_snapshots WHERE timestamp < ?", (cutoff_iso,))
# cutoff_iso is like "2026-05-08T00:00:00"
```

SQLite compares the stored float `1747305600.123` against the text `"2026-05-08T00:00:00"` using SQLite's type affinity rules. The column has no declared type (`timestamp REAL`), so stored values are REAL. Comparing REAL against TEXT in SQLite: TEXT is always greater than REAL (type sort order). Therefore `timestamp < "2026-05-08T..."` is **always false** for float rows — **`orderbook_snapshots` is never pruned**. This will cause unbounded disk growth.

**Fix**: use float-seconds for the pruning cutoff, consistent with the writer:

```python
conn.execute("DELETE FROM orderbook_snapshots WHERE timestamp < ?", (cutoff_secs,))
```

Same issue applies to `price_ticks`, `trades`, and `ticker_updates`, but those already use `cutoff_secs` — only `orderbook_snapshots` is wrong.

---

### HIGH-1 — 429 backoff is not cumulative; backoff resets every cycle

**File**: `spot_price_poller.py:137-165`

`_run_rest_cycle` initialises `backoff = self.poll_interval` at the top of each call. On a 429 it sleeps `min(backoff * 2, 60)` then returns. On the next call from `_run_loop`, `backoff` is reset to `self.poll_interval` again. This means successive 429 responses never actually back off — the effective sleep is always `min(poll_interval * 2, 60)` regardless of how many consecutive 429s arrive.

**Fix**: promote `_rest_backoff` to an instance variable (default `self.poll_interval`), double it on 429, reset it to `self.poll_interval` on a 200.

---

### HIGH-2 — `_run_ws` does not increment `_ws_fail_count` on clean WS close

**File**: `spot_price_poller.py:89-117`

When the Binance WS closes cleanly (server-side disconnect, not an exception), the `async for msg in ws` loop terminates via the `break` on `WSMsgType.CLOSED`. Execution returns to `_run_loop` which calls `_run_ws` again immediately, resetting `_ws_fail_count = 0` at line 94 on the next successful connect. That is fine **if** reconnect succeeds; but if the WS closes cleanly on every attempt (e.g. Binance sends close frame due to stream limit), `_ws_fail_count` is always reset on entry and never reaches the threshold. The poller never falls back to REST.

**Fix**: increment `_ws_fail_count` also inside the `CLOSED/ERROR` branch before the `break`, and only reset it to 0 after a successful message is received (move reset out of the connect block).

---

### MEDIUM-1 — `price = (bid+ask)/2` stored as `price NOT NULL` when both are zero

**File**: `spot_price_poller.py:128,160`

When a bookTicker arrives with both `bid=0` and `ask=0` (symbol not yet trading or parse error), the fallback `(bid or ask)` evaluates to `0`, and `price=0.0` is written to the DB as if it were a valid price. The design doc acknowledged this deviation and suggests possibly storing NULL for `price`; a zero `price` is actively misleading for analysis.

**Fix**: either store `price = None` when both bid and ask are zero (requires changing column to allow NULL and using `Optional[float]` in the signature), or add a guard to skip the DB write entirely when both are zero.

---

### MEDIUM-2 — `timestamp_ms` on WS path is local clock, not exchange timestamp

**File**: `spot_price_poller.py:127`

```python
ts_ms = int(time.time() * 1000)
```

Binance `bookTicker` stream messages do not include a server-side timestamp in the multi-stream envelope, so local clock is the only option. This is acceptable but should be documented as a known limitation in `save_spot_price` or via the `source` field (already `"binance_ws"`). The REST path (`_run_rest_cycle:155`) has the same behaviour and the same note applies. No code change required, but document it.

---

### MEDIUM-3 — `_WS_RETRY_INTERVAL` sleep runs even after `stop()` is called

**File**: `spot_price_poller.py:167-175`

`_try_promote_to_ws` unconditionally sleeps 60 seconds via `await asyncio.sleep(_WS_RETRY_INTERVAL)`. If `stop()` is called during this sleep, the task keeps the event loop busy for up to 60 s before checking `self._running`. In practice this is mitigated because `asyncio.wait` will cancel the task, but if the poller is used standalone, shutdown can stall.

**Fix**: use a cancellation-aware sleep or poll `self._running` in shorter intervals.

---

### LOW-1 — `spot_asset_resolver.py` keyword "eth" will match "method", "ether", "seth"

**File**: `spot_asset_resolver.py:19`

The substring check `"eth" in lowered` will match "method", "withdraw", "synthetic", etc. Short keywords like `"sol"`, `"link"`, `"ada"`, `"op"` have the same false-positive risk.

**Fix**: use word-boundary regex (`\beth\b`) or a dedicated word-tokeniser rather than substring `in`.

---

### LOW-2 — `save_spot_price` opens a new SQLite connection per row on WS path

**File**: `realtime_capture.py:262-273`

This is consistent with the existing `save_orderbook` / `save_trade` pattern but can create hundreds of connections per second on a busy WS. Not a bug today, but worth noting for future optimisation (batch writes or a shared connection with WAL mode).

---

### LOW-3 — `_resilient_task` passes `spot_symbols` as positional arg but `poller.run` signature is `run(self, symbols: Set[str])`

**File**: `realtime_capture.py:917`

```python
_resilient_task(poller.run, spot_symbols, label="SpotPricePoller")
```

`_resilient_task` calls `coro_fn(*args)`, so `spot_symbols` is passed as the first positional to `poller.run`. `poller.run` signature is `run(self, symbols)` — because `poller.run` is a bound method, `self` is already bound, and `spot_symbols` maps to `symbols`. This is correct. No bug, but the call reads ambiguously; naming it explicitly is cleaner.

---

### NIT-1 — Legacy files untouched

`git diff HEAD -- buscador.py app.py polymarket_core.py` produces no output. Constraint satisfied.

---

### NIT-2 — `_build_ws_url` silently builds broken URLs for symbols without a USDT pair

**File**: `spot_price_poller.py:85-87`

Symbols like `OP`, `PEPE`, `SUI` do have USDT pairs on Binance, but if a resolved symbol has no USDT pair (future-proofing concern), the stream simply receives no messages with no error. Acceptable for Phase 1.

---

## Verdict: **FIX-FIRST**

Two blockers must be resolved before shipping:

1. **BLOCKER-1**: `asyncio.wait(FIRST_COMPLETED)` cancels Polymarket WS capture when spot task exits — the primary data pipeline is at risk.
2. **BLOCKER-2**: `orderbook_snapshots` is never pruned because the ISO-string cutoff never matches float-second rows — the table will grow without bound.

Both fixes are small (< 5 lines each). After fixing those, HIGH-1 (backoff reset) and HIGH-2 (fail-count not incremented on clean WS close) should also be addressed as they compromise the fallback reliability, but they do not threaten the primary Polymarket capture path.
