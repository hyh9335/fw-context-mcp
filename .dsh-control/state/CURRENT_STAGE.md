# CURRENT_STAGE — fw-context M4

> Cache, NOT truth. The scanner + hard gate are truth. Reconcile every round.

**Milestone:** `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`

**Immutable contract:** `.dsh-control/contracts/IMMUTABLE_M4_GOAL_CONTRACT.md`

**Machine gate:** `python .dsh-control/gates/run_m4_stage_gate.py`

**Code-derived scanner:** `python .dsh-control/gates/scan_write_authority.py`

**M3B regression scanner:** `python .dsh-control/gates/scan_authority_state.py`
(M3B is COMPLETE and archived under `.dsh-control/archive/m3b/`; its scanner
and gate remain as the C23 regression check of the M4 gate.)

## Phase
M4-0 setup complete. Baseline gate RED (19/27 fail), non-vacuous. M3B green.

## Baseline counters (code-derived, HEAD 180da59)
- NORMAL_INDEX_ENTRYPOINTS = 4
- V2_PRIMARY_INDEX_ENTRYPOINTS = 0  (LEGACY_PRIMARY = 4)
- V2_FINALIZATION_IS_SUCCESS_AUTHORITY = false
- V2_FAILURE_MASKED_BY_LEGACY_PATHS = 1
- LEGACY_WRITE_REQUIRED_FOR_SUCCESS = 3
- LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS = 1
- LEGACY_DB_REQUIRED_FOR_V2_INDEX = 3
- INCOMPLETE_WORLD_EXPOSURE_PATHS = 1
- CURRENT_WRITE_MODE = LEGACY_PRIMARY
- M3B_READ_AUTHORITY_REGRESSION = PASS
- All 7 fault tests = NOT_RUN

## Next
1. Guardian G0 (startup/plan validation).
2. M4 write-path archaeology (M4_WRITE_PATH_ARCHAEOLOGY.md).
3. M4-1 freeze v2 completion contract.
4. M4-2 implement V2_PRIMARY_LEGACY_MIRROR mode.
5. M4-3 migrate entry points; M4-4 postprocess demotion; M4-5 Legacy-GATE.
6. M4-6 fault-injection tests.
7. G5 + fresh verifier + durable checkpoint + final gate exit 0.
