# RED TEAM REPORT — R5 Aggregate (A0 record)

Sources: `redteam/R5_A7_ATTACKS.md` (A7 lead: 9 vectors, updated zero-sunk-cost, 10 retained objections), `redteam/R5_A8_ALTERNATIVES.md` (A8: unconsidered alternatives, complexity attack, updated answer). Claims labeled in sources; this aggregate preserves attribution.

## 1. Attack matrix (A7 lead; severity per A7; strategy hit where stated)

| # | Vector | Severity | Hits hardest | Mitigation (adopted for R6) | Revisit trigger |
|---|---|---|---|---|---|
| V1 | Sunk cost (decision frame still substrate-indexed) | CRITICAL | all; S1 fatal | KC-2 A/B gate pre-registered; auto-strike impact-less F-V* fixes; 1-wave substrate cap until first e2e Agent case | first end-to-end case exists |
| V2 | Unnecessary fw-context work (guards = substrate work in disguise; export never costed) | HIGH | S2/S3 | gaps 7+3 before admitting export; guard expiry dates | export costed vs clang -dM -E |
| V3 | Missing dataflow/state reasoning (runtime half of goal unowned) | HIGH | all (program-wide ceiling) | minimal dynamic leg + GA-3 two-part labels on runtime-class RuleCards | first dynamic-leg case defined |
| V4 | Source-only limits (commit-derived GT survivorship bias; one host) | MEDIUM | evaluation design | ≥20% non-commit GT rows; ≥1 field-log-derived case | corpus authoring |
| V5 | Weak GT (self-authored oracles; FR-R1 precedent) | CRITICAL | all | blind independent oracle ≥30% of GT rows; 10-case external replication (gap 6) | before any S measurement counts |
| V6 | Evaluation gaming (S + guards are the optimization target; GA-1 caught under-coverage) | CRITICAL | S2/S3 | hold-one-out guard validation; holdout custody split; freeze-before-measure | any S-scored decision |
| V7 | Architecture complexity (9 components + 7 guards at 0 Agent LOC; GA-2 half-triggers S3's own kill) | HIGH | S2/S3 | A8's 3-component MVP (K1 Evidence Gateway, K2 Hypothesis-and-Rule Loop, K3 Audit-and-FP Harness); "component needs a measured miss to exist" | wave-1 build plan |
| V8 | Too-narrow MVP (S-bias by construction; macro class untested; Track B starves) | HIGH | S3 | GA-2 plant round 2 + ≥1 macro case round 1 + gap-5 Δ>20 rule | corpus round 1 definition |
| V9 | Premature generalization (5 RTOS → "firmware/software"; blanket GA-4 from 2 observations) | MEDIUM | all | scope stamping; gap 2 before un-quarantine | corpus expansion |

## 2. Unconsidered alternatives (A8)
- **ALT-S0 "reassess the goal" (HIGH, near-zero cost)**: every strategy's success criteria are wave-internal; no external-to-pack commitment test exists anywhere in the pack. Mitigation adopted: **S0 becomes a precondition GATE on any implementation wave** — one user turn confirming/amending the A1-P1 acceptance rubric and answering A1-Q1/Q2, recorded as an in-repo goal ADR (A1-P2).
- **ALT-S-buy "adopt an existing agent harness" (MEDIUM-HIGH schedule / MEDIUM correctness)**: buy the LOOP (C0/C7 plumbing) not the discipline (C2/C3/C6/corpus/G1–G7). Unregistered definitional fork registered for adjudication: audit-without-rerun (A8 A-3) vs transcript-auditability — rules must stay deterministic predicates to satisfy both. Contingency rule: buy only if bespoke skeleton exceeds ~2 wave-equivalents.
- **ALT-S-narrow "one rule family end-to-end" (MEDIUM)**: adopted as a SEQUENCING CONSTRAINT inside the winning strategy (config-class R2 first), not a fourth strategy. Honest loss recorded: wave-1 config-only never tests the call-graph differentiator.
- **Guards vs contracts self-correction (A8, HIGH for S2's zero-change claim)**: guard completeness is measured INCOMPLETE against its own 7-defect input list (no macro-observability, no slot-classification guard; GA-1) and scenario 03 is a no-guard ceiling — guards fail open to unknown classes, contract SEMANTICS fail safe. Corrected boundary adopted for R6: fail-safe contract semantics (refusal-not-warn, exact-no-fallback, deterministic enumeration, unsupported-data-refusal) are design-time SUBSTRATE SPEC (written, not coded); coverage SURFACES (macro export, vector C-form) stay S-gated Track-B items.
- **Complexity verdict (A8)**: C0–C8 correct as DOMAIN MAP, wrong as BUILD PLAN; 3-component MVP adopted as the build plan candidate; C5 deferred with re-earn event "family #2 admission" (scenarios 02/04 class).

## 3. Updated zero-sunk-cost answers (mandatory, both roles)
- **A7: still NO** — keep-list SHRINKS (runtime slice moves to non-substrate leg; from-scratch substrate core = index + per-context views + direct call graph + stdio + export pending gap-7). Least-biased shape: **S2/S3 hybrid** — S2 single-track consumer-first build + S3's S-gate/drop-rule/impact-statements on substrate items + GA-2 planted quarantined case round 2 + GA-1-hardened guards + KC-2 A/B as FIRST measurement. S1 disqualified. Flip evidence ordered: (1) KC-2 grep-arm ≥ substrate-arm; (2) U3 detects divergence cheaply; (3) external replication refutes FAIL_PRODUCT as oracle defects; (4) guard-plant ≥6/7 incl. macro probe; (5) channel representativeness fails.
- **A8: KIND survives, scope re-derived** — kernel + disciplined agent; re-earn becomes a mechanism (probe-or-event or deliberate deletion); contract semantics substrate-side; build 3 components not 9; S0 gate first; S-buy contingency armed.

## 4. A7's ten retained unresolved objections (carry to R6/R7 — none resolved by R5)
O1 KC-2 unmeasured; O2 UQ-2 GT + rubric N/M unset; O3 C-01 formally unadjudicated (KC-4); O4 gap-3 channel representativeness unvalidated; O5 macro channel absent in sprint 1 (UQ-3/KC-1); O6 same-lineage harness unresolved in all strategies; O7 guard effectiveness incl. macro probe unmeasured; O8 runtime-class slice unowned; O9 gap-5 S stability unexecuted; O10 evaluation-gaming freeze has no owner.

## 5. A0 synthesis note
The two red-team records CONVERGE on the same structural conclusion from different directions: the binding constraint is not substrate quality but **externally-owned success criteria** (S0 gate) plus a **measurement-first repair regime** (S-gate with guaranteed corpus coverage). Neither red-teamer endorses S1; both endorse a hybrid of S2's consumer-first build with S3's gating discipline, amended by GA-1/GA-2. This is the input shape for R6 revision and R7 decision — recorded as evidence-led convergence, not a vote.
