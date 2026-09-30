<!-- FWCTX_AUTONOMOUS_CONTROLLER_BEGIN -->

## fw-context M4 Autonomous Controller (project-local)

This repository is governed by
[`.dsh-control/contracts/IMMUTABLE_M4_GOAL_CONTRACT.md`](.dsh-control/contracts/IMMUTABLE_M4_GOAL_CONTRACT.md)
for the `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE` milestone.

The previous milestone `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` is COMPLETE and
archived under `.dsh-control/archive/m3b/` (history not rewritten).

- **PROJECT_STATUS.md, WRITE_AUTHORITY_LEDGER.md, and the ledgers under
  `.dsh-control/state/` are CACHES, not truth.** Truth is the code-derived
  write-authority scanner (`python .dsh-control/gates/scan_write_authority.py`)
  and the executable hard gate (`python .dsh-control/gates/run_m4_stage_gate.py`).
- The M3B read-authority scanner (`scan_authority_state.py`) and gate
  (`run_stage_gate.py`) remain as a regression check (C23 of the M4 gate).
- **Current production code + the executable hard gate outrank narration.**
- **No agent may weaken the immutable milestone.** The counters and gate
  clauses in the contract are frozen; changing them requires human escalation.
- **No agent may declare the project Goal complete** unless the project-local
  executable hard gate (`run_m4_stage_gate.py`) exits 0 against the current
  durable checkpoint (git commit), AND a fresh Verifier + fresh Guardian
  accepted that exact HEAD.
- **Dual-write is NOT write authority.** v2 receiving writes is insufficient;
  v2 completion/finalization must be the authoritative success criterion.
  Legacy success must never mask v2 failure (CASE C/D of the success matrix).
- **M4 is NOT legacy retirement.** Do not delete legacy index.db, remove
  legacy writes, or implement GC — that is M5. Legacy may remain as a
  compatibility mirror.
- **Any agent asked to review completion MUST reconstruct the entry-point
  denominator from current code** (enumerate normal indexing entry points and
  trace whether v2 finalization gates success), not from cached ledgers.
- Production code changes outside the target Goal require normal engineering
  justification, recorded in `.dsh-control/state/DECISION_LOG.md`.
- This block applies only within this Git project. It must NOT edit
  `$DSH_HOME/AGENTS.md`, `cordis.patch.yml`, DSH profiles, presets, or install
  Host plugins. Everything controller-specific lives under `.dsh-control/**`
  and this file.

Role prompts and the full control loop live under `.dsh-control/prompts/` and
`.dsh-control/gates/`.

<!-- FWCTX_AUTONOMOUS_CONTROLLER_END -->
