# Spot Price Capture — Phase 1 Design

**Status**: Ready for implementation  
**Scope**: Add crypto spot price ingestion running alongside existing WS capture in `realtime_capture.py`

---

## 1. Asset Discovery

### Problem
Polymarket markets reference underlying assets via natural-language questions (e.g. "Will BTC be above $100k by...?", "Bitcoin up or down by end of week?"). The `discovered_tokens.json` `markets` array has both `question` and `outcomes` fields per token; the `markets` table in `polymarket_realtime.db` has `question` and `event_slug`.

### Extraction logic

**Where it lives**: a new module `spot_asset_resolver.py` (< 100 lines).

**Algorithm** — run once at startup before launching asyncio tasks:

```python
ASSET_PATTERNS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "XRP": ["ripple", "xrp"],
    "DOGE": ["dogecoin", "doge"],
    "MATIC": ["polygon", "matic"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "BNB": ["bnb", "binance coin"],
    "ADA": ["cardano", "ada"],
    "SUI": ["sui"],
    "OP":  ["optimism"],
    "ARB": ["arbitrum", "arb"],
    "PEPE": ["pepe"],
}
```

1. Load `discovered_tokens.json` (already done at startup by `load_discovered_tokens`).
2. For each market entry, lowercase `question` + `event_slug` + `outcomes` joined as one string.
3. Walk `ASSET_PATTERNS`; first match wins → emit `(token_id, symbol)` pair.
4. Deduplicate: multiple token_ids may resolve to same symbol (UP/DOWN outcomes share one underlying).
5. Return `dict[str, str]` mapping `token_id → symbol` and a `set[str]` of distinct symbols to stream.

**Edge cases**:
- No match → token tagged `UNKNOWN`, excluded from spot polling, logged as warning.
- Stale `discovered_tokens.json` → re-run `market_discovery.py` before launching capture; the resolver is called synchronously before event loop starts.

---

## 2. Data Source Choice

**Decision: Binance public WebSocket (`wss://stream.binance.com:9443/stream`)**

Reasoning:

| Factor | Coinbase Exchange WS | Binance WS |
|--------|---------------------|------------|
| Auth required | No (public ticker) | No |
| Symbol coverage | ~200 pairs | ~2000+ pairs; all Polymarket-referenced assets covered |
| Update latency | ~100ms book ticker | ~100ms book ticker |
| Multiple symbols in one connection | No (one stream per product) | Yes — multi-stream endpoint allows N streams in one WS connection |
| Rate limits | 1 WS connection/IP (public) | 1024 streams per connection, no REST auth needed |
| Reconnect documentation | Good | Excellent, widely used |

Binance's `!bookTicker` (all-symbol best bid/ask push) or per-symbol `<symbol>@ticker` streams give sub-second bid/ask/last without auth. A single WS connection handles all discovered symbols.

**Fallback**: if Binance WS fails 3 times consecutively, fall back to Binance public REST `GET /api/v3/ticker/bookTicker?symbol=BTCUSDT` — no auth, generous rate limits (1200 weight/min, this call costs 2 weight per symbol).

---

## 3. Schema

### `spot_prices` table

```sql
CREATE TABLE IF NOT EXISTS spot_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,          -- e.g. 'BTC'
    source        TEXT    NOT NULL,          -- 'binance_ws' | 'binance_rest'
    timestamp_ms  INTEGER NOT NULL,          -- Unix ms from exchange (authoritative)
    price         REAL    NOT NULL,          -- last trade price
    bid           REAL,                      -- best bid (available from bookTicker)
    ask           REAL,                      -- best ask
    ingestion_ts  INTEGER NOT NULL           -- local time.time_ns() // 1_000_000
);

CREATE INDEX IF NOT EXISTS idx_spot_symbol_ts
    ON spot_prices(symbol, timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_spot_ts
    ON spot_prices(timestamp_ms);
```

### `market_assets` mapping table

```sql
CREATE TABLE IF NOT EXISTS market_assets (
    token_id  TEXT PRIMARY KEY,
    symbol    TEXT NOT NULL,
    resolved_at INTEGER NOT NULL  -- epoch ms, for cache invalidation
);

CREATE INDEX IF NOT EXISTS idx_market_assets_symbol
    ON market_assets(symbol);
```

**Important timestamp note**: existing tables (`orderbook_snapshots`, `price_ticks`) store `timestamp` as ISO-8601 text or Unix float seconds, not milliseconds. The `spot_prices` table uses `timestamp_ms INTEGER` (ms) for unambiguous integer range queries. Joins work as:

```sql
-- Nearest spot price to an orderbook snapshot (query-time approach)
SELECT o.*, s.price AS spot_price
FROM orderbook_snapshots o
JOIN market_assets ma ON ma.token_id = o.token_id
JOIN spot_prices s ON s.symbol = ma.symbol
WHERE s.timestamp_ms BETWEEN
      CAST(CAST(o.timestamp AS REAL) * 1000 AS INTEGER) - 2000
      AND CAST(CAST(o.timestamp AS REAL) * 1000 AS INTEGER) + 2000
ORDER BY ABS(s.timestamp_ms - CAST(CAST(o.timestamp AS REAL) * 1000 AS INTEGER))
LIMIT 1;
```

### Time-join strategy

**Recommendation: query-time nearest-neighbour with the `idx_spot_symbol_ts` index + a ±2 second window.**

Rationale: SQLite does not support materialised views natively. A periodic Python job writing pre-joined rows would require a second writer process competing for the SQLite lock. The index on `(symbol, timestamp_ms)` makes the window scan O(log n + k) where k is typically 1–5 rows in a 4-second window. For analysis queries (done offline, not hot path), this is fast enough. Revisit with a materialised snapshot table only if profiling shows >500ms query times.

**Alternative (if needed later)**: write a nightly Python script that inserts pre-joined rows into a `joined_snapshots` table — marked explicitly as a non-goal for Phase 1.

---

## 4. Integration into `realtime_capture.py`

### Placement

Add a `SpotPricePoller` class in a new file `spot_price_poller.py`. It follows the same pattern as `WebSocketCapture` and `RESTPollingCapture` — a class with an async `run(symbols)` coroutine and a `stop()` method. This keeps the file under 500 lines and the single-responsibility principle intact.

### Startup wiring in `realtime_capture.py`

In `HybridCapture.run()` (and similarly in `WebSocketCapture.run()` for websocket strategy), add a third concurrent task:

```python
# In realtime_capture.py main() or HybridCapture.run():
from spot_asset_resolver import resolve_assets
from spot_price_poller import SpotPricePoller

# Before event loop:
token_to_symbol, symbols = resolve_assets(discovered_tokens_path)
db.save_market_assets(token_to_symbol)       # upsert into market_assets table

# Inside run():
spot_task = asyncio.create_task(
    SpotPricePoller(db, poll_interval=SPOT_POLL_INTERVAL).run(symbols)
)
# Add spot_task to the asyncio.wait() set alongside ws_task and rest_task
```

`SPOT_POLL_INTERVAL` is a new CLI argument `--spot-interval` defaulting to `1.0` seconds (used only for REST fallback mode; WS mode ignores it and is event-driven).

### `SpotPricePoller` internal loop

```
Primary: Binance multi-stream WS
  connect → subscribe [BTCUSDT@bookTicker, ETHUSDT@bookTicker, ...]
  on message → parse bid/ask/price → db.save_spot_price()
  on disconnect/error → increment fail_count
    if fail_count < 3: reconnect with exponential backoff (5s, 10s, 20s)
    if fail_count >= 3: switch to REST fallback, log warning

REST fallback:
  loop with asyncio.sleep(poll_interval):
    GET /api/v3/ticker/bookTicker for each symbol (batched via comma list)
    on 429: exponential backoff, double sleep, max 60s, log rate-limit warning
    on success: reset backoff
    save_spot_price()
  attempt WS reconnect every 60s; if succeeds, switch back to WS mode
```

### Independence guarantee

`SpotPricePoller` holds its own `aiohttp.ClientSession` and its own Binance WS connection — completely separate from the Polymarket WS connection in `WebSocketCapture`. A Binance outage does not affect `WebSocketCapture.run()`, and vice versa. Both co-exist in the same asyncio event loop as independent tasks.

### DatabaseManager extension

Add one method to `DatabaseManager` in `realtime_capture.py`:

```python
def save_spot_price(self, symbol: str, source: str, timestamp_ms: int,
                    price: float, bid: float = None, ask: float = None):
    ingestion_ts = int(time.time() * 1000)
    # INSERT INTO spot_prices ...

def save_market_assets(self, mapping: dict[str, str]):
    # UPSERT INTO market_assets for each token_id → symbol
```

Schema DDL for both new tables is added to `DatabaseManager._init_db()` alongside the existing `CREATE TABLE IF NOT EXISTS` blocks.

---

## 5. Retention Plan

### Growth estimate

| Table | Rate (conservative) | Rows/day |
|-------|---------------------|----------|
| orderbook_snapshots | ~1 msg/sec × 80 tokens | ~7M |
| spot_prices | 1 update/sec × 10 symbols | ~860K |
| trades | sporadic | ~50K |

At ~200 bytes/row average, `spot_prices` alone adds ~170 MB/day. `orderbook_snapshots` dominates at ~1.4 GB/day.

### Decision: single-file SQLite with TTL pruning (7-day rolling window)

**Why not daily ATTACH partitioning**: SQLite `ATTACH` is per-connection; async Python opens many short-lived connections (current code does `sqlite3.connect()` per handler call). Routing writes to the correct day-file requires either a connection factory or always re-attaching — significant refactor with no benefit in Phase 1.

**Why not WAL + separate files**: over-engineered for current scale; no concurrent readers yet.

**Concrete implementation**: add a `PruningJob` asyncio task (alongside the spot poller) that runs every 6 hours:

```python
async def run_pruning_loop(db_path: str, retention_days: int = 7):
    while True:
        cutoff_ms = int((time.time() - retention_days * 86400) * 1000)
        cutoff_iso = datetime.utcfromtimestamp(cutoff_ms / 1000).isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM spot_prices WHERE timestamp_ms < ?", (cutoff_ms,))
        conn.execute("DELETE FROM orderbook_snapshots WHERE timestamp < ?", (cutoff_iso,))
        conn.execute("DELETE FROM price_ticks WHERE timestamp < ?", (cutoff_ms / 1000,))
        conn.execute("DELETE FROM trades WHERE timestamp < ?", (cutoff_ms / 1000,))
        conn.execute("VACUUM")   # reclaim space; safe during low-write windows
        conn.commit(); conn.close()
        await asyncio.sleep(6 * 3600)
```

`retention_days` is a CLI argument `--retention-days` defaulting to `7`. The coder can raise it if disk allows.

**Migration note**: the existing `polymarket_realtime.db` has no `spot_prices` or `market_assets` tables. `_init_db()` uses `CREATE TABLE IF NOT EXISTS`, so it is fully additive — no migration script needed.

---

## 6. Non-Goals (Phase 1)

The following are explicitly deferred and should not be implemented now:

- OHLC/candlestick aggregation from spot_prices (requires separate aggregation pass)
- Binance historical klines backfill (existing `data/*.json` files cover this)
- Cross-exchange price comparison (Coinbase vs Binance)
- Pre-joined materialised view table (`joined_snapshots`)
- Daily DB file partitioning / ATTACH strategy
- Token re-discovery on the fly (market set is fixed at startup for Phase 1)
- Any changes to `buscador.py` or legacy historical fetch paths

---

## File Map

| New file | Purpose |
|----------|---------|
| `spot_asset_resolver.py` | Regex/keyword extraction: `token_id → symbol` mapping |
| `spot_price_poller.py` | `SpotPricePoller` class: Binance WS primary, REST fallback |

| Modified file | Change |
|---------------|--------|
| `realtime_capture.py` | Add `save_spot_price`, `save_market_assets` to `DatabaseManager`; wire `SpotPricePoller` + `PruningJob` as asyncio tasks in `main()` and `HybridCapture.run()`; add `--spot-interval` and `--retention-days` CLI args |

No other files are touched.
