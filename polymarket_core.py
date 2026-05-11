import os, re, json, time, sqlite3, statistics, math, sys
from datetime import datetime, timedelta, timezone, date as date_type
import requests
from zoneinfo import ZoneInfo

# ─── Constants ────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com"

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
INTERVALS = ["5m", "15m", "1h", "4h", "1d"]

INTERVAL_SECONDS = {
    "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}

BINANCE_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
ASSET_SLUG_FULL = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
ASSET_SLUG_SHORT = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}

ET_TZ = ZoneInfo("America/New_York")
DB_NAME = "polymarket_cache.db"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# ─── Fallback Stats ───────────────────────────────────────────────────
_fallback_stats = {"hits": 0, "misses": 0, "by_combo": {}}

# ─── Pattern Stems (slug prefixes for search) ──────────────────────────
def pattern_stems(asset: str, interval: str):
    """Return list of slug-prefix candidates for asset x interval."""
    short = ASSET_SLUG_SHORT.get(asset)
    full = ASSET_SLUG_FULL.get(asset)

    if not short or not full:
        return []

    if interval == "5m":
        return [f"{short}-updown-5m-"]
    elif interval == "15m":
        return [f"{short}-updown-15m-"]
    elif interval == "4h":
        return [f"{short}-updown-4h-"]
    elif interval == "1h":
        return [f"{full}-up-or-down-"]
    elif interval == "1d":
        return [
            f"{short}-updown-1d-",
            f"{full}-up-or-down-on-",
        ]
    return []

# ─── Input Validation ─────────────────────────────────────────────────
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def validate_date_range(start, end):
    """Validate YYYY-MM-DD strings and that start <= end. Returns (start, end)."""
    for label, val in (("start_date", start), ("end_date", end)):
        if not isinstance(val, str) or not _DATE_RE.match(val):
            raise ValueError(f"{label} must be YYYY-MM-DD, got: {val!r}")
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"{label} is not a valid date: {val!r}")
    if start > end:
        raise ValueError(f"start_date must be on or before end_date ({start} > {end})")
    return start, end

def validate_import_payload(data):
    """Validate that data is a list of dicts each containing a non-empty slug string."""
    if not isinstance(data, list):
        raise ValueError("Import payload must be a JSON array")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Item {i} is not an object")
        if not isinstance(item.get("slug"), str) or not item["slug"]:
            raise ValueError(f"Item {i} is missing a non-empty 'slug' string")
        for field in ("up_snapshots", "down_snapshots", "btc_snapshots"):
            if field in item and not isinstance(item[field], list):
                raise ValueError(f"Item {i}: '{field}' must be a list if present")
    return data

# ─── Database ─────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS markets (slug TEXT PRIMARY KEY, data TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS slug_aliases (
        derived_slug TEXT PRIMARY KEY,
        real_slug TEXT NOT NULL,
        asset TEXT,
        interval TEXT,
        window_ts INT,
        resolved_at INT
    )""")
    return conn

def cache_get(slug):
    conn = get_db()
    row = conn.execute("SELECT data FROM markets WHERE slug=?", (slug,)).fetchone()
    conn.close()
    if not row:
        return None
    m = json.loads(row[0])
    # Only return from cache if it has real data
    if m.get("error"):
        return None
    if m.get("up_snapshots") and m.get("down_snapshots"):
        return m
    return None

def cache_put(slug, data):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO markets (slug, data) VALUES (?,?)", (slug, json.dumps(data)))
    conn.commit()
    conn.close()

def cache_count():
    conn = get_db()
    c = conn.execute("SELECT COUNT(*) as cnt FROM markets").fetchone()
    conn.close()
    return c["cnt"] if c else 0

def cache_clear():
    conn = get_db()
    conn.execute("DELETE FROM markets")
    conn.commit()
    conn.close()

def alias_get(derived_slug):
    """Return the real_slug if an alias exists, else None."""
    conn = get_db()
    row = conn.execute("SELECT real_slug FROM slug_aliases WHERE derived_slug=?", (derived_slug,)).fetchone()
    conn.close()
    return row[0] if row else None

def alias_put(derived_slug, real_slug, asset=None, interval=None, window_ts=None):
    """Cache an alias: derived_slug -> real_slug with metadata."""
    conn = get_db()
    resolved_at = int(time.time())
    conn.execute("""INSERT OR REPLACE INTO slug_aliases
                    (derived_slug, real_slug, asset, interval, window_ts, resolved_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (derived_slug, real_slug, asset, interval, window_ts, resolved_at))
    conn.commit()
    conn.close()

# ─── HTTP with retries ────────────────────────────────────────────────
def fetch_json(url, params=None):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 408, 502, 503) and attempt < MAX_RETRIES:
                time.sleep(2.0 * attempt)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_err = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(2.0 * attempt)
                continue
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {last_err}")

# ─── Slug Generation ──────────────────────────────────────────────────
def slug_from_ts(ts, asset="BTC", interval="5m"):
    full = ASSET_SLUG_FULL.get(asset, asset.lower())
    short = ASSET_SLUG_SHORT.get(asset, asset.lower())

    if interval == "1h":
        dt_et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET_TZ)
        hour = dt_et.strftime("%I").lstrip("0")
        ap = dt_et.strftime("%p").lower()
        return f"{full}-up-or-down-{dt_et.strftime('%B').lower()}-{dt_et.day}-{dt_et.year}-{hour}{ap}-et"

    if interval == "1d":
        # 1d slug uses the resolution date (noon ET end date)
        end_ts = ts + INTERVAL_SECONDS["1d"]
        dt_et = datetime.fromtimestamp(end_ts, tz=timezone.utc).astimezone(ET_TZ)
        return f"{full}-up-or-down-on-{dt_et.strftime('%B').lower()}-{dt_et.day}-{dt_et.year}"

    # 5m, 15m, 4h — all use {asset}-updown-{interval}-{timestamp}
    return f"{short}-updown-{interval}-{ts}"

def et_midnight_ts(d):
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    return int(datetime(d.year, d.month, d.day, tzinfo=ET_TZ).timestamp())

def generate_range_timestamps(start_str, end_str, interval="5m"):
    sec = INTERVAL_SECONDS[interval]
    start_day = datetime.strptime(start_str, "%Y-%m-%d").date()
    end_day = datetime.strptime(end_str, "%Y-%m-%d").date()
    timestamps = []
    current = start_day

    while current <= end_day:
        if interval == "1d":
            noon_ts = et_midnight_ts(current) + 43200
            timestamps.append(noon_ts - sec)
        elif interval == "4h":
            # 4h windows are aligned to UTC epoch (divisible by 14400)
            day_start_utc = int(datetime(current.year, current.month, current.day, tzinfo=timezone.utc).timestamp())
            # Align to nearest 4h boundary at or after day start
            aligned = day_start_utc - (day_start_utc % sec)
            if aligned < day_start_utc:
                aligned += sec
            day_end_utc = day_start_utc + 86400
            while aligned < day_end_utc:
                timestamps.append(aligned)
                aligned += sec
        elif interval == "1h":
            # 1h windows align to whole hours ET
            day_start = et_midnight_ts(current)
            for i in range(24):
                timestamps.append(day_start + i * 3600)
        else:
            # 5m, 15m — aligned to ET midnight
            day_start = et_midnight_ts(current)
            steps = 86400 // sec
            for i in range(steps):
                timestamps.append(day_start + i * sec)

        current += timedelta(days=1)

    return sorted(list(set(timestamps)))

# ─── API Calls ─────────────────────────────────────────────────────────
def resolve_slug(slug):
    data = fetch_json(f"{GAMMA_API}/events", {"slug": slug})
    if not isinstance(data, list) or len(data) == 0:
        return None
    event = data[0]
    markets = event.get("markets", [])
    if not markets:
        return None
    m = markets[0]
    token_ids_raw = m.get("clobTokenIds", "[]")
    token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else list(token_ids_raw)
    outcomes_raw = m.get("outcomes", "[]")
    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else list(outcomes_raw)
    return {
        "question": m.get("question", slug),
        "token_ids": token_ids,
        "outcomes": outcomes,
        "slug": m.get("slug", slug),
        "event_metadata": event.get("eventMetadata", {}),
    }

def fetch_price_history(token_id, start_ts, end_ts):
    data = fetch_json(f"{CLOB_API}/prices-history", {
        "market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": "1",
    })
    return data.get("history", []) if isinstance(data, dict) else []

def fetch_asset_candles(asset, interval, start_ts, end_ts):
    mapping = {"5m": "1m", "15m": "1m", "1h": "5m", "4h": "5m", "1d": "30m"}
    bin_int = mapping.get(interval, "1m")
    params = {
        "symbol": BINANCE_SYMBOL[asset], "interval": bin_int,
        "startTime": int(start_ts) * 1000, "endTime": int(end_ts) * 1000, "limit": 1000,
    }
    data = fetch_json(f"{BINANCE_API}/api/v3/klines", params)
    if not isinstance(data, list):
        return []
    candles = [{"t": int(k[0]) // 1000, "open": float(k[1]), "close": float(k[4])} for k in data]
    if not candles:
        return []
    base = candles[0]["open"]
    for c in candles:
        c["delta"] = c["close"] - base
    return candles

# ─── Full Market Fetch ─────────────────────────────────────────────────
def fetch_market(slug, asset="BTC", interval="5m", start_ts=None):
    cached = cache_get(slug)
    if cached:
        return cached

    sec = INTERVAL_SECONDS.get(interval, 300)

    # Derive start_ts from slug if not provided
    if start_ts is None:
        ts_match = re.search(r"(\d{10})$", slug)
        if ts_match:
            start_ts = int(ts_match.group(1))
        else:
            return {"slug": slug, "error": "Cannot derive timestamp from slug"}

    end_ts = start_ts + sec
    result = {"slug": slug, "window_start_ts": start_ts, "window_end_ts": end_ts, "error": None}

    try:
        info = resolve_slug(slug)
        if not info:
            result["error"] = "No Gamma event"
            return result
        result["question"] = info["question"]
        result["outcomes"] = info["outcomes"]
        token_ids = info["token_ids"]
        if len(token_ids) < 2:
            result["error"] = f"Only {len(token_ids)} tokens"
            return result

        up_idx, down_idx = (1, 0) if (info["outcomes"] and "down" in info["outcomes"][0].lower()) else (0, 1)
        result["up_token_id"] = token_ids[up_idx]
        result["down_token_id"] = token_ids[down_idx]

        up_snap = fetch_price_history(token_ids[up_idx], start_ts, end_ts)
        down_snap = fetch_price_history(token_ids[down_idx], start_ts, end_ts)
        result["up_snapshots"] = up_snap
        result["down_snapshots"] = down_snap

        candles = fetch_asset_candles(asset, interval, start_ts, end_ts)
        result["btc_snapshots"] = candles

        # Only cache if we got real data
        if up_snap and down_snap:
            cache_put(slug, result)
    except (RuntimeError, requests.RequestException) as e:
        print(f"fetch_market error for {slug}: {e!r}", file=sys.stderr)
        result["error"] = str(e)

    return result

def fetch_range(start_str, end_str, asset="BTC", interval="5m", progress_cb=None):
    timestamps = generate_range_timestamps(start_str, end_str, interval)
    results = []
    total = len(timestamps)
    for i, ts in enumerate(timestamps):
        slug = slug_from_ts(ts, asset, interval)
        m = fetch_market(slug, asset, interval, start_ts=ts)
        results.append(m)
        time.sleep(0.5)
        if progress_cb:
            progress_cb(i + 1, total)
        else:
            status = "✓" if not m.get("error") else f"✗ {m['error']}"
            print(f"  [{i+1}/{total}] {slug} {status}")
    return results

# ─── Analysis Helpers ──────────────────────────────────────────────────
def calc_net_pnl(entry_cents, exit_cents):
    """Net P&L after Polymarket 7.2% taker + Polygon 1% on buy and sell."""
    return exit_cents * 0.99 - entry_cents * 1.082

def closing_outcome(m):
    up = m.get("up_snapshots", [])
    down = m.get("down_snapshots", [])
    if not up or not down:
        return None
    return "UP" if up[-1]["p"] > down[-1]["p"] else "DOWN"

def price_at_minute(series, minute):
    idx = min(max(minute - 1, 0), len(series) - 1)
    return series[idx]["p"] if idx < len(series) else None

def btc_delta_at_minute(m, minute):
    snaps = m.get("btc_snapshots", [])
    if not snaps:
        return None
    idx = min(max(minute - 1, 0), len(snaps) - 1)
    return snaps[idx].get("delta", 0)

def _get_usable(markets):
    return [m for m in markets
            if not m.get("error")
            and m.get("up_snapshots")
            and m.get("down_snapshots")
            and closing_outcome(m)]

def _range_cents(points):
    if not points:
        return 0
    prices = [p["p"] * 100 for p in points]
    return max(prices) - min(prices)

# ─── Core Analysis ─────────────────────────────────────────────────────
def analyze(markets, asset="BTC"):
    usable = _get_usable(markets)
    if not usable:
        return None

    up_closes = sum(1 for m in usable if closing_outcome(m) == "UP")
    down_closes = len(usable) - up_closes

    # Correlation
    pairs = []
    for m in usable:
        d = btc_delta_at_minute(m, 1)
        p = price_at_minute(m["up_snapshots"], 1)
        if d is not None and p is not None:
            pairs.append((d, p * 100))
    corr = None
    if len(pairs) >= 5:
        xs, ys = zip(*pairs)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        corr = num / (sx * sy) if sx and sy else None

    avg_up_range = statistics.mean([_range_cents(m["up_snapshots"]) for m in usable]) if usable else 0
    avg_down_range = statistics.mean([_range_cents(m["down_snapshots"]) for m in usable]) if usable else 0
    final_moves = [abs(btc_delta_at_minute(m, len(m.get("btc_snapshots", []))) or 0) for m in usable]
    avg_abs_move = statistics.mean(final_moves) if final_moves else 0

    # Time-of-day segments
    tod_segments = []
    for h_start in range(0, 24, 4):
        seg = [m for m in usable
               if datetime.fromtimestamp(m["window_start_ts"], tz=timezone.utc).astimezone(ET_TZ).hour in range(h_start, h_start + 4)]
        seg_up = sum(1 for m in seg if closing_outcome(m) == "UP")
        tod_segments.append({
            "range": f"{h_start:02d}:00–{h_start+4:02d}:00 ET",
            "markets": len(seg),
            "up": seg_up,
            "down": len(seg) - seg_up,
            "up_pct": (seg_up / len(seg) * 100) if seg else 0,
        })

    # ── Outcome strategies ──
    outcome_insights = []
    for minute in [1, 2, 3]:
        for threshold in [25, 50, 100, 200]:
            for direction, side, cmp in [
                ("up", "UP", lambda d, t: d >= t),
                ("down", "DOWN", lambda d, t: d <= -t),
            ]:
                qualifying = []
                for m in usable:
                    d = btc_delta_at_minute(m, minute)
                    if d is None or not cmp(d, threshold):
                        continue
                    series = m["up_snapshots"] if side == "UP" else m["down_snapshots"]
                    entry = price_at_minute(series, minute)
                    if entry is None:
                        continue
                    entry_c = entry * 100
                    won = closing_outcome(m) == side
                    exit_c = 100 if won else 0
                    net = calc_net_pnl(entry_c, exit_c)
                    qualifying.append({"entry": entry_c, "won": won, "net": net, "btc_d": d, "slug": m["slug"]})

                if len(qualifying) >= 10:
                    wins = sum(1 for q in qualifying if q["won"])
                    conf = wins / len(qualifying)
                    avg_entry = statistics.mean([q["entry"] for q in qualifying])
                    avg_net = statistics.mean([q["net"] for q in qualifying])
                    if conf >= 0.7:
                        sign = "+" if direction == "up" else "-"
                        samples = [q for q in qualifying if q["won"]][:3]
                        outcome_insights.append({
                            "title": f"{asset} {sign}${threshold} at min {minute} → BUY {side}",
                            "confidence": conf,
                            "support": len(qualifying),
                            "avg_entry": avg_entry,
                            "avg_net_pnl": avg_net,
                            "bottomline": f"If {asset} is {'up' if direction == 'up' else 'down'} ${threshold}+ by minute {minute}, buy {side} at avg {avg_entry:.0f}c → closes {side} {conf*100:.0f}% of the time, net P&L {avg_net:.1f}c.",
                            "samples": [{"slug": s["slug"], "entry": s["entry"], "btc_d": s["btc_d"], "net": s["net"]} for s in samples],
                        })

    # ── Volatility strategies ──
    profit_insights = []
    for side_label in ["UP", "DOWN"]:
        for minute in [1, 2, 3]:
            for max_entry in [20, 35, 50]:
                qualifying = []
                for m in usable:
                    series = m["up_snapshots"] if side_label == "UP" else m["down_snapshots"]
                    if len(series) < minute:
                        continue
                    idx = min(minute - 1, len(series) - 1)
                    entry = series[idx]["p"]
                    entry_c = entry * 100
                    if entry_c > max_entry:
                        continue
                    future = series[idx + 1:]
                    if not future:
                        continue
                    best_exit = max(p["p"] for p in future)
                    net = calc_net_pnl(entry_c, best_exit * 100)
                    qualifying.append({"entry": entry_c, "exit": best_exit * 100, "net": net, "slug": m["slug"]})

                if len(qualifying) >= 10:
                    profitable = [q for q in qualifying if q["net"] > 0]
                    conf = len(profitable) / len(qualifying)
                    avg_net = statistics.mean([q["net"] for q in qualifying])
                    avg_entry = statistics.mean([q["entry"] for q in qualifying])
                    if conf >= 0.7 and avg_net > 0:
                        samples = profitable[:3]
                        profit_insights.append({
                            "title": f"{side_label} ≤ {max_entry}c at min {minute}, sell rally",
                            "confidence": conf,
                            "support": len(qualifying),
                            "avg_entry": avg_entry,
                            "avg_net_pnl": avg_net,
                            "bottomline": f"If {side_label} is {max_entry}c or cheaper at minute {minute}, buy and sell the rally → net profit {conf*100:.0f}% of the time, avg P&L {avg_net:.1f}c.",
                            "samples": [{"slug": s["slug"], "entry": s["entry"], "exit": s["exit"], "net": s["net"]} for s in samples],
                        })

    outcome_insights.sort(key=lambda x: (-x["confidence"], -x["avg_net_pnl"]))
    profit_insights.sort(key=lambda x: (-x["confidence"], -x["avg_net_pnl"]))

    return {
        "markets": len(markets),
        "usable": len(usable),
        "up_closes": up_closes,
        "down_closes": down_closes,
        "up_pct": up_closes / len(usable) * 100 if usable else 0,
        "avg_abs_move": avg_abs_move,
        "avg_up_range": avg_up_range,
        "avg_down_range": avg_down_range,
        "correlation": corr,
        "tod_segments": tod_segments,
        "outcome_insights": outcome_insights[:8],
        "profit_insights": profit_insights[:8],
    }

# ─── Out-of-Sample Validation ─────────────────────────────────────────
def validate_oos(markets, asset="BTC"):
    usable = sorted(_get_usable(markets), key=lambda m: m["window_start_ts"])
    mid = len(usable) // 2
    if mid < 10:
        return None
    train = analyze(usable[:mid], asset)
    val = analyze(usable[mid:], asset)
    return {
        "train_size": mid,
        "val_size": len(usable) - mid,
        "train": train,
        "validation": val,
    }

# ─── Strategy Simulator ───────────────────────────────────────────────
def simulate_strategy(markets, insight, bet_size=100):
    usable = _get_usable(markets)
    is_outcome = "→ BUY" in insight["title"]
    is_up_side = insight["title"].endswith("BUY UP") or insight["title"].startswith("UP")
    side = "UP" if is_up_side else "DOWN"

    min_match = re.search(r"min (\d)", insight["title"])
    minute = int(min_match.group(1)) if min_match else 1
    thresh_match = re.search(r"\$(\d+)", insight["title"])
    threshold = int(thresh_match.group(1)) if thresh_match else 0
    entry_cap_match = re.search(r"≤\s*(\d+)c", insight["title"])
    entry_cap = int(entry_cap_match.group(1)) if entry_cap_match else 999

    wins, losses, total_pnl = 0, 0, 0.0
    peak, max_dd, worst = 0.0, 0.0, 0.0
    bankroll = 1000.0
    peak_br = bankroll
    trades = []

    for m in usable:
        series = m["up_snapshots"] if side == "UP" else m["down_snapshots"]
        if len(series) < minute:
            continue
        idx = min(minute - 1, len(series) - 1)
        entry_c = series[idx]["p"] * 100
        btc_d = btc_delta_at_minute(m, minute) or 0

        if is_outcome:
            is_positive = "+" in insight["title"].split("→")[0]
            if is_positive and btc_d < threshold:
                continue
            if not is_positive and btc_d > -threshold:
                continue
        else:
            if entry_c > entry_cap:
                continue

        if is_outcome:
            exit_c = 100 if closing_outcome(m) == side else 0
        else:
            future = series[idx + 1:]
            exit_c = max((p["p"] for p in future), default=series[idx]["p"]) * 100

        net_per_cent = calc_net_pnl(entry_c, exit_c)
        trade_pnl = (net_per_cent / entry_c) * bet_size if entry_c > 0 else 0

        if trade_pnl > 0:
            wins += 1
        else:
            losses += 1
        total_pnl += trade_pnl
        bankroll += trade_pnl
        if bankroll > peak_br:
            peak_br = bankroll
        dd = peak_br - bankroll
        if dd > max_dd:
            max_dd = dd
        if trade_pnl < worst:
            worst = trade_pnl
        trades.append(trade_pnl)

    total = wins + losses
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "hit_rate": (wins / total * 100) if total else 0,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / 1000) * 100,
        "max_drawdown": max_dd,
        "worst_loss": worst,
        "avg_pnl": statistics.mean(trades) if trades else 0,
        "final_bankroll": bankroll,
    }

# ─── Snapshots ─────────────────────────────────────────────────────────
SNAPSHOTS_FILE = "analysis_snapshots.json"

def save_snapshot(meta):
    snapshots = load_snapshots()
    meta["id"] = f"{int(time.time())}-{len(snapshots)}"
    meta["timestamp"] = time.time()
    snapshots.insert(0, meta)
    with open(SNAPSHOTS_FILE, "w") as f:
        json.dump(snapshots, f)
    return meta

def load_snapshots():
    if os.path.exists(SNAPSHOTS_FILE):
        with open(SNAPSHOTS_FILE) as f:
            return json.load(f)
    return []

# ─── Export / Import ───────────────────────────────────────────────────
def export_markets(markets):
    return json.dumps(markets, indent=2)

def import_markets(data_str):
    return json.loads(data_str)

# ─── Diagnostics ───────────────────────────────────────────────────────
def run_diagnostics():
    results = []
    for asset in ASSETS:
        for interval in INTERVALS:
            # Pick a known-good aligned timestamp for each interval type
            if interval == "5m":
                ts = 1776960000  # Apr 17 2026 12:00pm ET, divisible by 300
            elif interval == "15m":
                ts = 1776960000  # also divisible by 900
            elif interval == "1h":
                ts = 1776960000  # also divisible by 3600
            elif interval == "4h":
                ts = 1776960000  # 1776960000 % 14400 = 0, aligned!
            elif interval == "1d":
                ts = 1776916800  # midnight April 17 ET ≈ noon-noon window
            else:
                ts = 1776960000

            slug = slug_from_ts(ts, asset, interval)
            ok, err = True, ""
            try:
                info = resolve_slug(slug)
                if not info:
                    ok, err = False, "No Gamma event"
                else:
                    if len(info["token_ids"]) < 2:
                        ok, err = False, f"Only {len(info['token_ids'])} tokens"
                    else:
                        sec = INTERVAL_SECONDS[interval]
                        up_snap = fetch_price_history(info["token_ids"][0], ts, ts + sec)
                        down_snap = fetch_price_history(info["token_ids"][1], ts, ts + sec)
                        if not up_snap and not down_snap:
                            ok, err = False, "No price history"
                        elif not up_snap:
                            ok, err = False, "No UP price history"
                        elif not down_snap:
                            ok, err = False, "No DOWN price history"
            except RuntimeError as e:
                ok, err = False, str(e)[:120]
            except Exception as e:
                ok, err = False, str(e)[:120]

            results.append({"asset": asset, "interval": interval, "slug": slug, "ok": ok, "error": err})
            time.sleep(0.5)  # rate limit
    return results
