#!/usr/bin/env python3
"""
Live BTC ticker — subscribes to active Polymarket BTC markets and prints every book tick.

Output columns:
  TIME | MARKET (truncated) | UP (YES mid) | DOWN (NO mid) | TARGET ($) | BTC (spot) | DELTA

Usage:
    python ticker.py                      # live BTC markets, auto-discovered
    python ticker.py --debug              # also print first 5 raw WS messages
    python ticker.py --limit 20           # cap to top 20 markets by volume
    python ticker.py --spot-interval 3    # Binance poll every 3s
"""
import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import requests
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------

def fetch_btc_markets(limit: int = 200) -> list[dict]:
    """
    Pull live active BTC markets from Gamma API.
    Returns a list of market dicts, each with YES/NO token IDs and metadata.
    Sorted by event 24h volume descending (most liquid first).
    """
    resp = requests.get(
        f"{GAMMA_API}/events",
        params={
            "active": "true",
            "closed": "false",
            "archived": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json()

    markets = []
    for event in events:
        title = event.get("title", "")
        if not any(k in title.lower() for k in ("bitcoin", "btc")):
            continue
        for market in event.get("markets", []):
            clob_ids = market.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    continue
            if len(clob_ids) < 2:
                continue

            outcomes = market.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []

            markets.append({
                "question":   market.get("question", title),
                "event_slug": event.get("slug", ""),
                "end_date":   market.get("endDate", ""),
                "token_yes":  clob_ids[0],
                "token_no":   clob_ids[1],
                "outcomes":   outcomes,
                "btc_target": _parse_target(market.get("question", title)),
            })

    return markets


def _parse_target(question: str) -> Optional[float]:
    """Extract the first dollar/k price from a question string."""
    m = re.search(r'\$?([\d,]+)(?:\.[\d]+)?([kK])?', question)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    if m.group(2):
        val *= 1000
    # Sanity: BTC prices are roughly $1k–$500k
    if not (1_000 <= val <= 500_000):
        return None
    return val


# ---------------------------------------------------------------------------
# Binance spot tracker
# ---------------------------------------------------------------------------

class SpotTracker:
    def __init__(self, interval: float = 5.0):
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
# Ticker
# ---------------------------------------------------------------------------

class Ticker:
    def __init__(self, markets: list[dict], spot: SpotTracker, debug: bool = False):
        self.markets = markets
        self.spot = spot
        self.debug = debug
        self._debug_count = 0

        # token_id → {"side": "yes"|"no", **market_dict}
        self._map: dict[str, dict] = {}
        for m in markets:
            self._map[m["token_yes"]] = {**m, "side": "yes"}
            self._map[m["token_no"]]  = {**m, "side": "no"}

        # best mid-price per token
        self._prices: dict[str, float] = {}

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

        token_id = ev.get("asset_id", "") or ev.get("token_id", "")
        if not token_id or token_id not in self._map:
            return

        bids = ev.get("bids", [])
        asks = ev.get("asks", [])

        # price_change events carry last_trade_price instead of bids/asks
        if not bids and not asks:
            ltp = ev.get("price") or ev.get("last_trade_price")
            if ltp:
                self._prices[token_id] = float(ltp)
                self._print_tick(token_id)
            return

        mid = self._mid(bids, asks)
        if mid is None:
            return
        self._prices[token_id] = mid
        self._print_tick(token_id)

    def _print_tick(self, token_id: str):
        info = self._map[token_id]
        yes_tok = info["token_yes"]
        no_tok  = info["token_no"]

        up_p   = self._prices.get(yes_tok)
        down_p = self._prices.get(no_tok)

        if up_p is None and down_p is None:
            return

        target  = info["btc_target"]
        btc_now = self.spot.price
        delta   = (btc_now - target) if (btc_now and target) else None

        ts   = datetime.now(timezone.utc).strftime("%H:%M:%S")
        name = info["question"][:54]
        up_s   = f"{up_p:.3f}"   if up_p   is not None else "  ---"
        dn_s   = f"{down_p:.3f}" if down_p is not None else "  ---"
        tgt_s  = f"${target:,.0f}" if target  else "  ?"
        btc_s  = f"${btc_now:,.0f}" if btc_now else "  ?"
        dlt_s  = f"{delta:+,.0f}"  if delta  is not None else "  ?"

        print(f"{ts}  {name:<55} UP={up_s}  DN={dn_s}  target={tgt_s:>10}  BTC={btc_s:>10}  delta={dlt_s:>8}")

    async def listen(self):
        all_tokens = list(self._map.keys())
        print(f"\nConnecting to {WS_URL}")
        print(f"Watching {len(self.markets)} BTC markets ({len(all_tokens)} tokens)\n")
        print(
            f"{'TIME':8}  {'MARKET':<55} {'UP':>7}  {'DOWN':>7}  "
            f"{'TARGET':>12}  {'BTC':>12}  {'DELTA':>9}"
        )
        print("-" * 130)

        backoff = 2
        while True:
            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2 ** 22,
                ) as ws:
                    backoff = 2
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "assets_ids": all_tokens,
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

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args):
    print("Fetching live BTC markets from Gamma API...")
    markets = fetch_btc_markets(limit=args.limit)

    if not markets:
        print("No active BTC markets found.")
        sys.exit(1)

    print(f"Found {len(markets)} markets:")
    for m in markets[:20]:
        tgt = f"${m['btc_target']:,.0f}" if m["btc_target"] else "  ?"
        print(f"  {tgt:>10}  {m['question'][:70]}")
    if len(markets) > 20:
        print(f"  ... and {len(markets)-20} more")
    print()

    spot = SpotTracker(interval=args.spot_interval)
    ticker = Ticker(markets, spot, debug=args.debug)

    spot_task   = asyncio.create_task(spot.run())
    listen_task = asyncio.create_task(ticker.listen())

    try:
        await listen_task
    except asyncio.CancelledError:
        pass
    finally:
        spot.stop()
        spot_task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket BTC live ticker")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max events to fetch from Gamma API (default 200)")
    parser.add_argument("--spot-interval", type=float, default=5.0,
                        help="Binance BTC spot poll interval in seconds (default 5)")
    parser.add_argument("--debug", action="store_true",
                        help="Print first 5 raw WS messages to diagnose format")
    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nStopped.")
