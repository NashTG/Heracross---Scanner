# STACK

## Language & Runtime
- Python 3.13/3.14 (both `__pycache__` versions present)
- No `requirements.txt`, `pyproject.toml`, or `setup.py` detected — dependencies installed ad-hoc via `pip`.

## Frameworks & Libraries
- **Flask** — single-page web UI (`app.py`).
- **requests** — synchronous HTTP client (Gamma, CLOB, Binance).
- **aiohttp + certifi** — async slug exploration (`buscador.py` only).
- **sqlite3** (stdlib) — market cache.
- **zoneinfo** (stdlib) — ET timezone math.
- **statistics, math, re, json** (stdlib) — analysis primitives.

## Frontend
- Inline HTML in `app.py` (`render_template_string`).
- **Tailwind CSS** via CDN (`cdn.tailwindcss.com`).
- Vanilla JS — no build, no bundler, no framework.

## Persistence
- `polymarket_cache.db` — SQLite, single table `markets(slug TEXT PRIMARY KEY, data TEXT)`.
- `analysis_snapshots.json` — flat JSON list of analysis runs.
- `*.json` — exported market datasets (`btc-5min-April.json`, `sol-5min-April.json`, etc.).

## Tooling Absent
- No tests framework, no linter config, no formatter config, no CI.
- `test.sh` and `test.r` exist but are not a test suite.
