# ROADMAP (R7, A0)

Strategy adopted: **hybrid** (D01/D09) — S2's consumer-first single-track build + S3's S-gate/drop-rule/impact-statement discipline, amended by R4 findings (GA-1..4) and R5 red team (V1–V9). Evidence basis: AG-2 (S1 disqualified), A7+A8 convergence (RED_TEAM_REPORT §5), R6 revisions (C1–C6).

## Wave 0 — S0 USER GATE (hours; precondition to everything)
- One capped user turn: confirm/amend acceptance rubric (A1-P1); answer A1-Q1/Q2; confirm wave-1 scope stamp (condition/config family).
- Output: in-repo goal ADR (A1-P2). Silent-narrowing guard on the stamp text (M6).
- Exit: ADR committed; S0 not passed ⇒ escalate to user, no wave starts.

## Wave 1 — AGENT WAVE (the only wave before first e2e case exists)
1. **Contract SPEC written, not coded** (FR-3 rows; D06): refusal-not-warn, exact-no-fallback, deterministic-enumeration+truncation, unsupported-data-refusal, context-pinning, out-of-band build-manifest diff (G1 instrument). Each row names its detector + plant test + expiry date.
2. **K1 Evidence Gateway**: immutable fact log; fw-context Tier-1 queries; grep/rg channel; WSL clang -E instrument; build-manifest diff; detectors G1–G9 wired with measured-miss citations.
3. **K2 Hypothesis-and-Rule Loop** (condition/config family only): Template → QueryBundle → EvidenceGate (E1–E7) → RuleCard (FR-1); family library as data; C5 evidence-link lists only.
4. **K3 Audit-and-FP Harness**: custody split (author≠custodian≠scorer; A0 fallback custodian + custody log — O10 closed); freeze-before-measure hash; hold-one-out on S rubric; absence adjudication (ambiguity⇒PRESENT); two-outcome refutation log.
5. **Corpus wave-1** (D07): {07 exact-lookup, 08 large-fanout, PFC-1 workq lost-submission (first END-TO-END planted case)} + 1 macro-context case (GA-1 probe, scenario-03-shaped, two-part contract GA-3). Scenario-01 class banned (GA-4); scenario 02/04/05/06 deferred.
6. **Measurements, in order**: (a) KC-2 A/B FIRST (substrate vs grep+rebuild+clang+LLM; ≥30 real bugs, 5 RTOS fix histories, blind oracles, pre-registered margin); (b) guard plant-test (7 FAIL_PRODUCT patterns + GA-1 macro probe + G10 slot probe; ≥6/7 required); (c) S-stability run (gap-5; Δ>20 invalidates narrow corpus); (d) channel representativeness (gap-3). S advisory until custody ruled (D08/M3).
7. **Track-B named pull event**: raw-evidence export (macro values + single-line bodies) with gap-7 costing vs clang -dM -E BEFORE admission (V2; starvation guard M5).

## Wave 2 (conditional on wave-1 exits)
- Planted quarantined-surface case (F-V1-007 single-line plant, scenario-06-shaped) — mandatory round-2 corpus member (GA-2).
- Track-B S-gated repairs, priority by measured misses: F-V2-001 (C-form vector) → F-V1-003 (compare) → F-V1-007 (single-line) → F-V1-005 (dead-code roots). Each fix: impact statement + 58-case campaign re-run (per-fix-wave rule) + Level-2 attribution before merge (V1/K2 kill).
- Family-#2 admission review (re-earn events: C5 DAG, callback propagation D04 revisit).

## Deferred (mandatory/deferred fw-context fixes)
- **Track-B queue (S-gated)**: F-V2-001, F-V1-003, F-V1-007, F-V1-005, F-V1-001 (value surface), F-V1-004 (full propagation).
- **Not scheduled**: F-V1-006 (NEUTRAL, caveat-bound), F-V1-008 (pagination contract covers the honesty need; fix only if plant-test fails), F-V2-002 (contract spec + G6 covers; fix only if unimplementable as spec), F-V4-001 (long-session watch, M8).

## Non-goals (D10)
Standalone product polish; green tests as success; universal static analysis; multi-variant pre-U3; runtime simulation; compiler-oracle MVP layer; LLM free-text rules; goal replacement/silent narrowing; MVP corpus without the round-2 planted case.

## First end-to-end MVP (the R-1 rubric case)
PFC-1 (Zephyr workq lost-submission): planted fault in a real Zephyr target on the frozen host; Agent discovers the mechanism from source via K1 evidence (condition + registration + submission-site facts), derives a RuleCard (deterministic predicate + refutation protocol), K3 executes the refutation with blind-oracle cross-check on ≥30% of evidence rows; two-outcome verdict logged; scope stamp cites goal item 1+2.

## Kill / revisit triggers (consolidated)
- KC-2 grep-arm ≥ substrate-arm ⇒ substrate demoted to cache (D01 revisit).
- Guard plant-test <6/7 ⇒ detector redesign before any wave-2 trust.
- S-stability Δ>20 ⇒ narrow corpus invalid; S3-start assumptions void (D07 revisit).
- Channel representativeness FAIL (gap-3) ⇒ untrusted-substrate premise weakens; per-surface trust re-derivation.
- External replication refutes FAIL_PRODUCT as oracle defects ⇒ gates loosen; S1-lite arguable (D01/D02 revisit).
- Track idle >1 cycle BY CALENDAR ⇒ explicit delete-or-build (S3 kill, inherited).
- Any fix without Level-2 attribution ⇒ gate decorative (kill).
- Two rounds with neither macro-probe miss nor export pull ⇒ delete-or-build on export (M5).
- U3 measured detectable+cheap ⇒ variant machinery re-enters core (D03 revisit).
- S0 gate rejected/amended ⇒ all scope stamps re-derived from the amended ADR.
