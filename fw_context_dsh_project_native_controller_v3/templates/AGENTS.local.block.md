<!-- FWCTX_AUTONOMOUS_CONTROLLER_BEGIN -->
# fw-context project-local autonomous controller

This block is project-local. It does not apply outside this repository.

Authoritative project-control contract:
`.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md`

Authority order:
immutable contract > executable hard gates/current code > reproducible evidence >
fresh verifier > fresh guardian > status/ledgers > agent narration.

Project status and ledgers are caches, not truth.

For the active M3B milestone:
v2 routing is not equivalent to v2-native semantic read.
`V2UnsupportedOperation -> legacy semantic execution` remains legacy semantic execution.

No agent may weaken the immutable milestone or change its denominator merely to pass.

No agent may claim final project completion unless:
`python .dsh-control/gates/run_stage_gate.py`
passes against the current accepted/checkpoint HEAD.

Reviewers must check the original immutable milestone, not only CURRENT_STAGE wording.
<!-- FWCTX_AUTONOMOUS_CONTROLLER_END -->
