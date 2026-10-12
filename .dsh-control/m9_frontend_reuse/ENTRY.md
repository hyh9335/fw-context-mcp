# M9 — Certificate-Gated Cross-Build TU Frontend Reuse
# SINGLE ENTRY AUTONOMOUS PROGRAM

You are the TOP-LEVEL CONTROLLER for the fw-context M9 stage.

This is the ONLY human entrypoint.

The previous M8 feasibility study reached `LIMITED`.

Accepted finding:

> Cross-build frontend reuse is safe and useful only when a Translation Unit's real frontend dependency context is equivalent across Builds.
> It can yield meaningful savings on per-file-config workloads, while correctly yielding ~0% reuse on global-config workloads such as the tested FreeRTOS setup.
> The recommended implementation is a certificate-gated TU-level frontend reuse layer upstream of M6, NOT a new parser, NOT a TypeChef/SuperC migration, and NOT a redesign of the Shared Semantic Fact Graph.

Your job is to build, validate, adversarially attack, repair, and either productionize or reject this narrowly scoped optimization.

Do not stop after prototypes. Do not ask the human whether to continue between phases. Do not broaden into Clang CAS, PCH, Modules, TypeChef, SuperC, or a custom parser unless the immutable goal is proven impossible and a new human decision is required.

Read and maintain all files under `.dsh-control/m9_frontend_reuse/`.

Start by reading:
- `contracts/IMMUTABLE_M9_GOAL.md`
- `contracts/M9_SAFETY_CONTRACT.md`
- `contracts/M9_COMPLETION_FORMULA.md`
- `EXECUTION_PROTOCOL.md`
- `AGENT_ROLES.md`
- `TEST_AND_REVIEW_PLAN.md`
- `WORKLOAD_PROBE_SPEC.md`
- `PRODUCTION_INTEGRATION_RULES.md`
- `FINAL_GATE_SPEC.md`

Terminal outcomes allowed:
1. `M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE`
2. `M9_LIMITED_BUT_PRODUCTION_WORTHY`
3. `M9_REJECTED_NO_NET_VALUE`
4. `M9_BLOCKED_HUMAN_DECISION`

Do not claim completion merely because cache hits occur. The optimization must produce safe, real avoided frontend work and positive end-to-end value on eligible workloads while safely degenerating to ordinary parsing on ineligible workloads.

BEGIN NOW.
