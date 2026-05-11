"""
Market discovery service - finds active crypto markets and extracts token IDs.
Uses Gamma API to discover markets, then prepares subscription lists for WebSocket.
"""

import requests
import json
from typing import List, Dict, Any

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


def get_active_markets(tag_slug: str = "crypto", limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch active markets from Gamma API."""
    url = f"{GAMMA_API_BASE}/events"
    params = {
        "tag_slug": tag_slug,
        "active": "true",
        "limit": limit
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching markets: {e}")
        return []


def extract_token_ids(events: List[Dict[str, Any]]) -> List[str]:
    """Extract all CLOB token IDs from event data."""
    token_ids = []
    
    for event in events:
        markets = event.get("markets", [])
        for market in markets:
            # Parse clobTokenIds (can be JSON string or list)
            clob_tokens = market.get("clobTokenIds", [])
            if isinstance(clob_tokens, str):
                try:
                    clob_tokens = json.loads(clob_tokens)
                except json.JSONDecodeError:
                    continue
            
            if isinstance(clob_tokens, list):
                token_ids.extend(clob_tokens)
    
    return token_ids


def get_market_info(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract detailed market information for database storage."""
    markets_info = []
    
    for event in events:
        event_slug = event.get("slug", "")
        event_title = event.get("title", "")
        
        for market in event.get("markets", []):
            # Parse clobTokenIds
            clob_tokens = market.get("clobTokenIds", [])
            if isinstance(clob_tokens, str):
                try:
                    clob_tokens = json.loads(clob_tokens)
                except json.JSONDecodeError:
                    clob_tokens = []
            
            # Parse outcomes
            outcomes = market.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except json.JSONDecodeError:
                    outcomes = []
            
            for token_id in clob_tokens:
                markets_info.append({
                    "token_id": token_id,
                    "event_slug": event_slug,
                    "question": market.get("question", ""),
                    "outcomes": outcomes,
                    "created_at": market.get("createdAt", ""),
                    "endDate": market.get("endDate", ""),
                    "status": "active" if market.get("active") else "closed"
                })
    
    return markets_info


def main():
    """Discover and display active crypto markets."""
    print("Fetching active crypto markets from Gamma API...")
    events = get_active_markets(tag_slug="crypto", limit=20)
    
    if not events:
        print("No events found or API error occurred")
        return
    
    print(f"\nFound {len(events)} events")
    
    token_ids = extract_token_ids(events)
    print(f"Extracted {len(token_ids)} unique token IDs")
    
    # Show first few token IDs
    print("\nFirst 10 token IDs:")
    for tid in token_ids[:10]:
        print(f"  - {tid}")
    
    # Get detailed market info
    markets_info = get_market_info(events)
    print(f"\nDetailed info for {len(markets_info)} markets")
    
    # Show sample
    if markets_info:
        print("\nSample market info:")
        print(json.dumps(markets_info[0], indent=2))
    
    # Save to file for use with WebSocket client
    with open("discovered_tokens.json", "w") as f:
        json.dump({"token_ids": token_ids, "markets": markets_info}, f, indent=2)
    
    print(f"\nSaved to discovered_tokens.json")


if __name__ == "__main__":
    main()
