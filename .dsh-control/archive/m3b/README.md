# M3B Archive — Frozen Snapshots

> This directory holds FROZEN snapshots of the accepted M3B milestone
> (`M3B_NATIVE_V2_READ_ENGINE_COMPLETE`) artifacts, captured at the
> accepted checkpoint HEAD `180da59`.

The M3B artifacts ALSO remain in their original active locations
(`.dsh-control/contracts/`, `.dsh-control/gates/`, etc.) because the M3B
scanner and gate serve as the C23 regression check of the M4 gate. The
copies here are the frozen-at-acceptance reference; the active copies may
evolve if a future regression-fix touches them.

## Accepted checkpoint
- **HEAD:** `180da5918dc8ab5a65e174ff24d613e8246d7fb5`
- **Branch:** `m3b-native-v2`
- **Accepted at:** M3B completion (fresh verifier + guardian PASS)

## Contents
- `contracts/` — IMMUTABLE_GOAL_CONTRACT.md, STAGE_GATE.json (frozen)
- `gates/` — scan_authority_state.py, run_stage_gate.py, self_test.py (frozen)
- `evidence/` — authority_state.json, verifier_180da59.json, guardian_180da59.json
- `prompts/` — CONTROLLER.md, LEAD.md, GUARDIAN.md, VERIFIER.md (M3B versions)
- `state/` — AUTHORITY_LEDGER.md, M3B_CURRENT_STAGE.md, M3B_PROJECT_PLAN.md,
  M3B_PROJECT_STATUS.md, DECISION_LOG.md

## M3B baseline counters (frozen)
- CORE_NORMAL_USE_SEMANTIC_TOOLS = 29
- CORE_V2_ROUTED = 29
- CORE_V2_NATIVE_READ = 29
- CORE_LEGACY_SEMANTIC_EXECUTION = 0
- CORE_UNSAFE_FALLBACK = 0
- UNRESOLVED_NORMAL_USE_V2_BUGS = 0

## History
M3B history was NOT rewritten. These are copies, not moves.
