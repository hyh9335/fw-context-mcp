# MINORITY REPORT (R7, A0)

Preserved disagreements and unresolved objections. No forced consensus was applied at any round; this document exists so the minority positions are auditable at R8 and re-openable by their triggers.

## M1 — A2 (held, substantive): scenario-03 ceiling does not demote R-O rules for the GOAL
A2 accepts the two-part facts+forbidden-overclaims contract (GA-3) and accepts runtime simulation as a non-goal (D10), but holds that runtime-observable rules remain goal-central: the goal says "故障机制", and queue/resource mechanisms are first-class fault mechanisms even when only their emission sites are source-visible. The decision treats the runtime slice as "unowned at wave 1" (V3); A2's position is that it is "owned at the contract level" from wave 1. **Re-open trigger**: family-#2 admission; or any external review re-weighting the goal's runtime half.

## M2 — A3: config-first wave-1 feasibility depends on the macro channel
A3 records that if macro values are unreachable in-band AND the Track-B export pull is not named and resourced, the config/condition family loses its strongest evidence tier and S-narrow becomes hollow. The decision names the pull event (D09 step 3) but does not guarantee its admission (SUBSTRATE-GAP-EXPORT costing pending). **Re-open trigger**: SUBSTRATE-GAP-EXPORT costing shows export not cheaper than clang -dM -E; or wave-1 RuleCards show evidence-tier degradation.

## M3 — A6: holdout custody feasibility is the weakest assumption
If custody split (author≠custodian≠scorer) is infeasible in a single repo with one team, the S metric must remain advisory forever, which weakens D02's S-gated re-earn machinery — the load-bearing anti-sunk-cost lever. **Re-open trigger**: custody ruling; if infeasible, D02 re-earn must switch to external-replication-only adjudication.
> S0 clarification (dissent preserved): the user ratified PRAGMATIC custody for Wave 1 (author = solving Agent; custodian = A0 with blinded expected answers; scorer = fresh verifier context post-solving; hashes before measurement). A6's underlying concern stands in amended form: if even this separation is technically impossible in the local Harness, the affected metrics must be DOWNGRADED and recorded — never faked — and S stays advisory. The ratification answers the feasibility question operationally; it does not declare A6's objection wrong.

## M4 — A2: spec-only contracts still fail open
The guards-vs-contracts correction moves fail-safe semantics to a written spec, but a spec without enforcement fails open exactly like guards do. The plant-test admission requirement (FR-4) mitigates but does not eliminate this. **Standing**; revisit at each plant-test run.

## M5 — A8: macro-surface pull event must be NAMED, not just contemplated
Starvation risk (GA-2) applies to the export surface itself: if wave 1 never measures a miss that export would close, the pull never materializes. The decision names export as the Track-B pull event but its admission depends on SUBSTRATE-GAP-EXPORT. **Re-open trigger**: two rounds with neither macro-probe miss nor export pull ⇒ explicit delete-or-build decision (A8 amended rule).

## M6 — A8 + A2 (co-signed): silent-goal-narrowing risk in S-narrow
Wave-1 scope stamping must cite the goal, never redefine it. The S0 gate text carries this guard; any later scope stamp must pass the same test. **Standing**; checked at every scope stamp.

## M7 — A7's ten unresolved objections (O1–O10, unchanged from R5)
O1 KC-2 unmeasured; O2 UQ-2 rubric N/M unset (S0 gate); O3 C-01 formally unadjudicated (KC-4); O4 MEASURE-GAP-CHANREP channel representativeness unvalidated (top risk per A5); O5 macro channel absent in sprint 1 (partially resolved by condition-text + -D-flags channel; values still absent); O6 same-lineage harness laundering (V6 discipline adopted, not proven); O7 guard effectiveness unmeasured (plant-test in wave 1); O8 runtime slice unowned (M1 disagrees); O9 S-stability unexecuted (commissioned, D08); O10 gaming-freeze owner (A0 defaults per A6 ASK-2 — assigned: custodian A0 with custody log).
> S0 status note (objections preserved; resolution states attached): O2 — the undefined N/M symbols are RETIRED and replaced by the explicit 10-item Wave-1 rubric (S0_GOAL_ADR §2); A3's ask is answered, the objection text is retained as history. O10 — owner assigned (custodian A0 + custody log). O7 — restated as per-detector MUST-PASS (DETECTOR_ADMISSION_MATRIX); unmeasured until plant tests run. O1 — split into KC-2A (post-first-E2E diagnostic) / KC-2B (investment gate); still unmeasured. O4/O5/O6/O8/O9 remain open with their original triggers.

## M8 — A4: F-V4-001 long-session impact unquantified
The long-session index divergence finding remains unquantified; it is NEUTRAL for wave-1 (short sessions) but could affect any extended Agent session. **Re-open trigger**: any wave-2 session > the measured duration envelope.

## M9 — A5: dynamic leg has no home in K1–K3
V3's "minimal dynamic leg" mitigation has a registered R3 placeholder (D09) but no component owns it. A5 objects that leaving it ownerless repeats the pattern the pack criticizes. **Re-open trigger**: family-#2 planning; goal-item-1 scope review.
