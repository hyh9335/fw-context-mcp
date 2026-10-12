# M9 EXECUTION PROTOCOL

Use the same DSH host and project-local control system. Do not modify global DSH config.

Roles: Controller, Lead Engineer, Frontend Safety Skeptic, Certificate Dependency Auditor, Performance/Economics Auditor, Fresh Verifier, Fresh Guardian.

Controller owns stage transitions and completion authority. Lead may change implementation details but not Goal, safety contract, denominator, Ground Truth, or completion formula.

Maintain machine state under `state/` and histories under `runtime/`; do not depend on chat memory.

Stages: BOOTSTRAP -> BASELINE_FREEZE -> CERTIFICATE_ARCHAEOLOGY -> RED_SAFETY_GATE -> PROTOTYPE -> ADVERSARIAL_EXPANSION -> WORKLOAD_PROBE -> PRODUCTION_INTEGRATION -> PERFORMANCE_VALIDATION -> FINAL_AUDIT -> terminal verdict.

Core loop: observe -> hypothesis -> minimal repro -> RED -> implement -> focused GREEN -> adversarial tests -> regressions -> canaries -> scanner -> hard gate -> fresh review -> repair -> rerun.

Escalate only for product-semantics ambiguity, a proven need to violate safety, a proven requirement to fork/replace Clang, or a fundamental out-of-scope architecture change.
