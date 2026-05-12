# Real-Time Data Capture System

## Overview
This system captures real-time Polymarket CLOB data via WebSocket and stores it in SQLite for offline analysis.

## Components

### 1. `websocket_client.py`
Core WebSocket client that:
- Connects to `wss://ws.clob.polymarket.com`
- Subscribes to market updates (orderbook, trades, ticker)
- Stores all data in SQLite database
- Handles reconnection with exponential backoff

### 2. `market_discovery.py`
Market discovery service that:
- Fetches active crypto markets from Gamma API
- Extracts token IDs for WebSocket subscription
- Saves discovered markets to `discovered_tokens.json`

### 3. Database Schema (`polymarket_data.db`)
SQLite database with tables for:
- `markets` - Market metadata
- `orderbook_snapshots` - L2 orderbook updates
- `trades` - Trade executions
- `ticker_updates` - Price ticker data
- `best_bid_ask_updates` - Top-of-book changes

## Quick Start

### Step 1: Discover Markets
```bash
python3 market_discovery.py
```
This creates `discovered_tokens.json` with active market token IDs.

### Step 2: Start WebSocket Capture
```bash
# Edit websocket_client.py to use discovered tokens
# Or run with custom token list:
python3 -c "
import asyncio
from websocket_client import WebSocketClient
import json

with open('discovered_tokens.json') as f:
    data = json.load(f)

# Use first 20 tokens for demo
token_ids = data['token_ids'][:20]
client = WebSocketClient(db_path='polymarket_realtime.db')

async def start():
    await client.run(token_ids, channels=['l2_book', 'trade', 'ticker'])

asyncio.run(start())
"
```

### Step 3: Query Stored Data
```python
import sqlite3

conn = sqlite3.connect('polymarket_realtime.db')
cursor = conn.cursor()

# Get recent orderbook snapshots
cursor.execute('''
    SELECT token_id, timestamp, best_bid, best_ask, spread, midpoint
    FROM orderbook_snapshots
    ORDER BY timestamp DESC
    LIMIT 10
''')

for row in cursor.fetchall():
    print(row)

conn.close()
```

## Configuration

### WebSocket URL
- **Production**: `wss://ws.clob.polymarket.com`
- **Testnet**: (if available)

### Subscription Channels
- `l2_book` - Full orderbook updates
- `trade` - Trade executions
- `ticker` - Price ticker
- `best_bid_ask` - Top-of-book only (lighter weight)

### Database Location
Default: `polymarket_data.db` in current directory
Custom: `WebSocketClient(db_path="custom_path.db")`

## Data Volume Estimates

| Channel | Updates/Market/Sec | 100 Markets/Day |
|---------|-------------------|-----------------|
| l2_book | ~5 | ~43M records |
| trade | ~0.5 | ~4.3M records |
| ticker | ~1 | ~8.6M records |
| best_bid_ask | ~2 | ~17M records |

**Recommendation**: Start with `best_bid_ask` + `trade` for lightweight capture.

## Analysis Integration

Once data is captured, you can:
1. Query historical prices directly from SQLite
2. Reconstruct orderbook history
3. Calculate VWAP, spreads, volatility
4. Backtest strategies on tick-level data

Example analysis query:
```sql
-- Calculate hourly OHLC for a market
SELECT 
    strftime('%Y-%m-%d %H:00', timestamp) as hour,
    MAX(midpoint) as high,
    MIN(midpoint) as low,
    FIRST_VALUE(midpoint) OVER (PARTITION BY strftime('%Y-%m-%d %H', timestamp)) as open,
    LAST_VALUE(midpoint) OVER (PARTITION BY strftime('%Y-%m-%d %H', timestamp)) as close
FROM orderbook_snapshots
WHERE token_id = '...'
GROUP BY hour
ORDER BY hour;
```

## Troubleshooting

### Connection Issues
- Check DNS resolution: `nslookup ws.clob.polymarket.com`
- Verify firewall allows WebSocket connections
- Check logs for specific error messages

### No Data Received
- Verify token IDs are valid and active
- Check subscription confirmation in logs
- Ensure markets have orderbook enabled

### Database Performance
- Run `VACUUM` periodically to optimize
- Consider partitioning for large datasets
- Add indexes for common query patterns

## Next Steps

1. **Run continuous capture** - Start with 10-20 active markets
2. **Build analysis functions** - Port existing analysis to SQL queries
3. **Add monitoring** - Track data quality and gaps
4. **Implement backfill** - Use REST API to fill historical gaps
5. **Scale up** - Add more markets as storage allows
