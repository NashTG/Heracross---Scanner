#!/usr/bin/env python3
"""
Live tick printer for active Polymarket BTC markets.

Prints every book update:
  market_name | up_price | down_price | btc_to_beat | btc_delta

Usage:
    python ticker.py
    python ticker.py --tag btc --limit 20 --spot-interval 5
"""
import asyncio
import json
import os
import re
import sys
import time
import argparse
from datetime import datetime
from typing import Optional

import aiohttp
import websockets
from dotenv import load_dotenv

load_dotenv()

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# ---------------------------------------------------------------------------
# Market loading
# ---------------------------------------------------------------------------

def fetch_btc_markets(tag_slug: str = "crypto", limit: int = 50) -> list[dict]:
    """Fetch active markets and return structured BTC market list."""
    import requests
    resp = requests.get(f"{GAMMA_API}/events", params={"tag_slug": tag_slug, "active": "true", "limit": limit}, timeout=10)
    resp.raise_for_status()
    events = resp.json()

    markets = []
    for event in events:
        for market in event.get("markets", []):
            q = market.get("question", "")
            if not any(k in q.lower() for k in ("bitcoin", "btc")):
                continue

            clob_ids = market.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except Exception:
                    continue

            outcomes = market.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []

            if len(clob_ids) < 2:
                continue

            btc_target = _parse_price_target(q)
            markets.append({
                "question": q,
                "slug": event.get("slug", ""),
                "end_date": market.get("endDate", ""),
                "token_yes": clob_ids[0],   # YES / Up outcome
                "token_no":  clob_ids[1],   # NO  / Down outcome
                "outcome_yes": outcomes[0] if outcomes else "Yes",
                "outcome_no":  outcomes[1] if len(outcomes) > 1 else "No",
                "btc_to_beat": btc_target,
            })
    return markets


def _parse_price_target(question: str) -> Optional[float]:
    """Extract the first dollar/k price from a question string."""
    # e.g. "$105,000", "$95k", "100000"
    m = re.search(r'\$?([\d,]+)(?:\.[\d]+)?([kK])?', question)
    if not m:
        return None
    digits = float(m.group(1).replace(",", ""))
    if m.group(2):
        digits *= 1000
    return digits


# ---------------------------------------------------------------------------
# Spot BTC price (Binance REST, polled in background)
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
                    async with session.get(BINANCE_TICKER, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        d = await r.json()
                        self.price = float(d["price"])
                except Exception:
                    pass
                await asyncio.sleep(self.interval)

    def stop(self):
        self._stop = True


# ---------------------------------------------------------------------------
# WS listener
# ---------------------------------------------------------------------------

class Ticker:
    def __init__(self, markets: list[dict], spot: SpotTracker):
        self.markets = markets
        self.spot = spot

        # token_id → market + side
        self._token_map: dict[str, dict] = {}
        for m in markets:
            self._token_map[m["token_yes"]] = {**m, "side": "yes"}
            self._token_map[m["token_no"]]  = {**m, "side": "no"}

        # best mid-price per token
        self._prices: dict[str, float] = {}

    def _mid(self, bids: list, asks: list) -> Optional[float]:
        """Best bid/ask midpoint, or best available side."""
        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return best_bid or best_ask

    def _handle_book(self, event: dict):
        token_id = event.get("asset_id", "")
        if token_id not in self._token_map:
            return

        bids = event.get("bids", [])
        asks = event.get("asks", [])
        if not bids and not asks:
            return

        mid = self._mid(bids, asks)
        if mid is None:
            return

        self._prices[token_id] = mid
        self._print_tick(token_id)

    def _print_tick(self, updated_token: str):
        info = self._token_map[updated_token]
        tok_yes = info["token_yes"]
        tok_no  = info["token_no"]

        up_price   = self._prices.get(tok_yes)
        down_price = self._prices.get(tok_no)

        if up_price is None and down_price is None:
            return

        btc_to_beat = info["btc_to_beat"]
        btc_now     = self.spot.price
        btc_delta   = (btc_now - btc_to_beat) if (btc_now and btc_to_beat) else None

        ts   = datetime.utcnow().strftime("%H:%M:%S")
        name = info["question"][:55]
        up_s   = f"{up_price:.3f}"   if up_price   is not None else "  ---"
        down_s = f"{down_price:.3f}" if down_price  is not None else "  ---"
        beat_s = f"${btc_to_beat:,.0f}" if btc_to_beat else "  ---"
        btc_s  = f"${btc_now:,.0f}"     if btc_now    else "  ---"
        delt_s = f"{btc_delta:+,.0f}"   if btc_delta  is not None else "  ---"

        print(f"{ts}  {name:<56} UP={up_s}  DN={down_s}  target={beat_s}  BTC={btc_s}  delta={delt_s}")

    async def listen(self):
        all_tokens = list(self._token_map.keys())
        print(f"Connecting to {WS_URL}...")
        print(f"Watching {len(self.markets)} BTC markets ({len(all_tokens)} tokens)\n")
        print(f"{'TIME':8}  {'MARKET':<56} {'UP':>7}  {'DOWN':>7}  {'TARGET':>10}  {'BTC':>10}  {'DELTA':>8}")
        print("-" * 120)

        backoff = 2
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10, max_size=2**22) as ws:
                    backoff = 2
                    # subscribe — one message per channel
                    msg = {"type": "subscribe", "assets_ids": all_tokens, "channel": "book"}
                    await ws.send(json.dumps(msg))

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        events = data if isinstance(data, list) else [data]
                        for ev in events:
                            if not isinstance(ev, dict):
                                continue
                            etype = ev.get("event_type") or ev.get("type") or ""
                            if etype in ("book", "book_snapshot", "l2_book", "price_change"):
                                self._handle_book(ev)

            except websockets.exceptions.ConnectionClosed as e:
                print(f"\n[reconnect] connection closed: {e}. Retrying in {backoff}s...")
            except Exception as e:
                print(f"\n[reconnect] error: {e}. Retrying in {backoff}s...")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args):
    print("Fetching active BTC markets from Gamma API...")
    markets = fetch_btc_markets(tag_slug=args.tag, limit=args.limit)

    if not markets:
        print("No BTC markets found. Try running market_discovery.py first.")
        sys.exit(1)

    print(f"Found {len(markets)} BTC markets:")
    for m in markets:
        target = f"${m['btc_to_beat']:,.0f}" if m['btc_to_beat'] else "?"
        print(f"  {target:>10}  {m['question'][:70]}")
    print()

    spot = SpotTracker(interval=args.spot_interval)
    ticker = Ticker(markets, spot)

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
    parser.add_argument("--tag",           default="crypto", help="Gamma API tag slug")
    parser.add_argument("--limit",         type=int, default=50, help="Max events to fetch")
    parser.add_argument("--spot-interval", type=float, default=5.0, help="Binance poll interval (s)")
    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nStopped.")
