# Research: Polymarket API + Slug Schema Resilience

## Question
How do we make `polymarket_core.py` resilient to upstream changes — slug-schema drift, removed historical markets, CLOB price-history limits — without rewriting on every Polymarket release?

## Findings

### Gamma API — events endpoint
- Base: `https://gamma-api.polymarket.com/events`
- `GET /events?slug=<slug>` returns a list (we currently take `[0]`).
- Supports rich filters: `archived`, `active`, `closed`, `liquidity_min`, `volume_min`, `start_date_min/max`, `end_date_min/max`, `tag_slug`, pagination (`limit`, `offset`, `order`, `ascending`).
- Slug is the path segment after `/event/` in the public URL — **owned by Polymarket's editorial layer**, not a stable contract.
- No documented version pin; schema can change with frontend updates.
- Source: https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide

### CLOB API — prices-history limitation **(critical for us)**
- Endpoint `GET /prices-history?market=<token_id>&startTs&endTs&fidelity=<minutes>`.
- `fidelity` is in **minutes** (not seconds; we currently pass `"1"` which is correct).
- **Documented limitation:** for resolved/closed markets, the endpoint only returns data at **12+ hour granularity**, even if the market was originally fine-grained.
- **Implication:** historical 5m/15m/1h/4h fetches against already-resolved markets can return empty or coarse history. Our cache may already contain resolved markets where re-fetching now yields nothing useful.
- Source: https://docs.polymarket.com/developers/CLOB/timeseries
- Issue confirming this: https://github.com/Polymarket/py-clob-client/issues/216

### Binance klines
- Stable, public, well-documented. No drift risk worth mitigating in this milestone.

## Implications for the codebase

### 1. Slug-schema fragility is unfixable from our side
We cannot make slug generation "drift-proof" — Polymarket controls the convention. What we **can** do:
- **Search-then-derive instead of derive-only.** Use Gamma `/events` filters (`tag_slug=crypto`, asset filters, `start_date_min/max`, `end_date_min/max`) to **discover** matching markets in a window, then read their actual slug, rather than constructing slugs from a template.
- Keep `slug_from_ts` as a **fast path** with a fallback to search when the constructed slug 404s.
- This eliminates the per-asset/interval template we maintain in `slug_from_ts` (5 separate format branches), which is the highest-drift code in the repo.

### 2. Diagnostics must self-update
Hardcoded `ts = 1776960000` (April 17 2026) will go stale. Replacement: `run_diagnostics` should call Gamma `/events?tag_slug=crypto&closed=true&order=endDate&limit=10` per asset/interval and probe the most recent N markets that match the asset's slug pattern. Always-recent probes, no maintenance.

### 3. Resolved-market history is a known cliff
- Document this in the UI: "Markets resolved >X hours ago return only 12h+ history."
- Cache: stop overwriting good fine-grained history with coarse re-fetches. Our `cache_put` only writes when `up_snapshots` and `down_snapshots` both non-empty — good. But we should also **prefer cache over re-fetch** for resolved markets (currently we always check cache first; verify this is actually the case for partial rows).

### 4. Schema-validate Gamma responses
- `clobTokenIds` and `outcomes` come back as JSON-encoded strings sometimes, lists other times. Current code handles both via `isinstance(_, str)` branch. Keep this. Consider a helper `_coerce_list`.
- If Gamma adds/removes top-level fields, our `event.get("markets", [])` pattern is forgiving — already correct.

## Recommendations (feed into REQUIREMENTS / ROADMAP)

| Recommendation | Phase candidate | Risk if skipped |
|---|---|---|
| Search-based slug discovery as fallback to template | Stabilization | Single Polymarket slug-format change kills entire pipeline |
| `run_diagnostics` auto-picks recent markets via Gamma search | Stabilization | Diagnose tab silently rots month over month |
| UI warning + caching policy for resolved-market 12h cliff | Stabilization | Confusing empty-data results in fine-grained intervals |
| Replace hit-rate gate with EV/Sharpe gate | Strategy improvement | High-confidence-low-edge strategies still surface |
| Walk-forward validation (replace half-and-half OOS) | Strategy improvement | Single split overstates robustness |

## Sources
- [Polymarket Gamma API — fetch markets](https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide)
- [Polymarket CLOB — Historical Timeseries](https://docs.polymarket.com/developers/CLOB/timeseries)
- [py-clob-client issue #216 — empty history at <12h on resolved markets](https://github.com/Polymarket/py-clob-client/issues/216)
- [py-clob-client issue #189 — blank prices-history responses](https://github.com/Polymarket/py-clob-client/issues/189)
- [Polymarket agents — gamma.py reference](https://github.com/Polymarket/agents/blob/main/agents/polymarket/gamma.py)
