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
from typing import List, Dict, Optional, Any
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
WEBSOCKET_URL = "wss://ws.clob.polymarket.com"
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
        
        # Create indexes for fast queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_ticks_token_time ON price_ticks(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_token_time ON trades(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_orderbook_token_time ON orderbook_snapshots(token_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticker_token_time ON ticker_updates(token_id, timestamp)')
        
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
        self.channels: List[str] = ['l2_book', 'trade', 'ticker', 'best_bid_ask']
        self.running = False
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        self.message_count = 0
        self.error_count = 0
    
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
        """Subscribe to channels for all tokens"""
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": self.channels,
                "token_ids": self.tokens
            }
        }
        
        await ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {len(self.channels)} channels for {len(self.tokens)} tokens")
        
        # Wait for subscription confirmation
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            logger.info(f"Subscription response: {response[:200]}...")
        except asyncio.TimeoutError:
            logger.warning("Subscription confirmation timeout")
    
    async def handle_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            self.message_count += 1
            
            msg_type = data.get('type', 'unknown')
            
            if msg_type == 'l2_book':
                await self.handle_l2_book(data)
            elif msg_type == 'trade':
                await self.handle_trade(data)
            elif msg_type == 'ticker':
                await self.handle_ticker(data)
            elif msg_type == 'best_bid_ask':
                await self.handle_best_bid_ask(data)
            elif msg_type == 'subscription':
                logger.info(f"Subscription confirmed: {data}")
            else:
                if self.message_count % 100 == 0:
                    logger.debug(f"Received unknown message type: {msg_type}")
                    
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}, message: {message[:100]}")
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error handling message: {e}")
            if self.error_count > 100:
                logger.error("Too many errors, consider reconnecting")
    
    async def handle_l2_book(self, data: Dict):
        """Handle L2 orderbook update"""
        payload = data.get('data', {})
        token_id = payload.get('token_id')
        if not token_id:
            return
        
        timestamp = time.time()
        bids = {item['price']: item['size'] for item in payload.get('bids', [])}
        asks = {item['price']: item['size'] for item in payload.get('asks', [])}
        
        # Save orderbook snapshot
        self.db.save_orderbook(token_id, timestamp, bids, asks)
        
        # Also save as price tick with best bid/ask
        best_bid = max(float(p) for p in bids.keys()) if bids else None
        best_ask = min(float(p) for p in asks.keys()) if asks else None
        
        tick = MarketData(
            token_id=token_id,
            timestamp=timestamp,
            source='websocket_l2',
            bid=best_bid,
            ask=best_ask,
            orderbook_bid_depth=bids,
            orderbook_ask_depth=asks
        )
        self.db.save_price_tick(tick)
        
        if self.message_count % 500 == 0:
            logger.info(f"L2 Book: {token_id} - Bid: {best_bid}, Ask: {best_ask}")
    
    async def handle_trade(self, data: Dict):
        """Handle individual trade"""
        payload = data.get('data', {})
        token_id = payload.get('token_id')
        if not token_id:
            return
        
        timestamp = time.time()
        price = float(payload.get('price', 0))
        size = float(payload.get('size', 0))
        side = payload.get('side', 'unknown')
        taker_order_id = payload.get('taker_order_id')
        maker_order_id = payload.get('maker_order_id')
        
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


async def main():
    parser = argparse.ArgumentParser(description='Polymarket Real-Time Data Capture')
    parser.add_argument('--strategy', choices=['websocket', 'rest', 'hybrid'], 
                       default='websocket', help='Capture strategy')
    parser.add_argument('--tokens', type=str, help='Comma-separated token IDs')
    parser.add_argument('--token-file', type=str, default='discovered_tokens.json',
                       help='File containing discovered tokens')
    parser.add_argument('--interval', type=float, default=1.0,
                       help='REST polling interval in seconds')
    parser.add_argument('--limit', type=int, default=0,
                       help='Limit number of tokens (0 = all)')
    
    args = parser.parse_args()
    
    # Initialize database
    db = DatabaseManager()
    
    # Get tokens
    if args.tokens:
        tokens = [t.strip() for t in args.tokens.split(',')]
    else:
        tokens = load_discovered_tokens(args.token_file)
    
    if not tokens:
        logger.error("No tokens specified. Use --tokens or ensure token file exists")
        return
    
    if args.limit > 0:
        tokens = tokens[:args.limit]
    
    logger.info(f"Using {len(tokens)} tokens")
    
    # Select strategy
    if args.strategy == 'websocket':
        capture = WebSocketCapture(db)
    elif args.strategy == 'rest':
        capture = RESTPollingCapture(db, args.interval)
    else:  # hybrid
        capture = HybridCapture(db, args.interval)
    
    # Handle shutdown
    import signal
    
    def signal_handler(sig, frame):
        logger.info("\nShutdown signal received")
        capture.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run capture
    try:
        await capture.run(tokens)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        capture.stop()
        logger.info("Capture stopped")


if __name__ == '__main__':
    asyncio.run(main())
