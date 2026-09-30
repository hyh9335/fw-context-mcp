# IMMUTABLE M4 GOAL CONTRACT — fw-context V2 Write / Finalization Authority

> **HUMAN-OWNED.** This contract is the single source of truth for the M4
> milestone. Agents may change PLAN. Agents may NOT change this contract
> without human escalation. PROJECT_STATUS / ledgers are caches, NOT truth.
> Current production code + the executable hard gate outrank narration.
>
> The previous milestone `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` is COMPLETE and
> archived under `.dsh-control/archive/m3b/`. Its history is NOT rewritten.

## 1. What M4 means

v2 completion/finalization is the **authoritative success criterion** for a
normal fw-context indexing run.

A normal indexing run succeeds **because the v2 World is correctly built,
accounted, finalized, and publishable.**

Legacy semantic storage may remain as a **temporary compatibility mirror**,
but **legacy success, legacy post-processing, or legacy semantic writes must
no longer determine whether a normal v2 indexing run succeeds.**

## 2. M4 is NOT legacy retirement

M4 allows: `v2 primary + legacy compatibility mirror`.

M4 does NOT require:
- deleting legacy index.db;
- removing every legacy write;
- removing legacy compatibility mode;
- GC implementation;
- full legacy code deletion.

Those belong to a later **M5** program. Do not expand M4 into M5.

## 3. Write authority semantics

The target normal production mode is:

`V2_PRIMARY_LEGACY_MIRROR`

Conceptual pipeline:

```
input → identity capture → v2 certification / fact capture →
SemanticState publication → World / TUBinding construction →
v2 completion validation → World finalization → INDEX SUCCESS →
optional legacy compatibility mirror
```

Legacy mirror ordering may differ if current architecture requires it, but the
semantic rule is fixed:

**LEGACY MUST NOT DETERMINE V2 SUCCESS.**

## 4. Immutable success matrix

The normal v2-primary mode MUST obey:

### CASE A — v2 succeeds, legacy mirror succeeds → INDEX SUCCESS
### CASE B — v2 succeeds, legacy mirror fails → INDEX SUCCESS + `LEGACY_COMPATIBILITY_DEGRADED`
A correct finalized v2 World must remain valid.
### CASE C — v2 fails, legacy succeeds → INDEX FAILURE
Legacy success must never mask a failed v2 authoritative index.
### CASE D — v2 finalization incomplete, legacy succeeds → INDEX FAILURE
An incomplete v2 World must remain unavailable as a normal completed World.

## 5. Write authority is different from "dual write exists"

Do NOT declare M4 complete merely because:
- v2 receives writes;
- v2 tables are populated;
- dual-write passes;
- a feature flag says `v2_write=true`;
- legacy and v2 both succeed.

**M4 means: v2 completion/finalization is the authoritative success criterion.**

## 6. Required machine counters (code-derived)

The scanner (`python .dsh-control/gates/scan_write_authority.py`) derives these
from CURRENT production code. They MUST NOT be sourced from
PROJECT_STATUS.md, ledgers, or Lead reports.

| Counter | Meaning |
|---|---|
| `NORMAL_INDEX_ENTRYPOINTS` | normal production indexing entry points (CLI `index`, MCP reindex, watcher/daemon reindex, library `runner.run` callers) — derived from code |
| `V2_PRIMARY_INDEX_ENTRYPOINTS` | of those, where v2 finalization is the success authority (v2 completion gates the run exit) |
| `LEGACY_PRIMARY_INDEX_ENTRYPOINTS` | of NORMAL, where legacy success still determines run success (must be 0 at completion) |
| `LEGACY_WRITE_REQUIRED_FOR_SUCCESS` | legacy write calls on the success path whose failure aborts indexing (must be 0) |
| `LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS` | legacy postprocess steps whose failure aborts indexing (must be 0) |
| `LEGACY_DB_REQUIRED_FOR_V2_INDEX` | direct legacy-DB dependencies on the v2 index path (must be 0) |
| `V2_FINALIZATION_IS_SUCCESS_AUTHORITY` | boolean: does v2 World finalization determine run success? (must be true) |
| `INCOMPLETE_WORLD_EXPOSURE_PATHS` | paths where an incomplete/un-finalized World becomes queryable/current (must be 0) |
| `UNSAFE_PARTIAL_FINALIZATION_PATHS` | finalization paths that publish a partial World (must be 0) |
| `EXPECTED_TU_ACCOUNTING_VIOLATIONS` | finalization paths that skip expected-TU reconciliation (must be 0) |
| `V2_FAILURE_MASKED_BY_LEGACY_PATHS` | paths where v2 failure is swallowed and legacy success masks it (must be 0) |
| `LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS` | paths where legacy mirror failure aborts an otherwise-successful v2 run (must be 0) |
| `CURRENT_WRITE_MODE` | detected active write mode (`V2_PRIMARY_LEGACY_MIRROR` / `LEGACY_PRIMARY` / `DUAL_EQUAL`) |
| `DEFAULT_WRITE_MODE` | the default mode a normal run uses without explicit override |
| `ROLLBACK_MODE_AVAILABLE` | boolean: is a coherent legacy-primary rollback mode available? |
| `M3B_READ_AUTHORITY_REGRESSION` | PASS/FAIL: does the accepted M3B read gate still pass? |
| `UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS` | open bugs blocking v2 write authority (tracked, must be 0) |

Fault-injection test results (runtime, stored as evidence files):

| Test | Meaning |
|---|---|
| `V2_SUCCESS_LEGACY_FAILURE_TEST` | CASE B: v2 ok + legacy fail → INDEX SUCCESS |
| `V2_FAILURE_LEGACY_SUCCESS_TEST` | CASE C: v2 fail + legacy ok → INDEX FAILURE |
| `CRASH_BEFORE_FINALIZE_TEST` | CASE D: interrupted run never becomes current World |
| `RESTART_IDEMPOTENCE_TEST` | finalized World survives restart |
| `RETRY_IDEMPOTENCE_TEST` | same Snapshot×Build retry is coherent |
| `READ_AFTER_FINALIZE_TEST` | M3B read path returns the finalized v2 World |
| `ROLLBACK_TEST` | rollback mode restores legacy-primary coherently |

## 7. M4 numeric gate (must ALL hold)

- `NORMAL_INDEX_ENTRYPOINTS > 0`
- `V2_PRIMARY_INDEX_ENTRYPOINTS == NORMAL_INDEX_ENTRYPOINTS`
- `LEGACY_WRITE_REQUIRED_FOR_SUCCESS == 0`
- `LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS == 0`
- `LEGACY_DB_REQUIRED_FOR_V2_INDEX == 0`
- `V2_FINALIZATION_IS_SUCCESS_AUTHORITY == true`
- `INCOMPLETE_WORLD_EXPOSURE_PATHS == 0`
- `UNSAFE_PARTIAL_FINALIZATION_PATHS == 0`
- `EXPECTED_TU_ACCOUNTING_VIOLATIONS == 0`
- `V2_FAILURE_MASKED_BY_LEGACY_PATHS == 0`
- `LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS == 0`
- `UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS == 0`
- `CURRENT_WRITE_MODE == "V2_PRIMARY_LEGACY_MIRROR"`
- `M3B_READ_AUTHORITY_REGRESSION == "PASS"`
- All 7 fault-injection tests == `PASS`
- Fresh Verifier accepted EXACTLY the current tested HEAD
- Fresh Guardian accepted EXACTLY the current tested HEAD
- Durable checkpoint (git commit) exists for the accepted HEAD
- No warning-pass

## 8. Authority rules for agents

- Agents may change PLAN (`PROJECT_PLAN.md`).
- Agents may NOT change this contract without human escalation.
- No agent may weaken the immutable milestone
  `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`.
- No agent may declare the project Goal complete unless the project-local
  executable hard gate (`run_m4_stage_gate.py`) exits 0 against the current
  durable checkpoint, AND a fresh Verifier + fresh Guardian accepted that
  exact HEAD.
- Equating "dual-write exists" with "write authority transferred" is forbidden.
- Legacy success masking v2 failure is forbidden.
- Deleting legacy compatibility before authority transfer is proven is forbidden.
- M4 must NOT expand into M5 (legacy retirement / GC / schema removal).
- Counters come from the scanner (code-derived), never from ledgers.
- This block applies only within this Git project.

## 9. Write-authority ledger discipline

`.dsh-control/state/WRITE_AUTHORITY_LEDGER.md` tracks SEPARATELY:
- input/run identity authority;
- SemanticState publication authority;
- TUBinding authority;
- World completion authority;
- command exit-status authority;
- legacy mirror authority;
- postprocess authority;
- rollback authority.

Never collapse all of these into one ambiguous word "write".

## 10. M3B must remain green

Every major M4 checkpoint must run the accepted M3B read gate/regression.
M4 is invalid if changing write/finalization authority breaks:
- 29/29 v2-native read;
- QueryView;
- pinned sessions;
- multi-build correctness;
- historical correctness.

## 11. Current immutable milestone

`M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`

## 12. Goal text (for the persisted DSH Goal)

> Make v2 the authoritative production indexing/write/finalization system for
> normal fw-context indexing. A normal indexing run succeeds because the v2
> World is correctly built, accounted, finalized, and publishable. Legacy may
> remain as a compatibility mirror, but legacy success/postprocess/writes must
> no longer determine whether a normal v2 indexing run succeeds. Do not
> complete this Goal unless `run_m4_stage_gate.py` exits 0 against the current
> durable checkpoint, AND a fresh Verifier + fresh Guardian accepted that exact
> HEAD. Do not expand into M5.
