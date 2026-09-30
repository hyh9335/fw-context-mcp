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
M4-2/4/5/8 complete. v2 write-authority semantics implemented + fault tests
7/7 PASS. Guardian G2 + fresh verifier pending. M4-7 (flip default) deferred
until G2 verdict. HEAD `9a45b2d`.

## Counters (code-derived, HEAD 9a45b2d, flag ON)
- NORMAL_INDEX_ENTRYPOINTS = 4
- V2_PRIMARY_INDEX_ENTRYPOINTS = 4  (LEGACY_PRIMARY = 0)
- V2_FINALIZATION_IS_SUCCESS_AUTHORITY = True
- V2_FAILURE_MASKED_BY_LEGACY_PATHS = 0
- LEGACY_WRITE_REQUIRED_FOR_SUCCESS = 0
- LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS = 0
- LEGACY_DB_REQUIRED_FOR_V2_INDEX = 0
- INCOMPLETE_WORLD_EXPOSURE_PATHS = 0
- CURRENT_WRITE_MODE = V2_PRIMARY_LEGACY_MIRROR (flag ON)
- DEFAULT_WRITE_MODE = LEGACY_PRIMARY (M4-7 will flip to V2_PRIMARY_LEGACY_MIRROR)
- M3B_READ_AUTHORITY_REGRESSION = PASS
- All 7 fault tests = PASS
- Self-tests = 7/7 PASS
- UNRESOLVED_BUGS = 1 (M4-BUG-003, flag default OFF — M4-7)

## Baseline counters (flag OFF, for reference)
- V2_PRIMARY = 0, LEGACY_PRIMARY = 4
- V2_FINALIZATION_IS_AUTHORITY = False (flag off)
- CURRENT_WRITE_MODE = LEGACY_PRIMARY

## Next
1. Guardian G2 verdict (running).
2. Fresh verifier verdict (running).
3. M4-7: flip v2_write_authority default to True (after G2 approval).
4. M4-6: Legacy-GATE reassessment (SEMANTIC_SAFETY/LEGACY_COMPAT/UNKNOWN).
5. G5 + fresh verifier + durable checkpoint + final gate exit 0.
