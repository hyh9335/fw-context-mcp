# K3 INTERFACE SPEC — Audit-and-FP Harness (Wave-1)

Component K3 of FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md. Governing inputs: S0_GOAL_ADR.md §1/§2/§4, CORPUS_LOCK_PLAN.md (§2 recipe, §4 custody, §9 checklist), DETECTOR_ADMISSION_MATRIX.md, REFUTATION_STATE_MODEL.md, FREEZE_MANIFEST_SPEC.md, WAVE1_CORPUS_MATRIX.md §3.

## 1. Freeze manifest contract
K3 validates the round freeze manifest per FREEZE_MANIFEST_SPEC.md (canonical JSON; per-artifact SHA-256; combined manifest hash; freeze_id = FRZ-<CASE>-<YYYYMMDDTHHMMSSZ>). K3 refuses to open a round unless: all hashes verify; every oracle file hash is present; custodian-only files are excluded from the solver context (CORPUS_LOCK_PLAN.md §9.3). Hash mismatch ⇒ round refused (rollback in WAVE1_IMPLEMENTATION_PLAN.md §2).

## 2. Oracle custody artifact (HARD CLAUSE)
- Custodian-only content (CORPUS_LOCK_PLAN.md §4): every FAULT_ORACLE.json; C4 EVIDENCE_MAP.md; C4+C6 SOURCE_LOCK.json; C6 PAIRING_NOTE.md; C1+C5 PLANT_DIFF.md; the C4/C6 A/B snapshot mapping; the blind-row sampling list.
- Oracle HASH is public (in the freeze manifest); oracle CONTENT is hidden.
- HARD CLAUSE: K3 MUST NEVER reveal hidden oracle content to the solving Agent before freeze/solve completes. K3 enforces this mechanically: the solver-bundle assembler emits only solver-visible inputs (snapshot/fixture tree, CASE_DESIGN.md, redacted task brief); any custodian-only path addressed from the solving Agent's session is rejected and logged as a custody event. ANY leakage voids the round (S0_GOAL_ADR.md §1/§4).

## 3. Plant-test runner contract
run_plant_tests(load_bearing_only: bool) → PLANT_RECEIPT[]
- Packages: plant_tests/PT-G1, PT-G4, PT-G6, PT-G7, PT-G8 (each = fixture/ + PLANT_SPEC.md + EXPECTED.json). G2/G3/G9 smoke stubs are reported, non-blocking.
- Per package: (1) recompute fixture_hash per the PLANT_SPEC recipe, compare to the manifest; (2) execute the K1/detector path under test against the fixture (substrate access via K1 only — K1_INTERFACE_SPEC.md HARD RULE); (3) compare observed behavior to EXPECTED.json pass_condition; a fail_condition match ⇒ FAIL.
- MUST-PASS gate: G1, G4, G6, G7, G8 must EACH pass 100% of mandatory plant tests BEFORE any acceptance measurement (DETECTOR_ADMISSION_MATRIX.md). Any FAIL ⇒ stop; fix the detector or re-tier the surface; re-run the FULL MUST-PASS set. No aggregate workaround.
- Receipt: {receipt_id, freeze_id, detector, package, fixture_hash, checks_evaluated, verdict, executed_at, executor} — immutable, appended to the audit log.

## 4. RuleCard scorer contract (fresh verifier context only; after solving)
score_rulecard(rulecard, frozen_outputs, oracle) → SCORED_VERDICT
1. Schema validation against RULECARD_SCHEMA.json, including refutation_result conformance to REFUTATION_STATE_MODEL.md §5.
2. Execute refutation_protocol steps to completion under the round freeze_id (substrate steps replayed via K1; independent_crosscheck steps re-executed; counterexamples verified and recorded as FactRecords under the SAME freeze_id).
3. Adjudicate refutation_result per the REFUTATION_STATE_MODEL.md §3 acceptance matrix, INCLUDING the C6 expected-refutation case: scope_stamp.corpus_case="C6" scores success iff state=REFUTED AND validation_status=rejected AND the counterexample came from the declared protocol under freeze discipline; never promoted to accepted. INCONCLUSIVE = failure-to-establish everywhere; ORACLE_CONFLICT blocks all acceptance pending re-derivation.
4. Replay the E1–E7 gate on the frozen evidence chain (rules of K2_INTERFACE_SPEC.md §4); report violations.
5. E7 absence adjudication: ambiguous absence ⇒ PRESENT (REFUTATION_STATE_MODEL.md §3.5).

## 5. False-positive / refutation logging (immutable)
Append-only log of: every accepted RuleCard; every executed refutation (state, counterexample_ref); every false positive (overclaim hit, T2 load-bearing attempt, C6 non-refutation, silent-error finding). Rows carry fact_ids, artifact hashes, adjudicated_at. No edits; supersession = new row.

## 6. Blind-row sampling (rubric item 7)
sample_blind_rows(load_bearing_rows) → SAMPLED_SET
- ≥30% of load-bearing evidence rows independently checked against the blind oracle (FR-6).
- The sampling list (which rows) is custodian-held; draw is deterministic (seeded) but the seed/list never reach the solving Agent before submission. Results go to the scorer only.

## 7. Scorer isolation (S0_GOAL_ADR.md §4)
author (solving Agent) ≠ custodian (A0) ≠ scorer (fresh verifier context). The scorer receives frozen outputs + oracle ONLY after solving completes. If separation is technically impossible in the local Harness: DOWNGRADE the affected metric and record the limitation in the audit receipt — NEVER fake independence (FR-6).

## 8. Audit receipt format (one per round; immutable)
{receipt_id, round{freeze_id, freeze_manifest_hash, cases[]}, what_ran{plant_tests[], scorer_runs[], sampling{ratio, method}}, artifact_hashes{fact_log, rulecards, receipts, scored_verdicts}, actors{author, custodian, scorer}, timestamps ISO-8601 UTC, deviations[], custody_events[]}
