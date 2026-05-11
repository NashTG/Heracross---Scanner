import asyncio
import aiohttp
import ssl
import certifi
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple
from collections import defaultdict

SAMPLE_SLUGS = [
    "btc-updown-5m-1777937400",
    "eth-updown-15m-1777937400",
    "bitcoin-up-or-down-may-4-2026-7pm-et",
    "xrp-updown-4h-1777924800",
    "bitcoin-up-or-down-on-may-4",
    "solana-up-or-down-on-may-5-2026",
]

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
SWEEP_COMBOS = [(asset, tf) for asset in ASSETS for tf in TIMEFRAMES]

ASSET_KEYWORDS = {
    "BTC":  ["btc", "bitcoin"],
    "ETH":  ["eth", "ethereum"],
    "XRP":  ["xrp", "ripple"],
    "SOL":  ["sol", "solana"],
}

ASSET_SHORT_NAMES = {
    "BTC": "btc",
    "ETH": "eth",
    "SOL": "sol",
    "XRP": "xrp",
}

ASSET_FULL_NAMES = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
}

TIMEFRAME_KEYWORDS = {
    "1m":  ["1m"],
    "5m":  ["5m"],
    "15m": ["15m"],
    "30m": ["30m"],
    "1h":  ["1h"],
    "4h":  ["4h"],
    "1d":  ["1d", "24h"],
    "7d":  ["7d", "1w"],
    "30d": ["30d", "1mo"],
}

DATE_MARKERS = ["up-or-down-may", "up-or-down-on", "up-or-down-april",
                "up-or-down-june", "up-or-down-july", "up-or-down-aug",
                "up-or-down-sep",  "up-or-down-oct",  "up-or-down-nov",
                "up-or-down-dec",  "up-or-down-jan",  "up-or-down-feb",
                "up-or-down-mar"]


def pattern_stems(asset: str, tf: str) -> List[str]:
    """Return all slug-prefix candidates for an assetxtimeframe combo."""
    short = ASSET_SHORT_NAMES.get(asset)
    full = ASSET_FULL_NAMES.get(asset)

    if not short or not full:
        return []

    if tf == "5m":
        return [f"{short}-updown-5m-"]
    elif tf == "15m":
        return [f"{short}-updown-15m-"]
    elif tf == "4h":
        return [f"{short}-updown-4h-"]
    elif tf == "1h":
        return [f"{full}-up-or-down-"]
    elif tf == "1d":
        return [
            f"{short}-updown-1d-",
            f"{full}-up-or-down-on-",
        ]
    return []

class PolymarketSlugExplorer:

    BASE       = "https://gamma-api.polymarket.com"
    SSL_CTX    = ssl.create_default_context(cafile=certifi.where())

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self):
        connector = aiohttp.TCPConnector(ssl=self.SSL_CTX)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"Accept": "application/json"},
        )

    async def close(self):
        if self.session:
            await self.session.close()

    @staticmethod
    def parse_slug(slug: str) -> dict:
        """Return {asset, timeframe, slug_type} from a slug string."""
        s = slug.lower()

        asset = next(
            (a for a, kws in ASSET_KEYWORDS.items() if any(s.startswith(kw) for kw in kws)),
            None
        )

        timeframe = next(
            (tf for tf, kws in TIMEFRAME_KEYWORDS.items()
             if any(f"-{kw}-" in s or s.endswith(f"-{kw}") for kw in kws)),
            None
        )

        if not timeframe and any(marker in s for marker in DATE_MARKERS):
            timeframe = "1d"

        slug_type = "structured" if "-updown-" in s else "date-based"

        return {"asset": asset, "timeframe": timeframe, "slug_type": slug_type}

    async def fetch_market_by_slug(self, slug: str) -> Optional[dict]:
        """Fetch a single market from gamma-api using its slug."""
        url = f"{self.BASE}/markets"
        params = {"slug": slug}

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and data:
                        return data[0]
                    if isinstance(data, dict):
                        return data
                else:
                    print(f"   ⚠️  {slug}: HTTP {resp.status}")
        except Exception as e:
            print(f"   ❌ {slug}: {e}")

        return None

    async def fetch_all_markets_for_pattern(self, asset: str, timeframe: str,
                                             limit: int = 1000) -> list:
        """
        Fetch every market whose slug matches patterns for assetxtimeframe combo.
        Internally calls pattern_stems() to get all relevant slug-prefix candidates.
        """
        url = f"{self.BASE}/markets"
        patterns = pattern_stems(asset, timeframe)

        if not patterns:
            return []

        results = []

        for pattern in patterns:
            offset = 0
            while True:
                params = {
                    "slug_contains": pattern,
                    "limit": limit,
                    "offset": offset,
                }
                try:
                    async with self.session.get(url, params=params) as resp:
                        if resp.status != 200:
                            break
                        data = await resp.json()
                        batch = data if isinstance(data, list) else data.get("data", [])
                        if not batch:
                            break

                        # Deduplicate by condition_id
                        for m in batch:
                            cid = m.get("condition_id") or m.get("conditionId") or m.get("id")
                            if cid and not any(r.get("condition_id") == cid or r.get("conditionId") == cid or r.get("id") == cid for r in results):
                                results.append(m)

                        if len(batch) < limit:
                            break
                        offset += limit
                except Exception as e:
                    print(f"   ❌ fetch error ({asset}/{timeframe} pattern '{pattern}'): {e}")
                    break

        return results

    @staticmethod
    def extract_created(market: dict) -> Optional[datetime]:
        for field in ["createdAt", "created_at", "startDate", "start_date", "createTime"]:
            raw = market.get(field)
            if raw:
                for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(str(raw)[:26], fmt)
                    except ValueError:
                        continue
                try:
                    return datetime.fromtimestamp(float(raw), tz=timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
        return None

    @staticmethod
    def extract_tokens(market: dict) -> list:
        """Extract tokens, handling multiple formats (list of dicts, list of strings, dict)."""
        tokens_raw = market.get("tokens", market.get("outcomes", []))
        
        if not tokens_raw:
            return []
        
        result = []
        
        # Case 1: List of dicts
        if isinstance(tokens_raw, list):
            for i, t in enumerate(tokens_raw):
                if isinstance(t, dict):
                    # Token is a dictionary
                    result.append({
                        "outcome": t.get("outcome", t.get("name", t.get("token", f"Token {i+1}"))),
                        "token_id": t.get("token_id", t.get("id", t.get("tokenId", "N/A"))),
                    })
                elif isinstance(t, str):
                    # Token is a string (just token ID)
                    result.append({
                        "outcome": f"Token {i+1}",
                        "token_id": t,
                    })
        
        # Case 2: Dict of token IDs
        elif isinstance(tokens_raw, dict):
            for outcome, token_id in tokens_raw.items():
                result.append({
                    "outcome": outcome,
                    "token_id": token_id,
                })
        
        return result

    async def discover_oldest_per_pattern(self, quiet: bool = False) -> Dict:
        """
        Iterate all 20 SWEEP_COMBOS and find oldest market for each.
        Returns {asset: {tf: entry_or_None}} with missing list.
        """
        if not quiet:
            print("\n" + "="*80)
            print("STEP B - Querying all markets per asset x timeframe combo")
            print("="*80)

        results = defaultdict(dict)
        missing = []

        for asset, tf in SWEEP_COMBOS:
            if not quiet:
                print(f"\n  [{asset} x {tf}] fetching...", end=" ", flush=True)

            entry = await self.find_oldest_for_combo(asset, tf)

            if entry:
                results[asset][tf] = entry
                if not quiet:
                    date_s = entry["created_at"].strftime("%Y-%m-%d") if entry["created_at"] else "unknown"
                    print(f"OK oldest: {date_s}  ({entry['total_found']} markets found)")
            else:
                missing.append([asset, tf])
                if not quiet:
                    print(f"FAIL no markets returned")

        if not quiet:
            print(f"\n  Missing combos: {missing if missing else 'none'}\n")

        return {"results": dict(results), "missing": missing}

    async def discover_from_sample_slugs(self, slugs: list) -> set:
        """Parse sample slugs to find which assetxtf combos exist."""
        print("\n" + "="*80)
        print(" STEP A - Analysing sample slugs")
        print("="*80)

        combos = set()

        for slug in slugs:
            meta = self.parse_slug(slug)
            asset, tf = meta["asset"], meta["timeframe"]
            symbol = "✅" if asset and tf else "⚠️ "
            print(f"  {symbol} {slug}")
            print(f"       → asset={asset}  timeframe={tf}  type={meta['slug_type']}")
            if asset and tf:
                combos.add((asset, tf))

        print(f"\n  Unique assetxtimeframe combos detected: {sorted(combos)}\n")
        return combos

    async def find_oldest_for_combo(self, asset: str, tf: str) -> Optional[dict]:
        """Fetch all markets for assetxtf and return the oldest."""
        markets = await self.fetch_all_markets_for_pattern(asset, tf)

        if not markets:
            return None

        dated = []
        for m in markets:
            created = self.extract_created(m)
            dated.append((created, m))

        # Sort: markets with known dates first (ascending), undated last
        dated.sort(key=lambda x: (x[0] is None, x[0] or datetime.max))

        oldest_dt, oldest_m = dated[0]

        return {
            "question":     oldest_m.get("question", oldest_m.get("title", "N/A")),
            "slug":         oldest_m.get("slug", "N/A"),
            "condition_id": oldest_m.get("condition_id", oldest_m.get("conditionId",
                            oldest_m.get("id", "N/A"))),
            "created_at":   oldest_dt,
            "active":       oldest_m.get("active", oldest_m.get("closed", "?")),
            "total_found":  len(markets),
            "tokens":       self.extract_tokens(oldest_m),
        }

    def write_json(self, data: dict, path: str):
        """Write discover_oldest_per_pattern results to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "combos_swept": len(SWEEP_COMBOS),
            "combos_found": sum(
                1 for asset_dict in data["results"].values()
                for _ in asset_dict.values()
            ),
            "results": {},
            "missing": data["missing"],
        }

        for asset in ASSETS:
            output["results"][asset] = {}
            asset_dict = data["results"].get(asset, {})
            for tf in TIMEFRAMES:
                if tf in asset_dict:
                    entry = asset_dict[tf]
                    output["results"][asset][tf] = {
                        "found": True,
                        "slug": entry["slug"],
                        "created_at": entry["created_at"].isoformat() if entry["created_at"] else None,
                        "condition_id": entry["condition_id"],
                        "total_found": entry["total_found"],
                    }
                else:
                    output["results"][asset][tf] = {"found": False}

        with open(p, "w") as f:
            json.dump(output, f, indent=2)

    def write_markdown(self, data: dict, path: str):
        """Write discover_oldest_per_pattern results to markdown table."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        lines = ["# Oldest Markets per Asset x Timeframe\n"]
        lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")
        lines.append(f"**Combos swept:** {len(SWEEP_COMBOS)}")
        lines.append(f"**Combos found:** {sum(1 for asset_dict in data['results'].values() for _ in asset_dict.values())}\n")

        # Build table
        lines.append("| Timeframe |" + " | ".join(ASSETS) + " |")
        lines.append("|" + "---|" * (len(ASSETS) + 1))

        for tf in TIMEFRAMES:
            row = f"| {tf} |"
            for asset in ASSETS:
                entry = data["results"].get(asset, {}).get(tf)
                if entry:
                    date_s = entry["created_at"].strftime("%Y-%m-%d") if entry["created_at"] else "?"
                    cell = f"{date_s} ({entry['total_found']} mkts)"
                else:
                    cell = "-"
                row += f" {cell} |"
            lines.append(row)

        # Details section
        lines.append("\n## Details\n")
        for asset in ASSETS:
            asset_dict = data["results"].get(asset, {})
            if not asset_dict:
                continue
            lines.append(f"### {asset}\n")
            for tf in TIMEFRAMES:
                if tf in asset_dict:
                    entry = asset_dict[tf]
                    lines.append(f"**{tf}**")
                    lines.append(f"- Slug: `{entry['slug']}`")
                    lines.append(f"- Created: {entry['created_at'].isoformat() if entry['created_at'] else '?'}")
                    lines.append(f"- Condition ID: {entry['condition_id']}")
                    lines.append(f"- Total found: {entry['total_found']}")
                    lines.append("")

        with open(p, "w") as f:
            f.write("\n".join(lines))

    def print_results(self, results: dict, combos: set):
        """Pretty-print the discovery results."""

        ASSETS = ["BTC", "ETH", "XRP", "SOL"]
        TFS    = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "7d", "30d"]

        print("\n" + "="*120)
        print("  OLDEST MARKET PER ASSET x TIMEFRAME")
        print("="*120)

        # Summary table
        col = 26
        header = f"  {'TF':<6}" + "".join(f"{a:<{col}}" for a in ASSETS)
        print(f"\n{header}")
        print(f"  {'──'*60}")

        for tf in TFS:
            row = f"  {tf:<6}"
            for asset in ASSETS:
                entry = results.get(asset, {}).get(tf)
                if entry:
                    date_s = entry["created_at"].strftime("%Y-%m-%d") if entry["created_at"] else "?"
                    cell = f"✅ {date_s} ({entry['total_found']} mkts)"
                elif (asset, tf) in combos:
                    cell = " queried / none"
                else:
                    cell = "-"
                row += f"{cell:<{col}}"
            print(row)

        # Detailed breakdown
        print("\n\n" + "="*120)
        print("  DETAILS")
        print("="*120)

        for asset in ASSETS:
            if asset not in results:
                continue
            print(f"\n  🪙  {asset}")
            print(f"  {'─'*116}")

            for tf in TFS:
                entry = results[asset].get(tf)
                if not entry:
                    continue

                print(f"\n    ⏱  {tf}")
                print(f"       Question   : {entry['question'][:90]}")
                print(f"       Slug       : {entry['slug']}")
                print(f"       Condition  : {entry['condition_id']}")
                print(f"       Created    : {entry['created_at']}")
                print(f"       Active     : {entry['active']}")
                print(f"       Total mkts : {entry['total_found']}")
                if entry["tokens"]:
                    print(f"       Tokens     :")
                    for t in entry["tokens"]:
                        print(f"          • {t['outcome']:12s} → {t['token_id']}")

    async def run_sample_mode(self, slugs: list):
        """Legacy sample-slug discovery mode."""
        # A – Parse slugs → discover combos
        combos = await self.discover_from_sample_slugs(slugs)

        # B – For each combo, fetch all markets and find oldest
        print("="*80)
        print(" STEP B - Querying all markets per assetxtimeframe combo")
        print("="*80)

        results = defaultdict(dict)

        for asset, tf in sorted(combos):
            print(f"\n  [{asset} x {tf}] fetching...", end=" ", flush=True)
            entry = await self.find_oldest_for_combo(asset, tf)

            if entry:
                results[asset][tf] = entry
                date_s = entry["created_at"].strftime("%Y-%m-%d") if entry["created_at"] else "unknown"
                print(f"✅  oldest: {date_s}  ({entry['total_found']} markets found)")
            else:
                print(f"❌  no markets returned")

        # C – Print results
        self.print_results(results, combos)

    async def run_sweep_mode(self, out_json: str, out_md: str, quiet: bool = False):
        """Full Cartesian sweep mode (20 combos)."""
        data = await self.discover_oldest_per_pattern(quiet=quiet)

        if not quiet:
            print(f"\n Writing JSON to {out_json}...")
        self.write_json(data, out_json)

        if not quiet:
            print(f" Writing Markdown to {out_md}...")
        self.write_markdown(data, out_md)

        if not quiet:
            print(f"✅ Complete. Outputs:")
            print(f"   - {out_json}")
            print(f"   - {out_md}\n")

    async def run(self, mode: str = "sweep", slugs: Optional[list] = None,
                  out_json: Optional[str] = None, out_md: Optional[str] = None,
                  quiet: bool = False):
        await self.init()

        try:
            if mode == "sample":
                if not slugs:
                    slugs = SAMPLE_SLUGS
                await self.run_sample_mode(slugs)
            elif mode == "sweep":
                if not out_json:
                    out_json = ".planning/research/oldest-markets.json"
                if not out_md:
                    out_md = ".planning/research/oldest-markets.md"
                await self.run_sweep_mode(out_json, out_md, quiet=quiet)
        finally:
            await self.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Buscador: Polymarket Up/Down market discovery",
    )
    parser.add_argument(
        "--mode",
        choices=["sweep", "sample"],
        default="sweep",
        help="sweep: full 20-combo Cartesian discovery (default); sample: legacy sample-slug mode",
    )
    parser.add_argument(
        "--out-json",
        default=".planning/research/oldest-markets.json",
        help="Output path for JSON results (sweep mode only)",
    )
    parser.add_argument(
        "--out-md",
        default=".planning/research/oldest-markets.md",
        help="Output path for Markdown results (sweep mode only)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-combo progress prints",
    )

    args = parser.parse_args()

    explorer = PolymarketSlugExplorer()
    asyncio.run(
        explorer.run(
            mode=args.mode,
            slugs=SAMPLE_SLUGS if args.mode == "sample" else None,
            out_json=args.out_json,
            out_md=args.out_md,
            quiet=args.quiet,
        )
    )