"""
WebSocket client for capturing real-time Polymarket CLOB data.
Connects to wss://ws.clob.polymarket.com and streams orderbook/trade updates.
Supports authenticated connections for full access.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Callable
import hmac
import hashlib
import base64
import time
import os

import websockets
from websockets.asyncio.client import ClientConnection
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get API credentials from environment
API_KEY = os.getenv('POLYMARKET_API_KEY')
API_SECRET = os.getenv('POLYMARKET_API_SECRET')
PASSPHRASE = os.getenv('POLYMARKET_PASSPHRASE')

if not all([API_KEY, API_SECRET, PASSPHRASE]):
    logger.warning("API credentials not found. Running in public mode (limited access).")
    AUTHENTICATED = False
else:
    AUTHENTICATED = True
    logger.info("API credentials loaded. Running in authenticated mode.")


def generate_auth_headers(method: str = "GET", request_path: str = "/ws") -> Dict[str, str]:
    """Generate Polymarket CLOB authentication headers."""
    if not AUTHENTICATED:
        return {}
    
    timestamp = str(int(time.time()))
    message = timestamp + method + request_path
    
    signature = hmac.new(
        base64.b64decode(API_SECRET),
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature_b64 = base64.b64encode(signature).decode('utf-8')
    
    return {
        'POLY_API_KEY': API_KEY,
        'POLY_PASSPHRASE': PASSPHRASE,
        'POLY_TIMESTAMP': timestamp,
        'POLY_SIGNATURE': signature_b64
    }


class WebSocketClient:
    """Manages WebSocket connection to Polymarket CLOB."""
    
    WS_URL = "wss://ws.clob.polymarket.com"
    PING_INTERVAL = 30  # seconds
    RECONNECT_DELAY = 5  # initial delay, will exponentially backoff
    
    def __init__(self, db_path: str = "polymarket_data.db"):
        self.db_path = db_path
        self.ws: Optional[ClientConnection] = None
        self.running = False
        self.subscribed_tokens: set[str] = set()
        self.reconnect_delay = self.RECONNECT_DELAY
        self.message_handlers: Dict[str, Callable] = {}
        
        # Initialize database
        self._init_database()
        
        # Register default handlers
        self.register_handler("l2_book", self._handle_l2_book)
        self.register_handler("trade", self._handle_trade)
        self.register_handler("ticker", self._handle_ticker)
        self.register_handler("best_bid_ask", self._handle_best_bid_ask)
    
    def _init_database(self):
        """Initialize SQLite database with required schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Markets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                token_id TEXT PRIMARY KEY,
                event_slug TEXT,
                question TEXT,
                outcomes TEXT,
                created_at TIMESTAMP,
                resolved_at TIMESTAMP,
                status TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Orderbook snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                timestamp TIMESTAMP,
                bids TEXT,
                asks TEXT,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                midpoint REAL
            )
        """)
        
        # Trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                timestamp TIMESTAMP,
                price REAL,
                size REAL,
                side TEXT,
                trade_id TEXT UNIQUE
            )
        """)
        
        # Ticker updates
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticker_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                timestamp TIMESTAMP,
                price REAL,
                volume_24h REAL,
                change_24h REAL
            )
        """)
        
        # Best bid/ask updates
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS best_bid_ask_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                timestamp TIMESTAMP,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                midpoint REAL
            )
        """)
        
        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_orderbook_token_time 
            ON orderbook_snapshots(token_id, timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_token_time 
            ON trades(token_id, timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticker_token_time 
            ON ticker_updates(token_id, timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bba_token_time 
            ON best_bid_ask_updates(token_id, timestamp)
        """)
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")
    
    def register_handler(self, event_type: str, handler: Callable):
        """Register a message handler for a specific event type."""
        self.message_handlers[event_type] = handler
        logger.debug(f"Registered handler for {event_type}")
    
    async def connect(self):
        """Establish WebSocket connection with optional authentication."""
        try:
            # Generate auth headers if credentials are available
            auth_headers = generate_auth_headers()
            
            # Prepare connection arguments
            connect_kwargs = {
                'ping_interval': self.PING_INTERVAL,
                'ping_timeout': 10,
            }
            
            # Add extra_headers if authenticated
            if auth_headers:
                connect_kwargs['extra_headers'] = auth_headers
                logger.info("Connecting with authentication...")
            else:
                logger.info("Connecting without authentication (public mode)...")
            
            self.ws = await websockets.connect(
                self.WS_URL,
                **connect_kwargs
            )
            logger.info(f"Connected to {self.WS_URL}")
            self.reconnect_delay = self.RECONNECT_DELAY  # Reset on success
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from WebSocket")
    
    async def subscribe(self, token_ids: List[str], channels: List[str] = None):
        """Subscribe to updates for specific tokens."""
        if channels is None:
            channels = ["l2_book", "trade", "ticker"]
        
        subscription_message = {
            "method": "SUBSCRIBE",
            "params": {
                "channels": [
                    {"name": channel, "token_ids": token_ids}
                    for channel in channels
                ]
            }
        }
        
        try:
            await self.ws.send(json.dumps(subscription_message))
            self.subscribed_tokens.update(token_ids)
            logger.info(f"Subscribed to {len(token_ids)} tokens on channels: {channels}")
        except Exception as e:
            logger.error(f"Subscription failed: {e}")
            raise
    
    async def unsubscribe(self, token_ids: List[str], channels: List[str] = None):
        """Unsubscribe from updates for specific tokens."""
        if channels is None:
            channels = ["l2_book", "trade", "ticker"]
        
        unsubscription_message = {
            "method": "UNSUBSCRIBE",
            "params": {
                "channels": [
                    {"name": channel, "token_ids": token_ids}
                    for channel in channels
                ]
            }
        }
        
        try:
            await self.ws.send(json.dumps(unsubscription_message))
            self.subscribed_tokens.difference_update(token_ids)
            logger.info(f"Unsubscribed from {len(token_ids)} tokens")
        except Exception as e:
            logger.error(f"Unsubscription failed: {e}")
    
    async def _handle_message(self, message: str):
        """Parse and route incoming messages to appropriate handlers."""
        try:
            data = json.loads(message)
            
            # Handle different message types
            if "type" in data:
                event_type = data["type"]
                if event_type in self.message_handlers:
                    await self.message_handlers[event_type](data)
                else:
                    logger.debug(f"No handler for event type: {event_type}")
            
            elif "event" in data:
                event_type = data["event"]
                if event_type in self.message_handlers:
                    await self.message_handlers[event_type](data)
                else:
                    logger.debug(f"No handler for event: {event_type}")
            
            # Handle subscription confirmations
            elif "method" in data and data.get("method") == "SUBSCRIBE":
                logger.info(f"Subscription confirmed: {data}")
            
            # Handle errors
            elif "error" in data:
                logger.error(f"WebSocket error: {data['error']}")
            
            else:
                logger.debug(f"Unknown message format: {data}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
    
    async def _handle_l2_book(self, data: Dict[str, Any]):
        """Handle orderbook update messages."""
        try:
            token_id = data.get("token_id") or data.get("market")
            if not token_id:
                return
            
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            timestamp = datetime.now(timezone.utc)
            
            # Calculate best bid/ask and spread
            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            spread = (best_ask - best_bid) if (best_bid and best_ask) else None
            midpoint = ((best_bid + best_ask) / 2) if (best_bid and best_ask) else None
            
            # Store in database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orderbook_snapshots 
                (token_id, timestamp, bids, asks, best_bid, best_ask, spread, midpoint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token_id,
                timestamp.isoformat(),
                json.dumps(bids),
                json.dumps(asks),
                best_bid,
                best_ask,
                spread,
                midpoint
            ))
            conn.commit()
            conn.close()
            
            logger.debug(f"Stored orderbook for {token_id}: bid={best_bid}, ask={best_ask}")
            
        except Exception as e:
            logger.error(f"Error handling l2_book: {e}")
    
    async def _handle_trade(self, data: Dict[str, Any]):
        """Handle trade execution messages."""
        try:
            token_id = data.get("token_id") or data.get("market")
            if not token_id:
                return
            
            trades = data.get("trades", [])
            if not trades:
                # Single trade format
                trades = [{
                    "price": data.get("price"),
                    "size": data.get("size"),
                    "side": data.get("side"),
                    "tradeID": data.get("tradeID") or data.get("trade_id")
                }]
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for trade in trades:
                timestamp = datetime.now(timezone.utc)
                cursor.execute("""
                    INSERT OR IGNORE INTO trades 
                    (token_id, timestamp, price, size, side, trade_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    token_id,
                    timestamp.isoformat(),
                    float(trade.get("price", 0)),
                    float(trade.get("size", 0)),
                    trade.get("side", ""),
                    str(trade.get("tradeID") or trade.get("trade_id", ""))
                ))
            
            conn.commit()
            conn.close()
            logger.debug(f"Stored {len(trades)} trades for {token_id}")
            
        except Exception as e:
            logger.error(f"Error handling trade: {e}")
    
    async def _handle_ticker(self, data: Dict[str, Any]):
        """Handle ticker update messages."""
        try:
            token_id = data.get("token_id") or data.get("market")
            if not token_id:
                return
            
            timestamp = datetime.now(timezone.utc)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ticker_updates 
                (token_id, timestamp, price, volume_24h, change_24h)
                VALUES (?, ?, ?, ?, ?)
            """, (
                token_id,
                timestamp.isoformat(),
                float(data.get("price", 0)),
                float(data.get("volume24h") or data.get("volume_24h", 0)),
                float(data.get("change24h") or data.get("change_24h", 0))
            ))
            conn.commit()
            conn.close()
            
            logger.debug(f"Stored ticker for {token_id}: price={data.get('price')}")
            
        except Exception as e:
            logger.error(f"Error handling ticker: {e}")
    
    async def _handle_best_bid_ask(self, data: Dict[str, Any]):
        """Handle best bid/ask update messages."""
        try:
            token_id = data.get("token_id") or data.get("market")
            if not token_id:
                return
            
            best_bid = float(data.get("best_bid_bid") or data.get("best_bid", 0))
            best_ask = float(data.get("best_ask_ask") or data.get("best_ask", 0))
            spread = best_ask - best_bid if (best_bid and best_ask) else None
            midpoint = (best_bid + best_ask) / 2 if (best_bid and best_ask) else None
            
            timestamp = datetime.now(timezone.utc)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO best_bid_ask_updates 
                (token_id, timestamp, best_bid, best_ask, spread, midpoint)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                token_id,
                timestamp.isoformat(),
                best_bid,
                best_ask,
                spread,
                midpoint
            ))
            conn.commit()
            conn.close()
            
            logger.debug(f"Stored BBA for {token_id}: {best_bid}/{best_ask}")
            
        except Exception as e:
            logger.error(f"Error handling best_bid_ask: {e}")
    
    async def run(self, token_ids: List[str], channels: List[str] = None):
        """Main loop - connect, subscribe, and process messages."""
        self.running = True
        
        while self.running:
            try:
                # Connect
                if not await self.connect():
                    logger.warning(f"Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, 60)
                    continue
                
                # Subscribe
                await self.subscribe(token_ids, channels)
                
                # Message processing loop
                async for message in self.ws:
                    if not self.running:
                        break
                    await self._handle_message(message)
                
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
            
            if self.running:
                logger.warning(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)
    
    def stop(self):
        """Stop the WebSocket client."""
        self.running = False
        logger.info("Stopping WebSocket client...")


async def main():
    """Example usage of WebSocketClient."""
    # Example token IDs (replace with actual market token IDs)
    token_ids = [
        "58097042028915691691707288370709969135249716122714156146991992373096973852681",
        "109553576899055322914561954174773868192997815073790549174493840159930517696797"
    ]
    
    client = WebSocketClient(db_path="polymarket_realtime.db")
    
    try:
        await client.run(token_ids, channels=["l2_book", "trade", "ticker"])
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        client.stop()


if __name__ == "__main__":
    asyncio.run(main())
