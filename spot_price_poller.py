"""
Spot Price Poller — streams best bid/ask from Binance for resolved crypto symbols.

Primary path: Binance multi-stream WebSocket (bookTicker per symbol).
Fallback path: Binance public REST bookTicker, polled every `poll_interval` seconds.
After 3 consecutive WS failures the poller switches to REST; it attempts WS reconnect
every 60 s and promotes back to WS on success.
"""

import asyncio
import json
import logging
import time
from typing import Optional, Set, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from realtime_capture import DatabaseManager

logger = logging.getLogger(__name__)

_BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"
_BINANCE_REST_URL = "https://api.binance.com/api/v3/ticker/bookTicker"

_WS_FAIL_THRESHOLD = 3       # switch to REST after this many consecutive WS failures
_WS_RETRY_INTERVAL = 60.0    # seconds between WS reconnect attempts while in REST mode
_MAX_REST_BACKOFF = 60.0      # cap for 429 exponential backoff


class SpotPricePoller:
    """Polls / streams Binance spot prices for a set of symbols."""

    def __init__(self, db: "DatabaseManager", poll_interval: float = 1.0):
        self.db = db
        self.poll_interval = poll_interval
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_fail_count = 0
        self._in_rest_mode = False
        self._rest_backoff = poll_interval

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, symbols: Set[str]) -> None:
        """Start the poller. Runs until stop() is called."""
        if not symbols:
            logger.warning("SpotPricePoller: no symbols to poll, exiting")
            return

        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
        logger.info(f"SpotPricePoller starting for symbols: {sorted(symbols)}")

        try:
            await self._run_loop(symbols)
        finally:
            await self._close_session()
            logger.info("SpotPricePoller stopped")

    def stop(self) -> None:
        """Signal the poller to stop after the current iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal: main loop
    # ------------------------------------------------------------------

    async def _run_loop(self, symbols: Set[str]) -> None:
        while self._running:
            if self._in_rest_mode:
                await self._run_rest_cycle(symbols)
                # Periodically attempt to recover WS
                await self._try_promote_to_ws(symbols)
            else:
                await self._run_ws(symbols)

    # ------------------------------------------------------------------
    # WebSocket path
    # ------------------------------------------------------------------

    def _build_ws_url(self, symbols: Set[str]) -> str:
        streams = "/".join(f"{s.lower()}usdt@bookTicker" for s in sorted(symbols))
        return f"{_BINANCE_WS_BASE}?streams={streams}"

    async def _run_ws(self, symbols: Set[str]) -> None:
        url = self._build_ws_url(symbols)
        logger.info(f"SpotPricePoller: connecting Binance WS ({len(symbols)} streams)")
        try:
            async with self._session.ws_connect(url) as ws:
                self._ws_fail_count = 0
                logger.info("SpotPricePoller: Binance WS connected")
                async for msg in ws:
                    if not self._running:
                        return
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        self._ws_fail_count += 1
                        logger.warning(
                            f"SpotPricePoller: WS closed/error (fail #{self._ws_fail_count}), reconnecting"
                        )
                        if self._ws_fail_count >= _WS_FAIL_THRESHOLD:
                            logger.warning(
                                "SpotPricePoller: too many WS closes, switching to REST fallback"
                            )
                            self._in_rest_mode = True
                        break
        except Exception as exc:
            self._ws_fail_count += 1
            logger.warning(
                f"SpotPricePoller: WS failure #{self._ws_fail_count}: {exc}"
            )
            if self._ws_fail_count >= _WS_FAIL_THRESHOLD:
                logger.warning(
                    "SpotPricePoller: too many WS failures, switching to REST fallback"
                )
                self._in_rest_mode = True
            else:
                backoff = 5 * (2 ** (self._ws_fail_count - 1))  # 5, 10, 20 s
                logger.info(f"SpotPricePoller: reconnecting WS in {backoff}s")
                await asyncio.sleep(backoff)

    def _handle_ws_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)  # multi-stream envelope wraps in "data"
            symbol_raw = data.get("s", "")          # e.g. "BTCUSDT"
            symbol = symbol_raw.replace("USDT", "")
            bid = float(data.get("b", 0) or 0)
            ask = float(data.get("a", 0) or 0)
            ts_ms = int(time.time() * 1000)
            price = (bid + ask) / 2 if bid and ask else (bid or ask)
            self.db.save_spot_price(symbol, "binance_ws", ts_ms, price, bid, ask)
        except Exception as exc:
            logger.debug(f"SpotPricePoller: bad WS message: {exc}")

    # ------------------------------------------------------------------
    # REST fallback path
    # ------------------------------------------------------------------

    async def _run_rest_cycle(self, symbols: Set[str]) -> None:
        pairs = [f'"{s}USDT"' for s in sorted(symbols)]
        params = {"symbols": f"[{','.join(pairs)}]"}

        try:
            async with self._session.get(_BINANCE_REST_URL, params=params) as resp:
                if resp.status == 429:
                    self._rest_backoff = min(self._rest_backoff * 2, _MAX_REST_BACKOFF)
                    logger.warning(
                        f"SpotPricePoller: REST rate-limited (429), backing off {self._rest_backoff}s"
                    )
                    await asyncio.sleep(self._rest_backoff)
                    return
                if resp.status != 200:
                    logger.warning(f"SpotPricePoller: REST status {resp.status}")
                    await asyncio.sleep(self._rest_backoff)
                    return
                tickers = await resp.json()
                self._rest_backoff = self.poll_interval  # reset on success
                ts_ms = int(time.time() * 1000)
                for ticker in tickers:
                    symbol = ticker.get("symbol", "").replace("USDT", "")
                    bid = float(ticker.get("bidPrice", 0) or 0)
                    ask = float(ticker.get("askPrice", 0) or 0)
                    price = (bid + ask) / 2 if bid and ask else (bid or ask)
                    self.db.save_spot_price(symbol, "binance_rest", ts_ms, price, bid, ask)
        except Exception as exc:
            logger.error(f"SpotPricePoller: REST error: {exc}")

        await asyncio.sleep(self.poll_interval)

    async def _try_promote_to_ws(self, symbols: Set[str]) -> None:
        """After being in REST mode, attempt to switch back to WS after 60 s."""
        logger.debug("SpotPricePoller: waiting before WS recovery attempt")
        await asyncio.sleep(_WS_RETRY_INTERVAL)
        if not self._running:
            return
        logger.info("SpotPricePoller: attempting WS recovery from REST mode")
        self._ws_fail_count = 0
        self._in_rest_mode = False  # _run_ws will re-set if it fails again

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    async def _close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
