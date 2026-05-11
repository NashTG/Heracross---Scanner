# INTEGRATIONS

## External APIs

### Polymarket Gamma API
- Base: `https://gamma-api.polymarket.com`
- Endpoint used: `GET /events?slug=<slug>` — resolve event metadata, markets, `clobTokenIds`, `outcomes`.
- No authentication. Public read.

### Polymarket CLOB API
- Base: `https://clob.polymarket.com`
- Endpoint used: `GET /prices-history?market=<token_id>&startTs&endTs&fidelity=1` — token price snapshots.
- No authentication. Public read.

### Binance Spot API
- Base: `https://api.binance.com`
- Endpoint used: `GET /api/v3/klines?symbol&interval&startTime&endTime&limit` — OHLC candles.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- Public read.

## Local Services
- **SQLite** (`polymarket_cache.db`) — market cache. Single table `markets`.
- **Filesystem JSON** — `analysis_snapshots.json`, exported datasets.

## HTTP Behavior
- `polymarket_core.fetch_json` — 30s timeout, 3 retries with linear backoff (2s × attempt) on 429/408/502/503.
- `fetch_range` sleeps 0.5s between markets (rate-limit avoidance).
- `run_diagnostics` sleeps 0.5s between probes.

## Auth & Secrets
- None. All endpoints are public, no API keys, no `.env`.
