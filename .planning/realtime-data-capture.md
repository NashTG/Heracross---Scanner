# Real-Time Data Capture Strategy

## Overview
Switch from on-demand REST API polling to continuous WebSocket data capture with offline analysis.

## Benefits
1. **No rate limits** - WebSocket pushes data, no HTTP request quotas
2. **Complete history** - Capture every tick, not just sampled snapshots
3. **Lower latency** - Real-time updates vs polling intervals
4. **Resilient to API changes** - Less dependent on slug schema drift
5. **Better backtesting** - Full tick-level data for strategy validation

## Architecture

### Components
1. **WebSocket Client** - Connects to `wss://ws.clob.polymarket.com`
2. **Message Handler** - Parses and validates incoming messages
3. **Storage Layer** - SQLite database for persistent storage
4. **Subscription Manager** - Manages market subscriptions dynamically
5. **Analysis Engine** - Offline analysis on stored data

### WebSocket Endpoints
- **URL**: `wss://ws.clob.polymarket.com`
- **Event Types**:
  - `l2_book` - Orderbook updates
  - `trade` - Trade executions  
  - `ticker` - Price ticker updates
  - `best_bid_ask` - Top of book changes
  - `tick_size_change` - Tick size adjustments
  - `new_market` - New market creation
  - `market_resolved` - Market resolution

### Subscription Message Format
```json
{
  "method": "SUBSCRIBE",
  "params": {
    "channels": [
      {"name": "l2_book", "token_ids": ["..."]},
      {"name": "trade", "token_ids": ["..."]}
    ]
  }
}
```

### Database Schema
```sql
-- Markets table
CREATE TABLE markets (
    token_id TEXT PRIMARY KEY,
    event_slug TEXT,
    question TEXT,
    outcomes TEXT,  -- JSON array
    created_at TIMESTAMP,
    resolved_at TIMESTAMP,
    status TEXT
);

-- Orderbook snapshots
CREATE TABLE orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT,
    timestamp TIMESTAMP,
    bids TEXT,  -- JSON array of [price, size]
    asks TEXT,  -- JSON array of [price, size]
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    midpoint REAL
);

-- Trades
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT,
    timestamp TIMESTAMP,
    price REAL,
    size REAL,
    side TEXT,  -- BUY or SELL
    trade_id TEXT UNIQUE
);

-- Ticker updates
CREATE TABLE ticker_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT,
    timestamp TIMESTAMP,
    price REAL,
    volume_24h REAL,
    change_24h REAL
);

-- Indexes for performance
CREATE INDEX idx_orderbook_token_time ON orderbook_snapshots(token_id, timestamp);
CREATE INDEX idx_trades_token_time ON trades(token_id, timestamp);
CREATE INDEX idx_ticker_token_time ON ticker_updates(token_id, timestamp);
```

## Implementation Phases

### Phase 1: WebSocket Infrastructure
- [ ] Create `websocket_client.py` with connection management
- [ ] Implement message parsing and validation
- [ ] Set up SQLite database schema
- [ ] Add subscription manager for dynamic market tracking

### Phase 2: Data Discovery
- [ ] Use Gamma API to discover active crypto markets
- [ ] Auto-subscribe to new markets as they appear
- [ ] Handle market resolutions and unsubscriptions

### Phase 3: Storage & Persistence
- [ ] Implement efficient batch inserts
- [ ] Add data compression for historical data
- [ ] Create backup/export utilities

### Phase 4: Analysis Integration
- [ ] Port existing analysis functions to work with stored data
- [ ] Add time-range queries for backtesting
- [ ] Implement walk-forward validation on historical data

### Phase 5: Monitoring & Recovery
- [ ] Add connection health monitoring
- [ ] Implement automatic reconnection with exponential backoff
- [ ] Create gap detection and backfill mechanisms

## Technical Considerations

### Connection Management
- Reconnect on disconnect with exponential backoff
- Heartbeat/ping-pong for connection health
- Queue messages during reconnection

### Data Volume
- Estimate: ~100 markets × 1 update/sec × 86400 sec/day = 8.6M records/day
- Compression needed for long-term storage
- Partitioning by date for query performance

### Memory Management
- Stream processing - don't buffer in memory
- Batch database writes (every N seconds or M records)
- Periodic vacuum/optimization of SQLite

### Error Handling
- Validate all incoming messages against schema
- Log malformed messages but don't crash
- Alert on prolonged disconnections

## Migration Path
1. Run WebSocket capture alongside existing REST-based system
2. Build up historical database over 1-2 weeks
3. Switch analysis to use stored data
4. Deprecate REST polling (keep for discovery/backfill)

## Next Steps
1. Implement basic WebSocket client
2. Test connection and subscription
3. Design and create database schema
4. Begin capturing data for target markets
