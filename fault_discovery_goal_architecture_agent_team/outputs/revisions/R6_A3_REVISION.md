# R6 — A3 (Evidence Rule Specialist) Position Revision

Inputs: R1_A3.md, DEBATE_ROUND_1.md (C-05, UQ-2/3/6), SCENARIO_STRESS_TEST.md (GA-1..4), RED_TEAM_REPORT.md (V1–V9, A8 corrections, ALT-S-narrow), FACT_LEDGER F-R0-04..18, CONTRADICTIONS C-01..04.

## POSITION CHANGES

PC-1. T2 gate SPLITS and partially relocates (Agent-side → substrate-spec). [PROPOSAL]
- [FACT] A8's correction (RED_TEAM §2) separates guards (detective, fail-open) from contract semantics (preventive, fail-safe, design-time SUBSTRATE SPEC written not coded). My R1 T2 was one Agent-side 5-check gate.
- Change: T2-SPEC = checks enforceable as refusal at the evidence boundary — rows/3 format, no `_fallback` on existence facts (F-R0-09/F-V2-002), context tuple present, stale/disk-origin refusal — become fail-safe contract semantics in K1 Evidence Gateway's written spec (zero Agent LOC, exempt from guard-plant validation). T2-AUDIT = rule-semantic checks needing judgment — polarity-vs-claim matching (I1), llm_analysis ban, min-over-precondition certainty — stay Agent-side (K2/K3) and [INFERENCE] being scoreable, fall under V6: hold-one-out validation + expiry date.
- [INFERENCE] Why the split survives V6: a refusal is transcript-observable, not an optimizable score; spec-side checks cannot be gamed the way guard counts can. [FACT] F-R0-06 (self-masking F-V1-003) shows detection-only layers fail; refusal layers do not.

PC-2. RuleCard validation_status / refutation_protocol hardened against V5/V6. [PROPOSAL]
- [FACT] V5: self-authored oracles (FR-R1 precedent: the expectation itself was wrong — F-R0-11); V6: S+guards are the optimization target. My R1 `validation_status: source_derived|trace_confirmed|human_adjudicated` conflates rule status with oracle quality.
- Change: add `oracle_provenance` per validation event (independent_blind | seeded_fixture | source_rederivation | trace | human), recording the V5 floor (≥30% independent-blind rows) on the RuleCard; refutation_protocol becomes two-outcome {trigger, rule_falsified, oracle_falsified→adjudicator} — FR-R1 proves refutation can indict the GT, not the rule.
- [PROPOSAL] freeze-before-measure: schema + refutation protocols frozen before any S-scored decision (V6 mitigation); owner = O10, currently unset. [PROPOSAL] adopt GA-3: runtime-class RuleCards carry two-part claims (fact + forbidden-overclaim), never one assertion.

PC-3. MVP sequencing changes; R3 splits into R3a/R3b (ALT-S-narrow + GA-4). [PROPOSAL]
- [FACT] ALT-S-narrow is adopted as a sequencing constraint (config-class first; honest loss: call-graph differentiator untested in wave 1). [FACT] It is named "config-class R2" — under MY numbering that family is R3; I read the constraint family-wise (config-conditional), not index-wise, and flag the numbering fork for the C-05 ruling.
- Change: R3 → R3a (within-context: "branch X compiles in config C; feature F iff -DF" — corpus-eligible now) and R3b (cross-variant claims — BLOCKED by GA-4 until U3 is measured or F-V1-003 fixed). My R1 R3 viability condition (per-variant compile_commands + tuple discipline) becomes the R3b gate; R3a inherits only tuple discipline.
- [INFERENCE] Wave-1 set = R1 + R4 + R3a + R6-decomposed-preconditions; R2 leaves wave 1 (accepting the honest loss), R2-as-lower-bound deferred to the family-#2 admission event (A8 C5 re-earn).
- [FACT] Coupling: config rules lean on macro values, which have no consumer surface (F-R0-16); if A0 rules macro DB/export read outside the substrate boundary (UQ-3), R3a loses its primary evidence and S-narrow becomes infeasible without a Track-B pull. [PROPOSAL] My position: row/export-level macro read IS inside the boundary — provenance (def_source, context) is schema-level, and lookup-surface `_fallback` failure modes (F-R0-09) do not exist on DB reads, which also simplifies T2-SPEC.

PC-4. Taxonomy scope-stamped (V9) and unified with A6's GT table (C-05). [PROPOSAL]
- [FACT] V9 flags premature generalization; C-05 records the convergence path (root-attestation qualifier; R7 GT row added at R6).
- Scope stamp: the 8 types are valid for compiled C/C++ with a per-context index; family-#2 admission (scenarios 02/04 class) is the re-earn event for extension.
- My C-05 contribution: R2 positive claims carry the root-attestation qualifier (valid iff certainty='exact' AND root attested — my F-V1-005 caveat in shared form); R7 GT row = counterexample-protocol-only (one counterexample refutes; absence of counterexamples never proves — I1 applied); R3 split per PC-3.

## POSITION UNCHANGED

PU-1. [INFERENCE] Polarity asymmetry (I1) as the central FP-control device — reinforced: A8's fail-safe-vs-fail-open argument is the substrate-level form of the same principle; my asymmetry is its rule-layer instance.
PU-2. [INFERENCE] Producer-not-validator (I3) — reinforced by F-R0-06 and AG-4; T4's out-of-band R3 re-derivation stays wave-1-critical for R3b, deferred with it.
PU-3. [INFERENCE] Typed certainty (I4) — A8's "exact-no-fallback, deterministic enumeration" are exactly its enforcement points.
PU-4. [INFERENCE] MVP exclusions stand: R5-as-diagnosis-rule, universal invariants, async sequences; GA-3 + the scenario-03 ceiling confirm them as program-wide facts, not strategy choices.
PU-5. [FACT] No Agent code exists (F-R0-18); all of the above is spec-level, consistent with the 0.31.0 product freeze and read-only constraint.

## REMAINING OBJECTIONS

RO-1. [UNCERTAINTY] UQ-3 macro boundary is the sharpest unresolved coupling: S-narrow sequencing vs Track-B macro surface conflict unless UQ-3 resolves as PC-3 proposes. Not settled here.
RO-2. [UNCERTAINTY] U2 (explain_condition nondeterminism, F-R0-15) is unresolved: an R3a precondition that flips between runs cannot pass a deterministic audit. If confirmed at rule level, R3a needs run-pinned context_id evidence or an explicit waiver.
RO-3. [UNCERTAINTY] O1/KC-2 unmeasured: my per-type evidence bindings are substrate-relative. If the grep-arm wins KC-2, R1/R4 evidence surfaces degrade to raw text and the type→evidence mapping must be re-derived; the claim-form taxonomy survives, the bindings do not.
RO-4. [UNCERTAINTY] U1 (candidate_usrs completeness) and U4 (build-source vector rows for absence claims) remain untested; absence-claim rules touching those surfaces stay blocked at my gate.
RO-5. [UNCERTAINTY] O10: freeze ownership is unset — until an owner exists, PC-2's freeze-before-measure is aspirational.

## ASKS TO CONTROLLER

AK-1. [PROPOSAL] RuleCard schema freeze prerequisites, in order: (1) UQ-2 rubric N/M + GT-class mix ruling (V5 floor); (2) UQ-3 macro-boundary ruling (gates R3a feasibility); (3) O10 freeze owner named; (4) oracle_provenance + two-outcome refutation accepted. No S measurement counts before all four.
AK-2. [PROPOSAL] C-05 ruling requested: adopt the unified taxonomy (PC-4: scope stamp + root-attestation qualifier + R7 counterexample-only GT row + R3a/R3b split) as the single type set for R7, so UQ-6's per-rule-type metric binding has one referent.
AK-3. [PROPOSAL] Absence-adjudication owner for the S-audit: K3 (Audit-and-FP Harness), with (a) a mandatory empty-surface classifier step (C-03 class) before any absence-claim RuleCard passes, (b) the rubric pre-registered and frozen before measurement (V6).
AK-4. [PROPOSAL] T2 placement ruling: T2-SPEC into K1's written substrate spec; T2-AUDIT into K2/K3 under hold-one-out + expiry. Ask A0/A8 to confirm inside the 3-component build plan (V7).
AK-5. [PROPOSAL] Metric-binding sketch for UQ-6: R1/R4 = existence precision; R2 = lower-bound recall only (never may-target precision); R3a = branch-state agreement vs external re-derivation; R6 = precondition precision, implication excluded from S until a dynamic leg exists (V3).
