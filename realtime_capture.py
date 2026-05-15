"""
Polymarket Real-Time Data Capture - Multi-Strategy Implementation

Three approaches:
1. WebSocket (Primary) - Sub-minute ticks, orderbook, trades
2. High-Frequency REST Polling (Fallback) - Best available via REST
3. Hybrid (WebSocket + REST) - Combine both for redundancy

Usage:
    python3 realtime_capture.py --strategy websocket --tokens TOKEN1,TOKEN2
    python3 realtime_capture.py --strategy rest --tokens TOKEN1,TOKEN2 --interval 5
    python3 realtime_capture.py --strategy hybrid --tokens TOKEN1,TOKEN2
"""

import asyncio
import json
import time
import sqlite3
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Optional, Any, Set
from dataclasses import dataclass, asdict
from pathlib import Path
import ssl
import sys

# Try websockets library
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("WARNING: websockets library not installed. Run: pip install websockets")

import aiohttp
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('realtime_capture.log')
    ]
)
logger = logging.getLogger('realtime_capture')

# Constants
WEBSOCKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

DB_PATH = "polymarket_realtime.db"


@dataclass
class MarketData:
    """Unified market data structure"""
    token_id: str
    timestamp: float
    source: str  # 'websocket_l2', 'websocket_trade', 'websocket_ticker', 'rest_poll'
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_price: Optional[float] = None
    volume: Optional[float] = None
    orderbook_bid_depth: Optional[Dict] = None  # {price: size}
    orderbook_ask_depth: Optional[Dict] = None  # {price: size}
    trade_size: Optional[float] = None
    trade_side: Optional[str] = None  # 'buy' or 'sell'
    raw_data: Optional[Dict] = None


class DatabaseManager:
    """Handles all database operations"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Markets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS markets (
                token_id TEXT PRIMARY KEY,
                event_name TEXT,
                question TEXT,
                discovered_at REAL,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        # High-frequency price ticks (sub-minute data)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL,
                bid REAL,
                ask REAL,
                last_price REAL,
                spread REAL,
                mid_price REAL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        ''')
        
        # Individual trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                side TEXT NOT NULL,
                taker_order_id TEXT,
                maker_order_id TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        ''')
        
        # Orderbook snapshots (L2 data)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                bids_json TEXT NOT NULL,
                asks_json TEXT NOT NULL,
                best_bid REAL,
                best_ask REAL,
                bid_depth_total REAL,
                ask_depth_total REAL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        ''')
        
        # Ticker updates
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                volume_24h REAL,
                high_24h REAL,
                low_24h REAL,
                open_24h REAL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        ''')
        
        # Spot prices table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS spot_prices (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT    NOT NULL,
                source       TEXT    NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                price        REAL    NOT NULL,
                bid          REAL,
                ask          REAL,
                ingestion_ts INTEGER NOT NULL
            )
        ''')

        # Market assets mapping table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_assets (
                token_id    TEXT PRIMARY KEY,
                symbol      TEXT NOT NULL,
                resolved_at INTEGER NOT NULL
            )
        ''')

        # Create indexes for fast queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_ticks_token_time ON price_ticks(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_token_time ON trades(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_orderbook_token_time ON orderbook_snapshots(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticker_token_time ON ticker_updates(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_spot_symbol_ts ON spot_prices(symbol, timestamp_ms)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_spot_ts ON spot_prices(timestamp_ms)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_market_assets_symbol ON market_assets(symbol)')

        conn.commit()
        conn.close()
        logger.info(f"Database initialized: {self.db_path}")
    
    def save_price_tick(self, data: MarketData):
        """Save a price tick"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        spread = (data.ask - data.bid) if (data.bid and data.ask) else None
        mid_price = ((data.bid + data.ask) / 2) if (data.bid and data.ask) else None
        
        cursor.execute('''
            INSERT INTO price_ticks (token_id, timestamp, source, bid, ask, last_price, spread, mid_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data.token_id, data.timestamp, data.source, data.bid, data.ask, 
              data.last_price, spread, mid_price))
        
        conn.commit()
        conn.close()
    
    def save_trade(self, token_id: str, timestamp: float, price: float, 
                   size: float, side: str, taker_order_id: str = None, 
                   maker_order_id: str = None):
        """Save an individual trade"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO trades (token_id, timestamp, price, size, side, taker_order_id, maker_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (token_id, timestamp, price, size, side, taker_order_id, maker_order_id))
        
        conn.commit()
        conn.close()
    
    def save_orderbook(self, token_id: str, timestamp: float, 
                       bids: Dict, asks: Dict):
        """Save orderbook snapshot"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        best_bid = max(float(p) for p in bids.keys()) if bids else None
        best_ask = min(float(p) for p in asks.keys()) if asks else None
        bid_depth = sum(float(s) for s in bids.values()) if bids else 0
        ask_depth = sum(float(s) for s in asks.values()) if asks else 0
        
        cursor.execute('''
            INSERT INTO orderbook_snapshots 
            (token_id, timestamp, bids_json, asks_json, best_bid, best_ask, bid_depth_total, ask_depth_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (token_id, timestamp, json.dumps(bids), json.dumps(asks), 
              best_bid, best_ask, bid_depth, ask_depth))
        
        conn.commit()
        conn.close()
    
    def save_ticker(self, token_id: str, timestamp: float, volume: float = None,
                    high: float = None, low: float = None, open_price: float = None):
        """Save ticker update"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO ticker_updates (token_id, timestamp, volume_24h, high_24h, low_24h, open_24h)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (token_id, timestamp, volume, high, low, open_price))
        
        conn.commit()
        conn.close()
    
    def save_spot_price(self, symbol: str, source: str, timestamp_ms: int,
                        price: float, bid: float = None, ask: float = None):
        """Persist a single spot price tick."""
        ingestion_ts = int(time.time() * 1000)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO spot_prices (symbol, source, timestamp_ms, price, bid, ask, ingestion_ts) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (symbol, source, timestamp_ms, price, bid, ask, ingestion_ts)
        )
        conn.commit()
        conn.close()

    def save_market_assets(self, mapping: Dict[str, str]):
        """Bulk upsert token_id → symbol into market_assets."""
        if not mapping:
            return
        resolved_at = int(time.time() * 1000)
        rows = [(token_id, symbol, resolved_at) for token_id, symbol in mapping.items()]
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            'INSERT OR REPLACE INTO market_assets (token_id, symbol, resolved_at) VALUES (?, ?, ?)',
            rows
        )
        conn.commit()
        conn.close()
        logger.info(f"Saved {len(rows)} market asset mapping(s)")

    def register_market(self, token_id: str, event_name: str = None, question: str = None):
        """Register a market"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO markets (token_id, event_name, question, discovered_at, is_active)
            VALUES (?, ?, ?, ?, 1)
        ''', (token_id, event_name, question, time.time()))
        
        conn.commit()
        conn.close()


class WebSocketCapture:
    """WebSocket-based real-time data capture"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.tokens: List[str] = []
        # Polymarket CLOB WS supports: book, trade, user (user requires auth)
        self.channels: List[str] = ['book', 'trade']
        self.running = False
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        self.message_count = 0
        self.error_count = 0
        self._ws: Optional[Any] = None  # active WS handle for mid-session subscribe/unsubscribe
    
    async def connect(self) -> Optional[Any]:
        """Establish WebSocket connection with multiple fallback strategies"""
        
        # Strategy 1: Standard connection
        try:
            logger.info("Attempting WebSocket connection (standard)...")
            ws = await websockets.connect(
                WEBSOCKET_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10,
                max_size=10 * 1024 * 1024,  # 10MB max message
            )
            logger.info("WebSocket connected successfully (standard)")
            return ws
        except Exception as e:
            logger.warning(f"Standard connection failed: {e}")
        
        # Strategy 2: Custom SSL context
        try:
            logger.info("Attempting WebSocket connection (custom SSL)...")
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            ws = await websockets.connect(
                WEBSOCKET_URL,
                ssl=ssl_context,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info("WebSocket connected successfully (custom SSL)")
            return ws
        except Exception as e:
            logger.warning(f"Custom SSL connection failed: {e}")
        
        # Strategy 3: With additional headers
        try:
            logger.info("Attempting WebSocket connection (with headers)...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Origin': 'https://polymarket.com',
            }
            
            ws = await websockets.connect(
                WEBSOCKET_URL,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info("WebSocket connected successfully (with headers)")
            return ws
        except Exception as e:
            logger.warning(f"Connection with headers failed: {e}")
        
        # Strategy 4: Explicit TLS 1.2
        try:
            logger.info("Attempting WebSocket connection (TLS 1.2)...")
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            ws = await websockets.connect(
                WEBSOCKET_URL,
                ssl=ssl_context,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info("WebSocket connected successfully (TLS 1.2)")
            return ws
        except Exception as e:
            logger.warning(f"TLS 1.2 connection failed: {e}")
        
        logger.error("All WebSocket connection strategies failed")
        return None
    
    async def subscribe(self, ws: Any):
        """Subscribe to channels for all tokens (one message per channel)."""
        for channel in self.channels:
            msg = {
                "type": "subscribe",
                "assets_ids": self.tokens,
                "channel": channel,
            }
            await ws.send(json.dumps(msg))
            logger.info(f"Subscribed to '{channel}' for {len(self.tokens)} tokens")

    async def add_tokens(self, new_tokens: List[str]):
        """Subscribe to additional tokens on the live WebSocket (no reconnect)."""
        new_tokens = [t for t in new_tokens if t not in self.tokens]
        if not new_tokens:
            return
        self.tokens.extend(new_tokens)
        if self._ws is None:
            return  # will be picked up on next connect
        for channel in self.channels:
            try:
                await self._ws.send(json.dumps({
                    "type": "subscribe",
                    "assets_ids": new_tokens,
                    "channel": channel,
                }))
            except Exception as exc:
                logger.warning(f"add_tokens send failed on '{channel}': {exc}")
        logger.info(f"Subscribed to {len(new_tokens)} additional tokens")

    async def remove_tokens(self, old_tokens: List[str]):
        """Unsubscribe tokens on the live WebSocket (no reconnect)."""
        old_tokens = [t for t in old_tokens if t in self.tokens]
        if not old_tokens:
            return
        for t in old_tokens:
            try:
                self.tokens.remove(t)
            except ValueError:
                pass
        if self._ws is None:
            return
        for channel in self.channels:
            try:
                await self._ws.send(json.dumps({
                    "type": "unsubscribe",
                    "assets_ids": old_tokens,
                    "channel": channel,
                }))
            except Exception as exc:
                logger.warning(f"remove_tokens send failed on '{channel}': {exc}")
        logger.info(f"Unsubscribed from {len(old_tokens)} tokens")
    
    async def handle_message(self, message: str):
        """Process incoming WebSocket message.

        Polymarket CLOB WS sends JSON arrays of events; each event has
        event_type ('book' | 'price_change' | 'trade' | ...) and asset_id.
        """
        try:
            data = json.loads(message)
            self.message_count += 1

            events = data if isinstance(data, list) else [data]
            for ev in events:
                if not isinstance(ev, dict):
                    continue

                etype = ev.get('event_type') or ev.get('type') or ''
                if etype in ('book', 'book_snapshot', 'l2_book'):
                    await self.handle_l2_book(ev)
                elif etype == 'price_change':
                    await self.handle_l2_book(ev)
                elif etype in ('trade', 'last_trade_price'):
                    await self.handle_trade(ev)
                elif etype == 'tick_size_change':
                    pass  # informational
                elif ev.get('type') == 'subscription':
                    logger.info(f"Subscription confirmed: {ev}")
                else:
                    if self.message_count % 200 == 0:
                        logger.debug(f"Unknown event_type '{etype}': {json.dumps(ev)[:200]}")

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}, message: {message[:100]}")
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error handling message: {e}")
            if self.error_count > 100:
                logger.error("Too many errors, consider reconnecting")
    
    async def handle_l2_book(self, data: Dict):
        """Handle L2 orderbook update / price_change event."""
        token_id = data.get('asset_id') or data.get('token_id')
        if not token_id:
            return

        def _price(level):
            if isinstance(level, dict):
                return level.get('price'), level.get('size')
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                return level[0], level[1]
            return None, None

        timestamp = time.time()
        bids: Dict = {}
        asks: Dict = {}
        for lvl in data.get('bids', []) or []:
            p, s = _price(lvl)
            if p is not None:
                bids[str(p)] = s
        for lvl in data.get('asks', []) or []:
            p, s = _price(lvl)
            if p is not None:
                asks[str(p)] = s

        # price_change events carry single price/size/side instead of full book
        if not bids and not asks and data.get('price'):
            side = (data.get('side') or '').lower()
            p, s = data.get('price'), data.get('size')
            if side in ('buy', 'bid'):
                bids[str(p)] = s
            elif side in ('sell', 'ask'):
                asks[str(p)] = s

        self.db.save_orderbook(token_id, timestamp, bids, asks)

        best_bid = max(float(p) for p in bids.keys()) if bids else None
        best_ask = min(float(p) for p in asks.keys()) if asks else None

        tick = MarketData(
            token_id=token_id,
            timestamp=timestamp,
            source='websocket_l2',
            bid=best_bid,
            ask=best_ask,
            orderbook_bid_depth=bids,
            orderbook_ask_depth=asks,
        )
        self.db.save_price_tick(tick)

        if self.message_count % 500 == 0:
            logger.info(f"Book: {token_id[:16]}... bid={best_bid} ask={best_ask}")

    async def handle_trade(self, data: Dict):
        """Handle individual trade event."""
        token_id = data.get('asset_id') or data.get('token_id')
        if not token_id:
            return

        timestamp = time.time()
        price = float(data.get('price', 0) or 0)
        size = float(data.get('size', 0) or 0)
        side = data.get('side', 'unknown')
        taker_order_id = data.get('taker_order_id')
        maker_order_id = data.get('maker_order_id')
        
        # Save trade
        self.db.save_trade(
            token_id, timestamp, price, size, side,
            taker_order_id, maker_order_id
        )
        
        # Also save as price tick
        tick = MarketData(
            token_id=token_id,
            timestamp=timestamp,
            source='websocket_trade',
            last_price=price,
            trade_size=size,
            trade_side=side
        )
        self.db.save_price_tick(tick)
        
        if self.message_count % 500 == 0:
            logger.info(f"Trade: {token_id} - {side} {size} @ {price}")
    
    async def handle_ticker(self, data: Dict):
        """Handle ticker update"""
        payload = data.get('data', {})
        token_id = payload.get('token_id')
        if not token_id:
            return
        
        timestamp = time.time()
        volume = float(payload.get('volume', 0)) if payload.get('volume') else None
        high = float(payload.get('high', 0)) if payload.get('high') else None
        low = float(payload.get('low', 0)) if payload.get('low') else None
        open_price = float(payload.get('open', 0)) if payload.get('open') else None
        
        self.db.save_ticker(token_id, timestamp, volume, high, low, open_price)
    
    async def handle_best_bid_ask(self, data: Dict):
        """Handle best bid/ask update"""
        payload = data.get('data', {})
        token_id = payload.get('token_id')
        if not token_id:
            return
        
        timestamp = time.time()
        bid = float(payload.get('bid', 0)) if payload.get('bid') else None
        ask = float(payload.get('ask', 0)) if payload.get('ask') else None
        
        tick = MarketData(
            token_id=token_id,
            timestamp=timestamp,
            source='websocket_best_bid_ask',
            bid=bid,
            ask=ask
        )
        self.db.save_price_tick(tick)
        
        if self.message_count % 1000 == 0:
            logger.info(f"BBA: {token_id} - Bid: {bid}, Ask: {ask}")
    
    async def run(self, tokens: List[str]):
        """Main WebSocket capture loop"""
        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets library not available")
            return
        
        self.tokens = tokens
        logger.info(f"Starting WebSocket capture for {len(tokens)} tokens")
        
        # Register markets
        for token_id in tokens:
            self.db.register_market(token_id)
        
        self.running = True
        
        while self.running:
            ws = await self.connect()
            
            if ws is None:
                logger.warning(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
                continue
            
            self.reconnect_delay = 5  # Reset on successful connection
            self._ws = ws

            try:
                await self.subscribe(ws)

                # Message processing loop
                async for message in ws:
                    if not self.running:
                        break
                    await self.handle_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                self._ws = None
                try:
                    await ws.close()
                except:
                    pass
            
            if self.running:
                logger.info(f"Stats - Messages: {self.message_count}, Errors: {self.error_count}")
                await asyncio.sleep(self.reconnect_delay)
    
    def stop(self):
        """Stop the capture"""
        self.running = False
        logger.info("Stopping WebSocket capture...")


class RESTPollingCapture:
    """High-frequency REST polling as fallback or hybrid approach"""
    
    def __init__(self, db: DatabaseManager, poll_interval: float = 1.0):
        self.db = db
        self.poll_interval = poll_interval  # Seconds between polls
        self.tokens: List[str] = []
        self.running = False
        self.session: Optional[aiohttp.ClientSession] = None
        self.poll_count = 0
        self.error_count = 0
    
    async def start_session(self):
        """Start aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def fetch_prices(self, token_ids: List[str]) -> Dict:
        """Fetch prices for multiple tokens"""
        if not self.session:
            await self.start_session()
        
        # Batch tokens (API limit: 100 per request)
        batch_size = 50
        all_prices = {}
        
        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i+batch_size]
            token_param = ','.join(batch)
            url = f"{CLOB_API_BASE}/prices?token_ids={token_param}"
            
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, list):
                            for item in data:
                                token_id = item.get('token_id')
                                if token_id:
                                    all_prices[token_id] = {
                                        'bid': float(item.get('bid', 0)),
                                        'ask': float(item.get('ask', 0)),
                                        'last': float(item.get('last', 0)),
                                        'volume': float(item.get('volume', 0))
                                    }
                    else:
                        logger.warning(f"Price fetch failed: {response.status}")
            except Exception as e:
                logger.error(f"Error fetching prices: {e}")
                self.error_count += 1
        
        return all_prices
    
    async def fetch_orderbook(self, token_id: str) -> Optional[Dict]:
        """Fetch orderbook for a single token"""
        if not self.session:
            await self.start_session()
        
        url = f"{CLOB_API_BASE}/book?token_id={token_id}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'bids': {item['price']: item['size'] for item in data.get('bids', [])},
                        'asks': {item['price']: item['size'] for item in data.get('asks', [])}
                    }
        except Exception as e:
            logger.error(f"Error fetching orderbook for {token_id}: {e}")
            self.error_count += 1
        
        return None
    
    async def run(self, tokens: List[str]):
        """Main polling loop"""
        self.tokens = tokens
        logger.info(f"Starting REST polling (interval: {self.poll_interval}s) for {len(tokens)} tokens")
        
        # Register markets
        for token_id in tokens:
            self.db.register_market(token_id)
        
        self.running = True
        
        while self.running:
            start_time = time.time()
            
            # Fetch all prices
            prices = await self.fetch_prices(tokens)
            timestamp = time.time()
            
            # Save price ticks
            for token_id, price_data in prices.items():
                tick = MarketData(
                    token_id=token_id,
                    timestamp=timestamp,
                    source='rest_poll',
                    bid=price_data.get('bid'),
                    ask=price_data.get('ask'),
                    last_price=price_data.get('last'),
                    volume=price_data.get('volume')
                )
                self.db.save_price_tick(tick)
            
            self.poll_count += 1
            
            # Log progress
            elapsed = time.time() - start_time
            if self.poll_count % 10 == 0:
                logger.info(f"Poll #{self.poll_count}: {len(prices)} tokens, {elapsed:.2f}s")
            
            # Sleep to maintain interval
            sleep_time = max(0, self.poll_interval - elapsed)
            await asyncio.sleep(sleep_time)
    
    def stop(self):
        """Stop polling"""
        self.running = False
        logger.info("Stopping REST polling...")


class HybridCapture:
    """Combine WebSocket and REST polling for maximum coverage"""
    
    def __init__(self, db: DatabaseManager, poll_interval: float = 5.0):
        self.db = db
        self.poll_interval = poll_interval
        self.ws_capture = WebSocketCapture(db)
        self.rest_capture = RESTPollingCapture(db, poll_interval)
        self.tokens: List[str] = []
        self.running = False
    
    async def run(self, tokens: List[str]):
        """Run both WebSocket and REST polling concurrently"""
        self.tokens = tokens
        logger.info(f"Starting Hybrid capture for {len(tokens)} tokens")
        logger.info(f"  - WebSocket: l2_book, trade, ticker, best_bid_ask")
        logger.info(f"  - REST polling: every {self.poll_interval}s")
        
        self.running = True
        
        # Run both concurrently
        ws_task = asyncio.create_task(self.ws_capture.run(tokens))
        rest_task = asyncio.create_task(self.rest_capture.run(tokens))
        
        # Wait for either to stop
        done, pending = await asyncio.wait(
            [ws_task, rest_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancel remaining
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    def stop(self):
        """Stop both capture methods"""
        self.running = False
        self.ws_capture.stop()
        self.rest_capture.stop()
        logger.info("Stopping Hybrid capture...")


async def run_pruning_loop(db_path: str, retention_days: int = 7):
    """Delete rows older than retention_days every 6 hours."""
    logger.info(f"PruningJob: started (retention={retention_days}d, interval=6h)")
    while True:
        await asyncio.sleep(6 * 3600)
        cutoff_ms = int((time.time() - retention_days * 86400) * 1000)
        cutoff_secs = cutoff_ms / 1000.0
        cutoff_iso = datetime.utcfromtimestamp(cutoff_secs).isoformat()
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM spot_prices WHERE timestamp_ms < ?", (cutoff_ms,))
            conn.execute("DELETE FROM orderbook_snapshots WHERE timestamp < ?", (cutoff_secs,))
            conn.execute("DELETE FROM trades WHERE timestamp < ?", (cutoff_secs,))
            conn.execute("DELETE FROM price_ticks WHERE timestamp < ?", (cutoff_secs,))
            conn.execute("DELETE FROM ticker_updates WHERE timestamp < ?", (cutoff_secs,))
            conn.execute("VACUUM")
            conn.commit()
            conn.close()
            logger.info(f"PruningJob: pruned rows older than {cutoff_iso}")
        except Exception as exc:
            logger.error(f"PruningJob: error during pruning: {exc}")


async def _resilient_task(coro_fn, *args, max_restarts: int = 5, restart_delay: float = 5.0,
                          label: str = "task", **kwargs):
    """Run a coroutine function with automatic restart on failure (up to max_restarts)."""
    restarts = 0
    while restarts <= max_restarts:
        try:
            await coro_fn(*args, **kwargs)
            return  # clean exit
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            restarts += 1
            if restarts > max_restarts:
                logger.error(f"{label}: exceeded max restarts ({max_restarts}), giving up: {exc}")
                return
            logger.warning(f"{label}: crashed (attempt {restarts}/{max_restarts}): {exc}. Restarting in {restart_delay}s")
            await asyncio.sleep(restart_delay)


def load_discovered_tokens(filepath: str = 'discovered_tokens.json') -> List[str]:
    """Load tokens from discovery file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            tokens = data.get('token_ids', [])
            logger.info(f"Loaded {len(tokens)} tokens from {filepath}")
            return tokens
    except FileNotFoundError:
        logger.error(f"File not found: {filepath}")
        return []
    except Exception as e:
        logger.error(f"Error loading tokens: {e}")
        return []


TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
TIMEFRAME_TAG = {"5m": "5M", "15m": "15M", "1h": "1H", "4h": "4H"}


def discover_rolling_btc_markets(timeframes: List[str]) -> List[dict]:
    """
    Discover currently-active rolling BTC Up/Down markets, one per timeframe.

    Filters: seriesSlug == f'btc-up-or-down-{tf}', closed is False, endDate > now.
    Picks the smallest endDate per timeframe → the currently-resolving market.

    Returns list of dicts with: timeframe, slug, end_dt, window_start_dt,
        token_up, token_down, question.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import json as _json

    now = _dt.now(_tz.utc)
    result: List[dict] = []

    for tf in timeframes:
        if tf not in TIMEFRAME_MINUTES:
            logger.warning(f"Unknown timeframe '{tf}', skipping")
            continue

        try:
            resp = requests.get(
                f"{GAMMA_API_BASE}/events",
                params={
                    "tag_slug": TIMEFRAME_TAG[tf],
                    "end_date_min": now.isoformat(),
                    "limit": 50,
                    "order": "endDate",
                    "ascending": "true",
                },
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as exc:
            logger.error(f"Rolling discovery {tf} failed: {exc}")
            continue

        target_series = f"btc-up-or-down-{tf}"
        candidates = []
        for ev in events:
            if ev.get("seriesSlug") != target_series:
                continue
            if ev.get("closed", True):
                continue
            end_str = ev.get("endDate", "")
            try:
                end_dt = _dt.fromisoformat(end_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if end_dt <= now:
                continue
            candidates.append((end_dt, ev))

        if not candidates:
            logger.warning(f"No active rolling {tf} BTC market found")
            continue

        end_dt, ev = min(candidates, key=lambda x: x[0])
        markets = ev.get("markets", [])
        if not markets:
            continue
        market = markets[0]

        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            try:
                clob_ids = _json.loads(clob_ids)
            except Exception:
                continue
        if len(clob_ids) < 2:
            continue

        window_start_dt = end_dt - _td(minutes=TIMEFRAME_MINUTES[tf])
        result.append({
            "timeframe":       tf,
            "slug":            ev.get("slug", ""),
            "question":        market.get("question", ev.get("title", "")),
            "end_dt":          end_dt,
            "window_start_dt": window_start_dt,
            "token_up":        clob_ids[0],
            "token_down":      clob_ids[1],
        })

    logger.info(
        f"Rolling discovery: {len(result)}/{len(timeframes)} timeframes resolved "
        f"({[m['timeframe'] for m in result]})"
    )
    return result


async def run_anchor_recorder(
    rotator: 'RollingMarketRotator',
    db: 'DatabaseManager',
    poll_interval: float = 1.0,
):
    """
    For each active rolling market, once now >= window_start_dt and no anchor has
    been recorded, snapshot Binance BTC/USDT spot and freeze it as the anchor.
    Writes to spot_prices with source=f'anchor_{tf}'.
    """
    from datetime import datetime as _dt, timezone as _tz
    binance_url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                now = _dt.now(_tz.utc)
                pending = []
                for tf, market in rotator.active.items():
                    if rotator.anchors.get(tf) is not None:
                        continue
                    if now >= market["window_start_dt"]:
                        pending.append((tf, market))

                if pending:
                    try:
                        async with session.get(
                            binance_url, timeout=aiohttp.ClientTimeout(total=5)
                        ) as r:
                            payload = await r.json()
                            price = float(payload["price"])
                    except Exception as exc:
                        logger.warning(f"Anchor fetch failed: {exc}")
                        await asyncio.sleep(poll_interval)
                        continue

                    ts_ms = int(now.timestamp() * 1000)
                    for tf, market in pending:
                        rotator.anchors[tf] = price
                        db.save_spot_price(
                            symbol="BTC",
                            source=f"anchor_{tf}",
                            timestamp_ms=ts_ms,
                            price=price,
                            bid=price,
                            ask=price,
                        )
                        logger.info(
                            f"Anchor[{tf}]: locked at ${price:,.2f} for "
                            f"{market['slug']} (window ends {market['end_dt']:%H:%M:%S} UTC)"
                        )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Anchor recorder error: {exc}")
            await asyncio.sleep(poll_interval)


class RollingMarketRotator:
    """
    Tracks the currently-active rolling BTC Up/Down market per timeframe.
    Every `poll_interval` seconds re-discovers and emits add/remove token diffs.

    Shared `anchors` dict: {timeframe -> anchor_price}. Reset to None on rotation.
    Shared `active` dict: {timeframe -> RollingMarket dict}. Replaced on rotation.
    """

    def __init__(self, timeframes: List[str], capture: 'WebSocketCapture',
                 db: 'DatabaseManager', poll_interval: float = 15.0):
        self.timeframes = timeframes
        self.capture = capture
        self.db = db
        self.poll_interval = poll_interval
        self.active: Dict[str, dict] = {}          # tf -> RollingMarket
        self.anchors: Dict[str, Optional[float]] = {tf: None for tf in timeframes}
        self._stop = False

    def stop(self):
        self._stop = True

    def tokens_for(self, market: dict) -> List[str]:
        return [market["token_up"], market["token_down"]]

    async def discover_once(self) -> List[dict]:
        # Discovery is sync (requests); run in default executor
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, discover_rolling_btc_markets, self.timeframes
        )

    async def run(self):
        # Initial bootstrap
        await self._apply()
        # Periodic rediscovery
        while not self._stop:
            await asyncio.sleep(self.poll_interval)
            if self._stop:
                break
            await self._apply()

    async def _apply(self):
        try:
            fresh = await self.discover_once()
        except Exception as exc:
            logger.warning(f"Rotator: discovery error: {exc}")
            return

        fresh_by_tf = {m["timeframe"]: m for m in fresh}

        for tf in self.timeframes:
            new = fresh_by_tf.get(tf)
            old = self.active.get(tf)

            if new is None:
                continue  # nothing active right now for this tf; keep old until it expires

            if old is None or old.get("slug") != new.get("slug"):
                # rotation (or first attach)
                old_tokens = self.tokens_for(old) if old else []
                new_tokens = self.tokens_for(new)

                if old:
                    logger.info(
                        f"Rotator[{tf}]: rotating {old['slug']} -> {new['slug']} "
                        f"(window {new['window_start_dt']:%H:%M:%S}-{new['end_dt']:%H:%M:%S} UTC)"
                    )
                else:
                    logger.info(
                        f"Rotator[{tf}]: attached {new['slug']} "
                        f"(window {new['window_start_dt']:%H:%M:%S}-{new['end_dt']:%H:%M:%S} UTC)"
                    )

                if old_tokens:
                    await self.capture.remove_tokens(old_tokens)
                await self.capture.add_tokens(new_tokens)

                # Register markets in DB
                for tok in new_tokens:
                    self.db.register_market(tok, event_name=new.get("slug"),
                                            question=new.get("question"))

                self.active[tf] = new
                self.anchors[tf] = None  # reset anchor for the new window


async def main():
    parser = argparse.ArgumentParser(description='Polymarket Real-Time Data Capture')
    parser.add_argument('--strategy', choices=['websocket', 'rest', 'hybrid'],
                       default='websocket', help='Capture strategy')
    parser.add_argument('--tokens', type=str, help='Comma-separated token IDs')
    parser.add_argument('--token-file', type=str, default=None,
                       help='File containing discovered tokens (default: live Gamma API discovery)')
    parser.add_argument('--interval', type=float, default=1.0,
                       help='REST polling interval in seconds')
    parser.add_argument('--limit', type=int, default=0,
                       help='Limit number of tokens (0 = all)')
    parser.add_argument('--spot-interval', type=float, default=1.0,
                       help='Spot price REST fallback poll interval in seconds (default: 1.0)')
    parser.add_argument('--retention-days', type=int, default=7,
                       help='Days to retain time-series rows before pruning (default: 7)')
    parser.add_argument('--disable-spot', action='store_true',
                       help='Skip spot price capture entirely')
    parser.add_argument('--disable-pruning', action='store_true',
                       help='Disable the retention pruning job (data grows unbounded)')
    parser.add_argument('--timeframes', type=str, default='5m,15m',
                       help='Rolling BTC Up/Down timeframes to watch (comma-sep). '
                            'Valid: 5m,15m,1h,4h. Default: 5m,15m')
    parser.add_argument('--rotator-interval', type=float, default=15.0,
                       help='Seconds between rolling-market rediscovery (default: 15)')

    args = parser.parse_args()

    # Initialize database
    db = DatabaseManager()

    # Parse timeframes
    timeframes = [t.strip() for t in args.timeframes.split(',') if t.strip()]
    invalid = [t for t in timeframes if t not in TIMEFRAME_MINUTES]
    if invalid:
        logger.error(f"Invalid timeframes: {invalid}. Valid: {list(TIMEFRAME_MINUTES)}")
        return

    # Resolution path: rolling rotator (default) vs manual tokens/file
    use_rotator = not (args.tokens or args.token_file)

    # Get initial tokens
    if args.tokens:
        tokens = [t.strip() for t in args.tokens.split(',')]
    elif args.token_file:
        tokens = load_discovered_tokens(args.token_file)
    else:
        # Bootstrap from rolling discovery; rotator will keep this in sync
        initial_markets = discover_rolling_btc_markets(timeframes)
        tokens = []
        for m in initial_markets:
            tokens.append(m["token_up"])
            tokens.append(m["token_down"])

    if not tokens:
        logger.error("No tokens to subscribe. Check network or pass --tokens / --token-file")
        return

    if args.limit > 0:
        tokens = tokens[:args.limit]

    logger.info(f"Using {len(tokens)} tokens "
                f"({'rolling rotator' if use_rotator else 'manual'})")

    # Spot assets: for rolling mode we know it's BTC. For manual mode, resolve via file/markets.
    spot_symbols: Set[str] = set()
    if not args.disable_spot:
        if use_rotator:
            spot_symbols = {"BTC"}
            db.save_market_assets({t: "BTC" for t in tokens})
        else:
            try:
                from spot_asset_resolver import resolve_assets
                token_to_symbol, spot_symbols = resolve_assets(
                    args.token_file or 'discovered_tokens.json'
                )
                db.save_market_assets(token_to_symbol)
            except Exception as exc:
                logger.error(f"Spot asset resolution failed (continuing without spot): {exc}")

    # Select Polymarket capture strategy
    if args.strategy == 'websocket':
        capture = WebSocketCapture(db)
    elif args.strategy == 'rest':
        capture = RESTPollingCapture(db, args.interval)
    else:  # hybrid
        capture = HybridCapture(db, args.interval)

    # Handle shutdown
    import signal

    all_tasks: List[asyncio.Task] = []

    def signal_handler(sig, frame):
        logger.info("\nShutdown signal received")
        capture.stop()
        for t in all_tasks:
            t.cancel()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run capture + optional spot poller + pruning job + rotator/anchor concurrently
    try:
        spot_task: Optional[asyncio.Task] = None
        prune_task: Optional[asyncio.Task] = None
        rotator_task: Optional[asyncio.Task] = None
        anchor_task: Optional[asyncio.Task] = None

        poly_task = asyncio.create_task(capture.run(tokens), name="polymarket_capture")
        all_tasks.append(poly_task)

        if not args.disable_spot and spot_symbols:
            from spot_price_poller import SpotPricePoller
            poller = SpotPricePoller(db, poll_interval=args.spot_interval)
            spot_task = asyncio.create_task(
                _resilient_task(poller.run, spot_symbols, label="SpotPricePoller"),
                name="spot_poller"
            )
            all_tasks.append(spot_task)

        if not args.disable_pruning:
            prune_task = asyncio.create_task(
                run_pruning_loop(db.db_path, args.retention_days),
                name="pruning_job"
            )
            all_tasks.append(prune_task)

        # Rotator + anchor recorder only when running in rolling mode with a WS capture
        if use_rotator and isinstance(capture, WebSocketCapture):
            rotator = RollingMarketRotator(
                timeframes=timeframes,
                capture=capture,
                db=db,
                poll_interval=args.rotator_interval,
            )
            rotator_task = asyncio.create_task(
                _resilient_task(rotator.run, label="RollingRotator"),
                name="rolling_rotator",
            )
            anchor_task = asyncio.create_task(
                _resilient_task(run_anchor_recorder, rotator, db, label="AnchorRecorder"),
                name="anchor_recorder",
            )
            all_tasks.extend([rotator_task, anchor_task])

        try:
            await poly_task
        except asyncio.CancelledError:
            pass
        finally:
            for t in [spot_task, prune_task, rotator_task, anchor_task]:
                if t and not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        capture.stop()
        logger.info("Capture stopped")


if __name__ == '__main__':
    asyncio.run(main())
