<!-- FWCTX_AUTONOMOUS_CONTROLLER_BEGIN -->

## fw-context M5 Autonomous Controller (project-local)

This repository is governed by
[`.dsh-control/contracts/IMMUTABLE_M5_GOAL_CONTRACT.md`](.dsh-control/contracts/IMMUTABLE_M5_GOAL_CONTRACT.md)
for the `M5_V2_LIFECYCLE_AND_LEGACY_AUTHORITY_RETIREMENT_COMPLETE` milestone.

The previous milestones are COMPLETE and archived (history not rewritten):
- `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` — accepted at `180da59`, archived
  under `.dsh-control/archive/m3b/`.
- `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE` — accepted at `10d1f36`,
  archived under `.dsh-control/archive/m4/`.

- **PROJECT_STATUS.md, WRITE_AUTHORITY_LEDGER.md, LIFECYCLE_LEDGER.md, and
  the ledgers under `.dsh-control/state/` are CACHES, not truth.** Truth is
  the code-derived M5 scanner (`python .dsh-control/gates/scan_m5_state.py`)
  and the executable hard gate (`python .dsh-control/gates/run_m5_stage_gate.py`).
- The M3B read-authority scanner (`scan_authority_state.py`) and the M4
  write-authority scanner (`scan_write_authority.py`) remain as regression
  checks (C20/C21 of the M5 gate).
- **Current production code + the executable hard gate outrank narration.**
- **No agent may weaken the immutable milestone.** The counters and gate
  clauses in the contract are frozen; changing them requires human escalation.
- **No agent may declare the project Goal complete** unless the project-local
  executable hard gate (`run_m5_stage_gate.py`) exits 0 against the current
  durable checkpoint (git commit), AND a fresh Verifier + fresh Guardian G6
  accepted that exact HEAD.
- **Measure execution paths, not file names.** A legacy dependency exists
  only if normal/default operation actually reads/writes/creates/consults
  legacy semantic storage. Importing an isolated, never-executed
  compatibility module is NOT semantic authority.
- **GC must be reachability-based.** No age-only semantic GC. GC must not
  delete a pinned World, the current completed World, or a SemanticState
  referenced by another World.
- **M5 is semantic/operational retirement, NOT physical deletion.** An
  explicit, isolated, default-OFF compatibility island may remain. Physical
  deletion of every legacy module is a FUTURE optional task (§41 stop
  boundary). Do NOT expand M5 into storage/performance redesign or new
  features.
- **Any agent asked to review completion MUST reconstruct runtime authority
  from current code** (trace whether normal/default operation touches legacy
  semantic storage), not from cached ledgers.
- Production code changes outside the target Goal require normal engineering
  justification, recorded in `.dsh-control/state/DECISION_LOG.md`.
- This block applies only within this Git project. It must NOT edit
  `$DSH_HOME/AGENTS.md`, `cordis.patch.yml`, DSH profiles, presets, or install
  Host plugins. Everything controller-specific lives under `.dsh-control/**`
  and this file.

Role prompts and the full control loop live under `.dsh-control/prompts/` and
`.dsh-control/gates/`.

<!-- FWCTX_AUTONOMOUS_CONTROLLER_END -->
