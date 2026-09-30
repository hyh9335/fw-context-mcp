# Fixed State Machine

`INIT -> R0_EVIDENCE_FREEZE -> R1_INDEPENDENT_RESEARCH -> R2_STRATEGY_FORMATION -> R3_CROSS_EXAMINATION -> R4_SCENARIO_STRESS -> R5_RED_TEAM -> R6_CONVERGENCE -> R7_DECISION -> R8_RELEASE_AUDIT -> DONE`

`BLOCKED_HARD` is allowed only when required evidence cannot be obtained after documenting why proceeding would corrupt the decision.

A phase closes only if all required artifacts exist, every required role has a receipt, unresolved contradictions are registered, and the machine gate passes.

R0 freezes evidence. R1 is independent. R2 creates >=3 competing strategies. R3 cross-examines. R4 stress-tests concrete fault scenarios. R5 attacks local optimum/sunk cost. R6 revises. R7 decides without voting. R8 independently audits the process and roadmap.
