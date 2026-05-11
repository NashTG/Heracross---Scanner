# STRUCTURE

## Layout
```
AnalyzerDiagnosis/
├── app.py                       # Flask web app + inline HTML/JS UI (374 lines)
├── polymarket_core.py           # Canonical domain library (600 lines)
├── analyzer.py                  # Legacy/standalone version (720 lines, unused)
├── buscador.py                  # Async slug-discovery tool (377 lines, standalone)
├── polymarket_cache.db          # SQLite cache (markets table)
├── analysis_snapshots.json      # Run history (appended on each fetch)
├── btc-5min-April.json          # Exported dataset
├── sol-5min-April.json          # Exported dataset
├── oldest_markets.json          # Discovery output
├── polymarket_markets.json      # Discovery output
├── polymarket_oldest.json       # Discovery output
├── test.sh                      # Shell script (not a test suite)
├── test.r                       # R script (not a test suite)
└── __pycache__/                 # Compiled bytecode (3.13, 3.14)
```

## File Responsibilities

| File | Role | Used by |
|---|---|---|
| `app.py` | Web routes, UI, request lifecycle | entry point |
| `polymarket_core.py` | Fetch, cache, analyze, simulate | `app.py` |
| `analyzer.py` | Older copy of core logic | nothing |
| `buscador.py` | Slug exploration via Gamma | manual run |

## Module Organization (within `polymarket_core.py`)
Logical sections demarcated by `# ─── ... ───` banners:
1. Constants (APIs, assets, intervals, TZ)
2. Database helpers
3. HTTP retry wrapper
4. Slug generation
5. API calls (Gamma, CLOB, Binance)
6. Full market fetch pipeline
7. Analysis helpers
8. Core `analyze`
9. OOS validation
10. Strategy simulator
11. Snapshots
12. Export/import
13. Diagnostics

## Notes
- No package structure (`__init__.py` absent). Flat module layout.
- No `tests/`, `docs/`, `static/`, `templates/` directories.
- HTML and JS live as a Python string literal in `app.py`.
