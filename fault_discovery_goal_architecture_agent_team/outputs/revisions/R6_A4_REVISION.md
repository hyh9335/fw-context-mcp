# R6 — A4 (FW_CONTEXT_AUDITOR) Position Revision

Inputs: R1_A4.md; DEBATE_ROUND_1.md (AG-1..7, UQ-1..7, C-05); SCENARIO_STRESS_TEST.md (GA-1..4); RED_TEAM_REPORT.md (V1–V9, A8 guards-vs-contracts, A7 hybrid); FACT_LEDGER.json; CONTRADICTIONS.json.

## POSITION CHANGES

PC-1 | Wrapper claim (A4-I5/P2): survives, re-scoped — A8's correction changes its boundary, not its truth.
- What changed: "wrapper converts 6 degrading finding-groups without product changes" (6 groups = F-V1-001, -002/007, -004, -005, F-V2-001, F-V2-002; 7 defects counting 002+007) is retained as an operational claim but demoted from sufficient defense to known-class mitigation layer.
- Why: A8's correction — guards fail OPEN to unknown classes; contract SEMANTICS fail safe (RED_TEAM §2). FACT: my wrapper mechanisms were per-known-finding only and never claimed F-V1-003. INFERENCE: therefore the wrapper inherits the guards' fail-open boundary and certifies nothing about unknown classes.
- Evidence: per-finding mechanisms are FACT-cited in R1 (A4-F7 detectability: `_fallback` marker, count==cap, coverage disclosure).
- Amendments: (a) fail-safe contract semantics (refusal-not-warn, exact-no-fallback, deterministic enumeration, unsupported-data-refusal) move to substrate design-time SPEC — written not coded, so "without product changes" remains TRUE (FACT: spec authorship is not code); (b) wrapper rules now REQUIRE plant-test validation (gap-4 + GA-1 macro probe, hold-one-out per V6) before any trust — INFERENCE: unvalidated wrapper rules reproduce O6 (same-lineage harness) inside my own proposal.

PC-2 | C-01 readiness statement (A4-I1): posture stands, epistemic status downgraded after V6/V5/O6.
- What changed: "fit for BOUNDED, SCOPED trial" is re-labeled a HYPOTHESIS, not a measured property.
- Why: V6 evaluation-gaming; GA-1 proved the guard list under-covers its own 7-defect input; O6 — the F-R0-13/F-R0-10 "established surface" evidence is same-lineage, oracle-authored by the campaign lineage. FACT: no external replication exists (gap 6 open, O3).
- Amended ruling text (PROPOSAL): fw-context @ d8f08a4 is fit for a bounded, scoped Agent trial — build-system-backed projects, F-R0-13 surfaces, evidence-discipline contract — WHERE the trial itself is a V6-disciplined measurement event (freeze-before-measure, blind oracles ≥30% of GT, holdout custody split, 10-case external replication). Same-lineage PASS = fitness hypothesis, not proof.

PC-3 | GA-1 does not flip my classifications; one premise correction + one caveat.
- Premise correction: F-V1-001 was never NEUTRAL — my R1 table classifies it DEGRADING_FOR_AGENT_QUALITY (macro channel absent). GA-1's macro half changes the guard-set coverage verdict, not the finding class. INFERENCE: it raises UQ-3's priority and supports substrate-side macro-surface work; it does NOT reorder P3, because macro export is now a S-gated coverage surface (A8 boundary).
- F-V1-006 stays NEUTRAL with caveat (INFERENCE): NEUTRAL holds only while no RuleCard family consumes slot classes; GA-1's slot half means such consumption would be guard-unguarded. F-V1-008 NEUTRAL unchanged (truncation detectable).

PC-4 | P3 revised: ZERO product fixes are MVP-blocking; the top two become S-gated Track-B repairs with re-earn triggers.
- What changed: R1-P3 named F-V1-003 + F-V2-001 "the only roadmap-critical product fixes". Amended: under MVP scope (A4-A2, now AG-6/GA-4) neither blocks the MVP — F-V1-003 is fenced (GA-4 hardened), F-V2-001 is cheaply mitigated consumer-side (FACT: lookup_symbol `__VECTOR_TABLE` works per the finding; E5 rule). Both become the top S-gated Track-B repairs: F-V1-003 re-earn = un-quarantine after gap-2 measurement or fix; F-V2-001 re-earn = vector C-form corpus need. F-V1-001/002/007 exposure work joins the same gate. Accepts V2 (no ungated substrate work) and A7's drop-rule.
- Why honest: R4 revalidation cadence (58-case campaign re-runs per fix-wave) raises fix cost — fence-then-trial reinforced, C-04 direction unchanged.

PC-5 | E5 over my G5 — accepted at R3 (AG-3), restated settled: cross-check ANY vector claim, not empty-only (FACT: F-V2-001's false absence is affirmative, and F-V1-006 shows even non-empty output can carry gaps).

## POSITION UNCHANGED
- U-1: C-01 adjudication core (A4-F1/F2 + I1): both verdicts TRUE under their scopes on identical code (FACT A4-F1: one docs commit, empty src diff); collision rhetorical. Only the provenance qualifier (PC-2) is new.
- U-2: I3 blocking criterion — only F-V1-003 meets all three conditions. R5 found no fourth affirmatively-fabricating surface (A4-Q2 answered negative so far; update rule registered below).
- U-3: A4-A2 build-system-backed MVP scope — now consensus-adjacent (AG-6, GA-4); unchanged.
- U-4: I4 three-cluster structure — unchanged; A8's 3-component MVP is a build-plan cut of the same map, not a contradiction.
- U-5: S1 disqualified on my evidence too (AG-2); A7's S2+gates hybrid is the evidence-led shape I can sign under (INFERENCE, not enthusiasm).

## REMAINING OBJECTIONS
- RO-1: The hybrid's load-bearing parts are all UNMEASURED (FACT: O1/O4/O7 open): gap-2 detector (see ASK-1), gap-3 channel representativeness, gap-4 plant effectiveness incl. GA-1 macro probe. I will not treat any guard or wrapper list as effective until plant-tested.
- RO-2: A7's K4 is only conditionally discharged by my UQ-3 proposal — the condition-text + build-flag channel is unmeasured for latency/representativeness; if gap-3 fails it, S2's sprint-1 form dies as stated (UNCERTAINTY).
- RO-3: F-V4-001 under a long-lived Agent MCP session remains unquantified (A4-U5); CLI-per-step indexing + read-only queries stays the safe pattern until measured.
- RO-4: ALT-S-buy contingency and the audit-without-rerun fork (deterministic predicates) still lack an owner and a cost number (INFERENCE: open schedule risk).

## ASKS TO CONTROLLER
- ASK-1 (UQ-1, owner A4 — ANSWERED): NO G1-sufficient IN-BAND per-variant provenance signal exists at 0.31.0. INFERENCE from FACTS F-R0-06 + A4-F4: every in-band signal (compare_variants/group_equivalent_variants config_hash tuples, get_active_build.defines/defines_varying, list_variants rows) is computed FROM the collapsed data, and the collapse is self-masking by construction. config_hash suffices for IDENTITY, never INTEGRITY. PROPOSAL: record the answer as NO-in-band; define G1 as an OUT-OF-BAND detector — re-derive per-variant -D sets from the build manifest and diff against get_active_build.defines (collapse signature: variants' defines identical while source trees differ) — and fold its measurement into gap-2. Until measured: the fence stands (GA-4); no bare-generator variant reads in any tier.
- ASK-2 (UQ-3, cited): PROPOSAL for A0 ruling: substrate boundary = contracted MCP surfaces ONLY. Direct DB/export read of macro values is an OUT-OF-BOUNDARY instrumented workaround — permitted in the bounded trial only as explicitly degraded-provenance evidence, never load-bearing for a RuleCard premise (schema instability, inactive-branch-row hazard, new parsing lineage = fresh oracle surface). Sprint-1 macro channel: condition TEXT via list_conditional_regions (in-band, works — FACT per R1 table) + -D flags from build files (out-of-band, cheap); per-context VALUES = S-gated substrate surface (macro export). This answers K4 without a substrate sprint-1 dependency.
- ASK-3 (C-01 final ruling input): adopt I1 + PC-2's provenance qualifier as the ruling; require R7's zero-sunk-cost answer to be computed against A7's SHRUNKEN keep-list, not the 9-component domain map.
- ASK-4 (UQ-P2-1..5 statuses): (1) variant-surface scope — settled via AG-6/GA-4; (2) trigger_conditions.variant tiering — Tier-1 only for build-system variants WITH out-of-band defines diff; bare-generator quarantined per ASK-1; (3) macro-value tier — per ASK-2; (4) fourth-fabricating-surface update rule (PROPOSAL): any substrate affirmative certification (equivalence / exact / no-fallback / coverage-said-so) of wrong data ⇒ immediate fence + out-of-band corroboration; wrapper mitigation never claimed for that class; (5) single-line raw-mode measurement (PROPOSAL): pair search_bodies vs lookup_symbol enumeration on one real project inside the bounded trial; feeds G4 shape-trigger validation.

No forced agreement claimed; PC-1..PC-5 are my own reconciliations and remain falsifiable by the gap measurements above.
