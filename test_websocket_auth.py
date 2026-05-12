#!/usr/bin/env python3
"""
Test script to verify WebSocket connection with authentication.
Make sure you have created a .env file with your API credentials.
"""

import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv()

# Check credentials
api_key = os.getenv('POLYMARKET_API_KEY')
api_secret = os.getenv('POLYMARKET_API_SECRET')
passphrase = os.getenv('POLYMARKET_PASSPHRASE')

print("=" * 60)
print("Polymarket WebSocket Connection Test")
print("=" * 60)
print()

if not all([api_key, api_secret, passphrase]):
    print("❌ ERROR: Missing API credentials!")
    print()
    print("Please create a .env file with:")
    print("  POLYMARKET_API_KEY=your_api_key")
    print("  POLYMARKET_API_SECRET=your_api_secret")
    print("  POLYMARKET_PASSPHRASE=your_passphrase")
    exit(1)

print("✓ API credentials found:")
print(f"  API Key: {api_key[:8]}...{api_key[-4:]}")
print(f"  API Secret: {api_secret[:8]}...{api_secret[-4:]}")
print(f"  Passphrase: {passphrase[:8]}...{passphrase[-4:]}")
print()

# Import and test the WebSocket client
from websocket_client import WebSocketClient, generate_auth_headers

# Test auth header generation
print("Testing authentication header generation...")
headers = generate_auth_headers()
if headers:
    print("✓ Authentication headers generated successfully:")
    for key, value in headers.items():
        if key == 'POLY_SIGNATURE':
            print(f"  {key}: {value[:20]}...{value[-20:]}")
        else:
            print(f"  {key}: {value}")
else:
    print("✗ No authentication headers generated (check credentials)")
print()

# Test WebSocket connection
async def test_connection():
    print("Attempting WebSocket connection...")
    print()
    
    # Load discovered tokens
    token_ids = []
    try:
        with open('discovered_tokens.json', 'r') as f:
            data = json.load(f)
            token_ids = data.get('token_ids', [])[:10]  # Use first 10 tokens
        print(f"✓ Loaded {len(token_ids)} token IDs from discovered_tokens.json")
    except FileNotFoundError:
        print("⚠ discovered_tokens.json not found, using sample tokens")
        token_ids = [
            "58097042028915691691707288370709969135249716122714156146991992373096973852681",
            "109553576899055322914561954174773868192997815073790549174493840159930517696797"
        ]
    
    print()
    print(f"Will subscribe to {len(token_ids)} tokens...")
    print()
    
    client = WebSocketClient(db_path="polymarket_realtime_test.db")
    
    try:
        # Try to connect
        connected = await client.connect()
        
        if not connected:
            print("❌ Connection failed!")
            print()
            print("Troubleshooting tips:")
            print("  1. Check your internet connection")
            print("  2. Verify API credentials are correct")
            print("  3. Check if Polymarket WebSocket is accessible from your network")
            print("  4. Try running from a different network/location")
            return
        
        print("✓ Successfully connected to WebSocket!")
        print()
        
        # Subscribe to channels
        print("Subscribing to channels: l2_book, trade, ticker, best_bid_ask")
        await client.subscribe(token_ids, channels=["l2_book", "trade", "ticker", "best_bid_ask"])
        print("✓ Subscription sent")
        print()
        
        # Listen for messages (timeout after 10 seconds)
        print("Listening for messages (will timeout after 10 seconds)...")
        print("-" * 60)
        
        message_count = 0
        try:
            async with asyncio.timeout(10):
                async for message in client.ws:
                    message_count += 1
                    data = json.loads(message)
                    
                    if message_count <= 5:  # Show first 5 messages
                        print(f"Message {message_count}:")
                        print(f"  Type: {data.get('type') or data.get('event') or 'unknown'}")
                        if 'token_id' in data or 'market' in data:
                            token = data.get('token_id') or data.get('market')
                            print(f"  Token: {token[:20]}...{token[-20:]}" if len(token) > 40 else f"  Token: {token}")
                        if 'price' in data:
                            print(f"  Price: {data['price']}")
                        if 'bids' in data:
                            bids = data.get('bids', [])
                            asks = data.get('asks', [])
                            print(f"  Bids: {len(bids)}, Asks: {len(asks)}")
                        print()
                    
        except asyncio.TimeoutError:
            pass
        
        print("-" * 60)
        print()
        print(f"✓ Received {message_count} messages in 10 seconds")
        print()
        
        if message_count > 0:
            print("🎉 SUCCESS! WebSocket is working correctly!")
            print()
            print("Next steps:")
            print("  1. Run the full client: python websocket_client.py")
            print("  2. Data will be stored in polymarket_realtime.db")
            print("  3. Use analyze.py to process the captured data")
        else:
            print("⚠ Connected but no messages received")
            print("  This might mean:")
            print("  - The subscribed tokens are not active")
            print("  - Authentication is required for these channels")
            print("  - Network issues preventing message delivery")
        
        # Cleanup
        client.stop()
        await client.disconnect()
        
    except Exception as e:
        print(f"❌ Error during connection test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_connection())
