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
M4-1 through M4-8 ALL COMPLETE. v2 write-authority is the default production
mode. All 4 bugs resolved. 8/8 fault tests PASS (7 PASS + 1 SKIP runtime).
Fresh verifier + G-final guardian running on HEAD `c22c433`. Awaiting
ACCEPT_COMPLETION.

## Counters (code-derived, HEAD c22c433, default ON — no env var)
- NORMAL_INDEX_ENTRYPOINTS = 4
- V2_PRIMARY_INDEX_ENTRYPOINTS = 4  (LEGACY_PRIMARY = 0)
- V2_FINALIZATION_IS_SUCCESS_AUTHORITY = True
- V2_FAILURE_MASKED_BY_LEGACY_PATHS = 0
- LEGACY_WRITE_REQUIRED_FOR_SUCCESS = 0
- LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS = 0
- LEGACY_DB_REQUIRED_FOR_V2_INDEX = 0
- INCOMPLETE_WORLD_EXPOSURE_PATHS = 0
- CURRENT_WRITE_MODE = V2_PRIMARY_LEGACY_MIRROR (default ON after M4-7)
- DEFAULT_WRITE_MODE = V2_PRIMARY_LEGACY_MIRROR
- M3B_READ_AUTHORITY_REGRESSION = PASS
- All 8 fault tests = PASS (7 PASS + 1 SKIP runtime in sandbox)
- Self-tests = 7/7 PASS
- UNRESOLVED_BUGS = 0 (all 4 resolved)

## Rollback counters (env=0, for reference)
- V2_PRIMARY = 0, LEGACY_PRIMARY = 4
- V2_FINALIZATION_IS_AUTHORITY = False (flag off)
- CURRENT_WRITE_MODE = LEGACY_PRIMARY

## Next
1. Fresh verifier verdict (running on c22c433).
2. G-final guardian verdict (running on c22c433).
3. Final gate exit 0 (C25/C26 resolve once artifacts are on current HEAD).
4. Durable checkpoint declaration.
