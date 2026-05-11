# Research: Polymarket Up/Down Market Coverage by Asset × Interval

Source: extracted from prior `test.r` / `test.sh` Python heredocs in repo root (now deleted). Reflects discovery state at time of capture — **may be outdated**; user notes 5m and 4h markets do exist now.

## Oldest-market dates found at discovery time

| Asset | TF | Status | Oldest seen | Slug | Note |
|---|---|---|---|---|---|
| BTC | 5m | (re-verify) | — | — | Not seen at discovery; user confirms now exists |
| BTC | 15m | ✓ | 2025-09-19 | `btc-up-or-down-15m-1758283200` | PolyGun launch day |
| BTC | 1h | ✓ | 2024-11-20 | `bitcoin-up-or-down-november-20-9am-et` | Oldest hourly |
| BTC | 4h | (re-verify) | — | — | Not seen at discovery; user confirms now exists |
| BTC | 1d | ✓ | 2025-03-13 | `bitcoin-up-or-down-on-march-13` | |
| ETH | 15m | ✓ | 2025-09-19 | `eth-up-or-down-15m-1758285900` | |
| ETH | 1h | (re-verify) | — | — | No standalone hourly seen at discovery |
| ETH | 1d | ✓ | 2025-03-19 | `ethereum-up-or-down-on-march-19` | |
| XRP | 1h | ✓ | 2025-09-01 | `xrp-up-or-down-september-1-3pm-et` | |
| XRP | 1d | ✓ | 2025-04-30 | `xrp-up-or-down-in-april` | Monthly market |
| SOL | 1h | ✓ | 2025-09-01 | `solana-up-or-down-september-1-4pm-et` | |
| SOL | 1d | ✓ | 2025-04-30 | `solana-up-or-down-in-april` | Monthly market |

## Slug pattern observations
- 15m intervals use `{asset_short}-up-or-down-15m-{ts}` (PolyGun timestamp format).
- 1h markets named with weekday-style slugs: `{asset_full}-up-or-down-{month}-{day}-{hour}{am/pm}-et`.
- 1d markets: `{asset_full}-up-or-down-on-{month}-{day}` (no year for some, with year for others).
- Monthly markets exist for SOL/XRP: `{asset_full}-up-or-down-in-{month}`.

## Action item
- `buscador.py` should be extended to **discover the first (oldest) market of every recurring crypto slug pattern** — re-verify the table above for 5m and 4h cases the user confirms exist. This is part of the slug-resilience plan.
