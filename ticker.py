#!/usr/bin/env python3
"""
Live ticker for the currently-active rolling BTC Up/Down markets.

Watches ONLY the recurring directional markets (default: 5m + 15m).
One row per timeframe, updated in place on every book tick.

Output columns:
  TF | WINDOW (UTC) | secs_left | UP | DOWN | anchor (target) | BTC spot | delta

Anchor (= "btc_to_beat") is locked at the moment the resolution window opens,
sourced from Binance BTC/USDT spot. Before window-open it shows (pending).

Usage:
    python ticker.py                       # 5m + 15m (default)
    python ticker.py --timeframes 5m,15m,1h
    python ticker.py --debug               # first 5 raw WS messages
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import websockets

from realtime_capture import (
    discover_rolling_btc_markets,
    TIMEFRAME_MINUTES,
    WEBSOCKET_URL as WS_URL,
)

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


# ---------------------------------------------------------------------------
# Binance spot tracker
# ---------------------------------------------------------------------------

class SpotTracker:
    def __init__(self, interval: float = 3.0):
        self.price: Optional[float] = None
        self.interval = interval
        self._stop = False

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while not self._stop:
                try:
                    async with session.get(
                        BINANCE_PRICE_URL,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as r:
                        data = await r.json()
                        self.price = float(data["price"])
                except Exception:
                    pass
                await asyncio.sleep(self.interval)

    def stop(self):
        self._stop = True


# ---------------------------------------------------------------------------
# Row state
# ---------------------------------------------------------------------------

class RowState:
    def __init__(self, market: dict):
        self.market = market
        self.up_price: Optional[float] = None
        self.down_price: Optional[float] = None
        self.anchor: Optional[float] = None  # Binance snapshot at window_start


# ---------------------------------------------------------------------------
# Ticker
# ---------------------------------------------------------------------------

class Ticker:
    def __init__(self, timeframes: list[str], spot: SpotTracker, debug: bool = False):
        self.timeframes = timeframes
        self.spot = spot
        self.debug = debug
        self._debug_count = 0
        self.rows: dict[str, RowState] = {}     # tf -> RowState
        self._token_map: dict[str, str] = {}    # token_id -> (tf, "up"|"down")

    def _rebuild_token_map(self):
        self._token_map = {}
        for tf, row in self.rows.items():
            self._token_map[row.market["token_up"]]   = (tf, "up")
            self._token_map[row.market["token_down"]] = (tf, "down")

    async def rediscover(self) -> tuple[list[str], list[str]]:
        """Return (added_tokens, removed_tokens) since last call."""
        loop = asyncio.get_running_loop()
        fresh = await loop.run_in_executor(
            None, discover_rolling_btc_markets, self.timeframes
        )

        added, removed = [], []
        fresh_by_tf = {m["timeframe"]: m for m in fresh}

        for tf in self.timeframes:
            new = fresh_by_tf.get(tf)
            old = self.rows.get(tf)
            if new is None:
                continue
            if old is None or old.market["slug"] != new["slug"]:
                if old:
                    removed.extend([old.market["token_up"], old.market["token_down"]])
                    print(f"\n--- rotation: {tf} new window "
                          f"{new['window_start_dt']:%H:%M:%S}-{new['end_dt']:%H:%M:%S} UTC "
                          f"({new['slug']}) ---")
                added.extend([new["token_up"], new["token_down"]])
                self.rows[tf] = RowState(new)

        self._rebuild_token_map()
        return added, removed

    def _mid(self, bids: list, asks: list) -> Optional[float]:
        def price_of(level):
            if isinstance(level, dict):
                return float(level.get("price", 0) or 0)
            if isinstance(level, (list, tuple)) and level:
                return float(level[0])
            return 0.0
        best_bid = price_of(bids[0]) if bids else None
        best_ask = price_of(asks[0]) if asks else None
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return best_bid or best_ask

    def _handle_event(self, ev: dict):
        if not isinstance(ev, dict):
            return
        etype = ev.get("event_type") or ev.get("type") or ""
        if etype not in ("book", "book_snapshot", "l2_book", "price_change", "last_trade_price"):
            return

        token_id = ev.get("asset_id") or ev.get("token_id")
        if not token_id or token_id not in self._token_map:
            return

        tf, side = self._token_map[token_id]
        row = self.rows.get(tf)
        if not row:
            return

        bids = ev.get("bids", []) or []
        asks = ev.get("asks", []) or []
        if not bids and not asks:
            ltp = ev.get("price") or ev.get("last_trade_price")
            if ltp is None:
                return
            mid = float(ltp)
        else:
            mid = self._mid(bids, asks)
            if mid is None:
                return

        if side == "up":
            row.up_price = mid
        else:
            row.down_price = mid

        self._print_row(tf)

    def _print_row(self, tf: str):
        row = self.rows.get(tf)
        if not row:
            return
        m = row.market
        now = datetime.now(timezone.utc)

        # Anchor logic: lock at window start using Binance snapshot
        if row.anchor is None and now >= m["window_start_dt"] and self.spot.price is not None:
            row.anchor = self.spot.price

        secs_left = int((m["end_dt"] - now).total_seconds())
        secs_left = max(secs_left, 0)

        win = f"{m['window_start_dt']:%H:%M:%S}-{m['end_dt']:%H:%M:%S}"
        up_s   = f"{row.up_price:.3f}"   if row.up_price   is not None else "  ---"
        dn_s   = f"{row.down_price:.3f}" if row.down_price is not None else "  ---"
        if row.anchor is not None:
            anc_s = f"${row.anchor:,.0f}"
            if self.spot.price is not None:
                delta = self.spot.price - row.anchor
                dlt_s = f"{delta:+,.2f}"
            else:
                dlt_s = "  ?"
        else:
            anc_s = "(pending)"
            dlt_s = "(pending)"
        btc_s  = f"${self.spot.price:,.0f}" if self.spot.price is not None else "  ?"

        ts = now.strftime("%H:%M:%S")
        print(f"{ts}  [{tf:>3}]  {win}  {secs_left:>3}s left  "
              f"UP={up_s}  DN={dn_s}  anchor={anc_s:>10}  BTC={btc_s:>10}  delta={dlt_s:>9}")

    async def listen(self):
        print(f"\nConnecting to {WS_URL}")
        added, _ = await self.rediscover()
        print(f"Watching {len(self.rows)} rolling markets:")
        for tf, row in self.rows.items():
            m = row.market
            print(f"  [{tf}]  window {m['window_start_dt']:%H:%M:%S}-{m['end_dt']:%H:%M:%S} UTC  "
                  f"{m['slug']}")
        print()

        ws_handle: Optional[websockets.WebSocketClientProtocol] = None
        backoff = 2
        last_rediscover = 0.0

        async def rediscover_loop():
            nonlocal ws_handle
            import time as _t
            while True:
                await asyncio.sleep(15)
                if ws_handle is None:
                    continue
                try:
                    added, removed = await self.rediscover()
                    if removed:
                        for ch in ("book",):
                            await ws_handle.send(json.dumps({
                                "type": "unsubscribe",
                                "assets_ids": removed,
                                "channel": ch,
                            }))
                    if added:
                        for ch in ("book",):
                            await ws_handle.send(json.dumps({
                                "type": "subscribe",
                                "assets_ids": added,
                                "channel": ch,
                            }))
                except Exception as exc:
                    print(f"[rediscover] error: {exc}")

        rediscover_task = asyncio.create_task(rediscover_loop())

        async def heartbeat():
            # Reprint each row once per second so secs_left + delta tick visibly even without book events
            while True:
                await asyncio.sleep(1)
                for tf in list(self.rows.keys()):
                    self._print_row(tf)

        heartbeat_task = asyncio.create_task(heartbeat())

        try:
            while True:
                try:
                    async with websockets.connect(
                        WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                        max_size=2 ** 22,
                    ) as ws:
                        ws_handle = ws
                        backoff = 2
                        tokens = list(self._token_map.keys())
                        await ws.send(json.dumps({
                            "type": "subscribe",
                            "assets_ids": tokens,
                            "channel": "book",
                        }))

                        async for raw in ws:
                            if self.debug and self._debug_count < 5:
                                self._debug_count += 1
                                try:
                                    parsed = json.loads(raw)
                                    preview = json.dumps(parsed, indent=2)[:600]
                                except Exception:
                                    preview = raw[:600]
                                print(f"\n[DEBUG raw #{self._debug_count}]\n{preview}\n")

                            try:
                                data = json.loads(raw)
                            except Exception:
                                continue
                            events = data if isinstance(data, list) else [data]
                            for ev in events:
                                self._handle_event(ev)

                except websockets.exceptions.ConnectionClosed as e:
                    print(f"\n[reconnect] closed: {e}. Retry in {backoff}s...")
                except Exception as e:
                    print(f"\n[reconnect] error: {e}. Retry in {backoff}s...")
                finally:
                    ws_handle = None

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        finally:
            rediscover_task.cancel()
            heartbeat_task.cancel()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args):
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    invalid = [t for t in timeframes if t not in TIMEFRAME_MINUTES]
    if invalid:
        print(f"Invalid timeframes: {invalid}. Valid: {list(TIMEFRAME_MINUTES)}")
        sys.exit(1)

    spot = SpotTracker(interval=args.spot_interval)
    ticker = Ticker(timeframes, spot, debug=args.debug)

    spot_task = asyncio.create_task(spot.run())
    try:
        await ticker.listen()
    except asyncio.CancelledError:
        pass
    finally:
        spot.stop()
        spot_task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket rolling BTC Up/Down ticker")
    parser.add_argument("--timeframes", type=str, default="5m,15m",
                        help="Timeframes to watch (comma-sep). Valid: 5m,15m,1h,4h")
    parser.add_argument("--spot-interval", type=float, default=3.0,
                        help="Binance BTC spot poll interval seconds (default 3)")
    parser.add_argument("--debug", action="store_true",
                        help="Print first 5 raw WS messages")
    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nStopped.")
