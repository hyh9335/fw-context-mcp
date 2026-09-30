# PROJECT_STATUS — fw-context M4 (cache, NOT truth)

> **This file is a CACHE.** The code-derived scanner
> (`.dsh-control/gates/scan_write_authority.py`) and the executable hard gate
> (`.dsh-control/gates/run_m4_stage_gate.py`) are TRUTH. Reconcile every round.

**Milestone:** `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`

**HEAD:** 180da59 (branch `m3b-native-v2`)

**Gate status:** FAIL (baseline, non-vacuous). 19/27 clauses fail.
M3B read-authority regression PASS (C23 OK).

**Phase:** M4-0 setup complete. Archaeology in progress.

## Baseline counters (scanner-derived, not narrative)
- NORMAL_INDEX_ENTRYPOINTS = 4
- V2_PRIMARY_INDEX_ENTRYPOINTS = 0
- LEGACY_PRIMARY_INDEX_ENTRYPOINTS = 4
- V2_FINALIZATION_IS_SUCCESS_AUTHORITY = false
- V2_FAILURE_MASKED_BY_LEGACY_PATHS = 1
- LEGACY_WRITE_REQUIRED_FOR_SUCCESS = 3
- LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS = 1
- LEGACY_DB_REQUIRED_FOR_V2_INDEX = 3
- INCOMPLETE_WORLD_EXPOSURE_PATHS = 1
- CURRENT_WRITE_MODE = LEGACY_PRIMARY
- M3B_READ_AUTHORITY_REGRESSION = PASS
- UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS = 0
- Fault tests: all NOT_RUN

## Self-tests
`python .dsh-control/gates/m4_self_test.py` → 7/7 PASS (gate cannot fool itself).

## Previous milestone
M3B COMPLETE at 180da59. Archived under `.dsh-control/archive/m3b/`.
M3B scanner/gate retained as C23 regression check of the M4 gate.
