# PROJECT_PLAN — fw-context M4 (mutable)

> Agents MAY change this file. Agents may NOT change
> `.dsh-control/contracts/IMMUTABLE_M4_GOAL_CONTRACT.md`.

## Goal
Make v2 the authoritative production indexing/write/finalization system.
Legacy may remain as compatibility mirror. Legacy success must NOT determine
v2 success.

## Work packages

### M4-0 Setup (DONE)
- Archive M3B artifacts under `.dsh-control/archive/m3b/`.
- Create `IMMUTABLE_M4_GOAL_CONTRACT.md`, `M4_GATE.json`.
- Build `scan_write_authority.py` (code-derived, 17 counters).
- Build `run_m4_stage_gate.py` (27 clauses, no-warning-pass).
- Build `m4_self_test.py` (7/7 PASS — gate non-vacuous on baseline).
- Update `AGENTS.local.md` controller block → M4.
- Write M4 role prompts (Controller, Lead, Guardian, Verifier).

### M4-0 Archaeology (IN PROGRESS)
- Lead traces write path end-to-end → `M4_WRITE_PATH_ARCHAEOLOGY.md`.
- Inventory normal entry points, success authority, legacy postprocess.
- Guardian G1 reviews.

### M4-1 Freeze v2 completion contract
- Define World finalization invariants: exact RepositoryNamespace, Snapshot,
  Build, expected-TU accounting, immutable SemanticStates, final-state
  distinguishable from building/failed, deterministic QueryView eligibility.
- Smallest justified schema extension if needed.

### M4-2 V2_PRIMARY_LEGACY_MIRROR mode
- Implement explicit production mode where v2 finalization determines success.
- NOT `v2_success AND legacy_success`.

### M4-3 Entry-point migration
- All NORMAL_INDEX_ENTRYPOINTS reach v2 authoritative finalization.
- V2_PRIMARY == NORMAL.

### M4-4 Legacy postprocess demotion
- Classify each: V2_REQUIRED / LEGACY_COMPATIBILITY_ONLY / OBSOLETE.
- LEGACY_COMPATIBILITY_ONLY failure must not block v2 success.

### M4-5 Legacy-GATE reassessment
- SEMANTIC_SAFETY (keep) / LEGACY_READ_WRITE_COMPAT (move to mirror) / UNKNOWN (fail closed).

### M4-6 Fault-injection tests
- CASE A–D, crash-before-finalize, restart, retry, read-after-write, rollback.
- Evidence under `.dsh-control/evidence/m4_faults/`.

### M4-final
- Fresh verifier + G5 guardian + durable checkpoint + final gate exit 0.

## Stop boundary (M5 — NOT started)
Do NOT: delete legacy index.db, remove legacy writes, implement GC, remove
legacy schema, migration cleanup, legacy retirement.
