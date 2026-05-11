# STATE — AnalyzerDiagnosis

## Project
Polymarket Up/Down binary-market analyzer (brownfield). Single-user local research tool.

## Current Milestone
**Stabilize + Improve Strategy Finder** (active).
- Goal: regression net + slug-resilience + EV-aware strategy discovery.
- See `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`.

## Status
- Codebase mapped: `.planning/codebase/` (7 docs).
- Research: `.planning/research/polymarket-api-resilience.md`, `polymarket-market-coverage.md`.
- Cleanup: deleted `analyzer.py`, `test.r`, `test.sh`, buscador json outputs, `__pycache__`. Added `.gitignore`.
- Requirements + roadmap drafted (7 phases).
- **Next:** `/gsd-plan-phase 1` — Test Foundation.

## Recent Decisions
- **2026-05-07** — Stack tested via mocked HTTP (no live calls in test suite).
- **2026-05-07** — Slug resilience uses search-then-derive fallback, not full template rewrite.
- **2026-05-07** — EV/Sharpe replaces 70% hit-rate gate; legacy threshold remains configurable.
- **2026-05-07** — `buscador.py` kept and extended (oldest-market discovery), not deleted.
- **2026-05-07** — Out of scope this milestone: requirements.txt, multi-user/WSGI, auth, live monitoring, concurrency-safe globals rewrite.

## Open Questions
- Walk-forward fold count when fetch range is small (<30 markets) — degrade to single-split? Decide in Phase 6.
- New strategy classes (R7) — pick which two to implement first when Phase 7 plans.

## Live Bot Cross-Reference
Separate project. See: `C:\Users\Ignacio\.claude\projects\C--Users-Ignacio-Documents-Poly-Proj-mac0-proj\memory\SESSION_HANDOFF.md`. AnalyzerDiagnosis is the research/analysis side; the live bot consumes findings.
