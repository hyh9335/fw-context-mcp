<!-- FWCTX_AUTONOMOUS_CONTROLLER_BEGIN -->

## fw-context M3B Autonomous Controller (project-local)

This repository is governed by
[`.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md`](.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md)
for the `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` milestone.

- **PROJECT_STATUS.md, AUTHORITY_LEDGER.md, and the ledgers under
  `.dsh-control/state/` are CACHES, not truth.** Truth is the code-derived
  scanner (`python .dsh-control/gates/scan_authority_state.py`) and the
  executable hard gate (`python .dsh-control/gates/run_stage_gate.py`).
- **Current production code + the executable hard gate outrank narration.**
- **No agent may weaken the immutable milestone.** The counters and gate
  clauses in the contract are frozen; changing them requires human escalation.
- **No agent may declare the project Goal complete** unless the project-local
  executable hard gate (`run_stage_gate.py`) exits 0 against the current
  durable checkpoint (git commit), AND a fresh Verifier + fresh Guardian
  accepted that exact HEAD.
- **Same-view legacy fallback is safe compatibility but is NOT v2-native read.**
  A `raise V2UnsupportedOperation` → legacy path counts as legacy semantic
  execution and fails the M3B gate.
- **Any agent asked to review completion MUST reconstruct the denominator from
  current code** (enumerate `v2_authority_route(...)` in `server.py`), not from
  cached ledgers.
- Production code changes outside the target Goal require normal engineering
  justification, recorded in `.dsh-control/state/DECISION_LOG.md`.
- This block applies only within this Git project. It must NOT edit
  `$DSH_HOME/AGENTS.md`, `cordis.patch.yml`, DSH profiles, presets, or install
  Host plugins. Everything controller-specific lives under `.dsh-control/**`
  and this file.

Role prompts and the full control loop live under `.dsh-control/prompts/` and
`.dsh-control/gates/`.

<!-- FWCTX_AUTONOMOUS_CONTROLLER_END -->
