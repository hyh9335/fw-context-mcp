# FAULT_DISCOVERY_MVP_ARCHITECTURE_V1 (S0)

**Label (ratified): FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.** This is the MINIMUM architecture for proving the chain `source → evidence → mechanism hypothesis → RuleCard → refutation`. It is **NOT** a claim that the total Fault Discovery Agent architecture is complete. Supersedes the R7 architecture presentation (original preserved: archive_r7/FINAL_ARCHITECTURE.md; FINAL_ARCHITECTURE.md now carries a pointer).

## Components (Wave-1 minimum)

```
┌─────────────────────────────────────────────────────────────┐
│ GOAL ADR (S0_GOAL_ADR.md) — rubric, freeze rule, scope stamps│
└─────────────────────────────────────────────────────────────┘
┌──────────────────────┐   ┌──────────────────────────────┐
│ K1 EVIDENCE GATEWAY  │   │ K3 AUDIT-AND-FP HARNESS      │
│ immutable fact log   │   │ custody (author/custodian/   │
│ T0 direct claims ·   │   │ scorer) · freeze-hash ·      │
│ T1 fail-closed paths │   │ plant tests (per-detector    │
│ (ONLY through K1) ·  │   │ MUST-PASS) · S-audit +       │
│ T2 refused/labeled · │   │ absence adjudication ·       │
│ grep/rg · clang -E · │   │ two-outcome refutation log   │
│ build-manifest diff  │   └──────────────────────────────┘
│ (G1) · detectors     │
└──────────┬───────────┘
           ▼
┌──────────────────────────────────────────────────────────────┐
│ K2 HYPOTHESIS-AND-RULE LOOP                                   │
│ family library AS DATA · condition/config family (Wave 1)     │
│ RuleCard per FR-1 (deterministic predicates + refutation)     │
│ C5 DAG deferred (re-earn: family-#2) · R3 placeholder for     │
│ future dynamic leg (ownerless — Minority M9 stands)           │
└──────────────────────────────────────────────────────────────┘
```

## Trust flow (T0/T1/T2, ratified)
T0 claims pass with named-scope provenance; T1 claims exist ONLY as K1-mediated fail-closed paths (raw defective surfaces are never directly trusted); T2 claims are refused or labeled degraded-provenance and never load-bearing. Admission: per-detector MUST-PASS plant tests (DETECTOR_ADMISSION_MATRIX.md) — no aggregate gate.

## Extensibility (explicit — later evidence may add/restructure)
- runtime/dynamic evidence (Minority M1: R-O rules stay goal-central; scenario-03 ceiling is a Wave-1 boundary, not an architecture verdict);
- state/data propagation (C5 DAG re-earn event = family-#2 admission; M9 dynamic-leg ownership to be assigned when that family opens);
- callback-heavy family #2 (transitive propagation, D04 revisit);
- multi-variant analysis (U3/MEASURE-GAP-U3 measurement, D03 revisit);
- vector/startup analysis (F-V2-001 Track-B fix, D05 revisit);
- other fault families as measured misses demand.

None of these may be silently added: each requires a re-earn event or a user-visible scope stamp citing the goal.

## What V1 does NOT claim
- Not substrate completion; no hidden repair wave (all fw-context repairs remain S-gated Track-B, pulled by measured misses).
- Not evaluation-infrastructure completeness (pragmatic custody; KC-2A diagnostic; KC-2B later).
- Not multi-variant, not runtime, not family-complete.
- The K1/K2/K3 decomposition remains the build plan; C0–C8 remains the domain-map vocabulary; C0 plumbing stays a buy-candidate (S-buy contingency).

## Minority preservation in V1
M1 (A2: R-O rules not demoted for the goal) and M9 (A5: dynamic leg ownerless) are preserved verbatim in MINORITY_REPORT.md and referenced here as standing design constraints, not resolved items.
