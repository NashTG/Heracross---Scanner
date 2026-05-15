"""
Spot Asset Resolver — maps Polymarket token_ids to crypto symbols.

Reads discovered_tokens.json and keyword-matches each market's question/slug/outcomes
against ASSET_PATTERNS to produce a token_id → symbol mapping for spot price polling.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

ASSET_PATTERNS: Dict[str, list] = {
    "BTC":  ["bitcoin", "btc"],
    "ETH":  ["ethereum", "eth", "ether"],
    "SOL":  ["solana", "sol"],
    "XRP":  ["ripple", "xrp"],
    "DOGE": ["dogecoin", "doge"],
    "MATIC": ["polygon", "matic"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "BNB":  ["bnb", "binance coin"],
    "ADA":  ["cardano", "ada"],
    "SUI":  ["sui"],
    "OP":   ["optimism"],
    "ARB":  ["arbitrum", "arb"],
    "PEPE": ["pepe"],
}


def _match_symbol(text: str) -> str | None:
    """Return the first matching symbol for lowercased text, or None."""
    lowered = text.lower()
    for symbol, keywords in ASSET_PATTERNS.items():
        if any(kw in lowered for kw in keywords):
            return symbol
    return None


def _build_search_text(market: Dict) -> str:
    """Concatenate all searchable fields from a market entry."""
    outcomes = market.get("outcomes", [])
    if isinstance(outcomes, list):
        outcomes_str = " ".join(str(o) for o in outcomes)
    else:
        outcomes_str = str(outcomes)
    return " ".join([
        market.get("question", ""),
        market.get("event_slug", ""),
        outcomes_str,
    ])


def resolve_assets(
    filepath: str = "discovered_tokens.json",
) -> Tuple[Dict[str, str], Set[str]]:
    """
    Load discovered_tokens.json and resolve each token_id to a crypto symbol.

    Returns:
        token_to_symbol: dict mapping token_id -> symbol (e.g. "BTC")
        symbols:         set of distinct symbols found (used to build WS streams)
    """
    path = Path(filepath)
    if not path.exists():
        logger.warning(f"discovered_tokens.json not found at {filepath}; no assets resolved")
        return {}, set()

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.error(f"Failed to load {filepath}: {exc}")
        return {}, set()

    markets = data.get("markets", [])
    token_to_symbol: Dict[str, str] = {}
    unmatched = 0

    for market in markets:
        token_id = market.get("token_id", "").strip()
        if not token_id:
            continue

        search_text = _build_search_text(market)
        symbol = _match_symbol(search_text)

        if symbol is None:
            logger.warning(
                f"No asset match for token_id={token_id[:16]}... "
                f"(text='{search_text[:80]}')"
            )
            unmatched += 1
            continue

        token_to_symbol[token_id] = symbol

    symbols: Set[str] = set(token_to_symbol.values())
    logger.info(
        f"Resolved {len(token_to_symbol)} token(s) → {len(symbols)} symbol(s): {sorted(symbols)}"
        f" ({unmatched} unmatched, skipped)"
    )
    return token_to_symbol, symbols
