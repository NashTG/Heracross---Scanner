# TESTING

## Current State
**No automated tests.** No `pytest`, `unittest`, or test framework in use.

## Files Named "test"
- `test.sh` — shell script. Not a test runner.
- `test.r` — R script. Not a Python test suite.
- Neither is invoked by CI or referenced from Python code.

## In-Code Verification
- `run_diagnostics()` in `polymarket_core.py:556` — probes one known-good slug per asset×interval against Gamma + CLOB. Functions as a **smoke test** for live API health, exposed via the "Diagnose" tab in the UI.
- `validate_oos()` (`polymarket_core.py:440`) — split-half train/validate over fetched data. This is statistical robustness testing of strategies, not unit testing.

## Test Infrastructure Absent
- No `tests/` directory.
- No CI (no `.github/workflows`, no Jenkinsfile, etc.).
- No coverage reporting.
- No mocking layer for external APIs.

## Manual QA Surface
- "Diagnose" UI tab — verifies live API connectivity per asset/interval pair.
- "OOS" UI tab — checks strategy generalization on a fetched dataset.
- "Snapshots" UI tab — historical record of fetch/analysis runs (`analysis_snapshots.json`).

## Risk
- Refactors of `polymarket_core.py` (slug logic, fee math, strategy thresholds) have no regression safety net.
- Network/contract drift (Gamma slug schema changes, CLOB response shape) only surfaces via `run_diagnostics` after a manual user action.
