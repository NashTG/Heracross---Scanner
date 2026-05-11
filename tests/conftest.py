"""Shared fixtures and factory helpers for the AnalyzerDiagnosis test suite."""
import pytest
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")

# Reference timestamps (all ET-midnight aligned)
TS_APR17_ET_MIDNIGHT = 1744862400   # 2026-04-17 00:00 ET
TS_APR17_NOON_ET     = 1744905600   # 2026-04-17 12:00 ET


def make_snapshot_series(prices):
    """Build a CLOB snapshot list from a plain list of prices (0-1 range)."""
    return [{"p": p, "t": TS_APR17_NOON_ET + i * 60} for i, p in enumerate(prices)]


def make_market(
    slug="btc-updown-5m-1744862400",
    ts=TS_APR17_ET_MIDNIGHT,
    up_prices=None,
    down_prices=None,
    btc_deltas=None,
    asset="BTC",
    error=None,
):
    """Build a market dict matching the schema polymarket_core uses internally."""
    if error:
        return {"slug": slug, "window_start_ts": ts, "window_end_ts": ts + 300, "error": error}

    up_prices = up_prices if up_prices is not None else [0.6, 0.65, 0.7, 0.75, 0.8]
    down_prices = down_prices if down_prices is not None else [0.4, 0.35, 0.3, 0.25, 0.2]
    btc_deltas = btc_deltas if btc_deltas is not None else [0.0, 50.0, 100.0, 120.0, 130.0]

    up_snaps = make_snapshot_series(up_prices)
    down_snaps = make_snapshot_series(down_prices)
    btc_snaps = [
        {"t": TS_APR17_NOON_ET + i * 60, "open": 30000.0, "close": 30000.0 + d, "delta": d}
        for i, d in enumerate(btc_deltas)
    ]

    return {
        "slug": slug,
        "window_start_ts": ts,
        "window_end_ts": ts + 300,
        "error": None,
        "up_snapshots": up_snaps,
        "down_snapshots": down_snaps,
        "btc_snapshots": btc_snaps,
    }


def synthetic_dataset(n=20, asset="BTC", interval="5m", scenarios=None):
    """
    Build N markets with controllable properties.

    scenarios: list of dicts, each applied to one market:
        {"up_prices": [...], "down_prices": [...], "btc_deltas": [...]}
    Remaining markets use defaults (UP closes, neutral BTC delta).
    """
    markets = []
    for i in range(n):
        ts = TS_APR17_ET_MIDNIGHT + i * 300
        slug = f"btc-updown-5m-{ts}"
        if scenarios and i < len(scenarios):
            s = scenarios[i]
            markets.append(make_market(
                slug=slug, ts=ts,
                up_prices=s.get("up_prices"),
                down_prices=s.get("down_prices"),
                btc_deltas=s.get("btc_deltas"),
            ))
        else:
            markets.append(make_market(slug=slug, ts=ts))
    return markets
