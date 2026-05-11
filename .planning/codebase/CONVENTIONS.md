# CONVENTIONS

## Style
- **PEP 8 mostly**, but with **multi-import lines** (`import os, re, json, time, sqlite3, statistics, math`) — common throughout `polymarket_core.py` and `app.py`.
- Section banners: `# ─── Section ───` (em-dashes/box drawing) used consistently as visual separators.
- Spanish module names: `buscador.py` (= "searcher"). Other identifiers in English.

## Naming
- `snake_case` for functions and variables.
- `UPPER_SNAKE` for module-level constants (`GAMMA_API`, `INTERVAL_SECONDS`, `ASSETS`).
- Short tactical names: `m` (market), `ts` (timestamp), `c` (cursor/candle), `d` (delta), `q` (qualifying row).
- Single-letter `_` prefix for private helpers: `_get_usable`, `_range_cents`.

## Functions
- Small, single-purpose; almost no classes (only `PolymarketSlugExplorer` in `buscador.py`).
- Returns dicts, not dataclasses — schema is documented by usage, not types.
- **No type hints** in `polymarket_core.py` / `app.py`. Some hints in `buscador.py` (`Optional[aiohttp.ClientSession]`).

## Error Handling
- Domain errors flow back as `{"error": "..."}` dicts on the market record (see `fetch_market`), not exceptions.
- HTTP layer (`fetch_json`) raises `RuntimeError` after retries exhausted.
- Flask routes wrap `fetch_range` in a broad `except Exception` and return `{"error": str(e)}, 500`.
- Frontend checks `if(d.error)` and logs.

## State
- **Module-level globals** in `app.py`: `progress_state`, `last_results`, `last_analysis`. Mutated directly from request handlers — single-user assumption.
- SQLite connections opened/closed per call (no pooling). Acceptable for this scale.

## Frontend
- Inline HTML in a raw string (`HTML = r"""..."""`).
- No build step. Tailwind via CDN.
- Vanilla JS, jQuery-style helpers (`$ = id => document.getElementById(id)`).
- Inline event handlers (`onclick="..."`). Heavy template-string concatenation for rendering.

## Comments
- Sparse but useful — section banners and brief intent notes (e.g., "Only cache if we got real data").
- Top-of-file docstrings on `app.py`, `analyzer.py`.

## Magic Numbers
- Fee constants embedded in `calc_net_pnl`: `0.99`, `1.082` (Polymarket 7.2% taker + Polygon 1%, documented in UI text).
- Strategy thresholds hardcoded: minutes `[1,2,3]`, deltas `[25,50,100,200]`, entry caps `[20,35,50]`.
