# FAULT DISCOVERY REQUIREMENTS (S0-revised) — supersedes archive_r7/FAULT_DISCOVERY_REQUIREMENTS.md

Requirements for the Agent system. Sources: R1 (A2/A3), R4 stress, R5 red team, R6 revisions, S0 user decisions. Governing rubric: S0_GOAL_ADR.md §2 (10-item Wave-1 acceptance).

## FR-1 RuleCard contract (A2 FaultMechanismRecord + A3 RuleCard, unified)
Fields: mechanism_id; fault_class; trigger_conditions (deterministic predicates, polarity-tagged); evidence[] (FactRecords: surface, trust tier T0/T1/T2, query, provenance, certainty); refutation_protocol (two-outcome: rule_falsified | oracle_falsified); polarity (positive/negative/lower_bound); validation_status; oracle_provenance (blind/commit/field-log); scope_stamp (goal item + corpus). Schema freeze AFTER this ADR (sequencing per ROADMAP Wave 0→1). Rubric items 8/10 bind acceptance.

## FR-2 Evidence discipline (E1–E7, restated over T0/T1/T2)
- E1 evidence-with-provenance only; substrate output never ground truth — including T0 (trusted-within-scope ≠ ground truth).
- E2 T2-surface claims require labeled degraded provenance and are never load-bearing; T1 claims flow ONLY through K1 (never raw); T0 claims stay inside their named scope (out-of-scope use drops to the stricter class).
- E3 cross-context claims via recorded query chains; single-line/fragment claims ≥2-surface cross-check (T2 class F-V1-007).
- E4 reachability negatives carry root-set attestation (G3 discipline).
- E5 ANY vector-table claim requires ≥2-surface cross-check + form attestation; no absence conclusion from empty results without corroboration (C-03).
- E6 macro-value evidence is G8-policed, labeled degraded, never load-bearing.
- E7 absence/polarity: negative claims enumerate attempts; ambiguous absence ⇒ PRESENT for scoring (anti-sunk-cost default).

## FR-3 Substrate contract spec (design-time, written not coded)
refusal-not-warn; exact-no-fallback (T1 enforcement via `_fallback` rejection); deterministic-enumeration + explicit truncation (T1); unsupported-data-refusal; out-of-band build-manifest diff (G1 instrument); run-pinned explain_condition context (T1). Each row names its detector + plant test + expiry (DETECTOR_ADMISSION_MATRIX.md).

## FR-4 Detector admission (REVISED per S0 — no aggregate)
Per-detector matrix with protected claim, load-bearing flag, plant tests, pass/fail conditions, fail behavior, expiry: DETECTOR_ADMISSION_MATRIX.md. **Every Wave-1 load-bearing detector (G1, G4, G6, G7, G8) MUST pass 100% of its mandatory plant tests. No aggregate score exists; the former ≥6/7 is retired.** Non-load-bearing detectors (G2, G3, G9) measured + reported, non-blocking.

## FR-5 Rule family scope (Wave 1, unchanged in kind)
Condition/config family (R3a), direct-flow presence (R1), existence/absence (R4), R6 preconditions. R3b cross-variant blocked (GA-4). H2/H3 deferred with re-earn events. C5 hypothesis-DAG deferred; family library as K2 data.

## FR-6 Evaluation (S0-revised)
- Rubric items 1–10 (S0_GOAL_ADR §2) are the acceptance gate; case mapping in WAVE1_CORPUS_MATRIX.md.
- Custody (pragmatic, ratified): author = solving Agent; custodian = A0/controller holding blinded expected answers; scorer = fresh verifier context receiving frozen outputs + oracle only after solving; hashes/timestamps before measurement. If Harness separation is technically impossible: downgrade the affected metric and record it — never fake independence.
- ≥30% load-bearing evidence rows blind-oracle checked; commit-derived GT ≤80%; ≥1 field-log-derived case (may land in Wave 2 if no Wave-1 case needs it — registered, not silently dropped).
- Freeze discipline per S0-Q1 (anti-test-time-adaptation).
- KC-2A (small early diagnostic, post-first-E2E-path) / KC-2B (preregistered ≥30 bugs, ≥5 RTOS histories, blind-oracle A/B) — KC-2B governs large substrate investment; NEITHER gates the first E2E RuleCard.
- S metric: advisory until custody separation is demonstrated in-Harness; with pragmatic custody ratified, S may gate Wave-1 acceptance items only where the scorer/custodian separation actually held.
- 58-case campaign re-runs per fix-wave (unchanged).

## FR-7 Runtime-facing honesty (unchanged)
Queue/resource mechanisms: facts (bounds, producers, consumers, drop paths) + forbidden-overclaims list. No simulation claims. (Minority M1 preserved: R-O rules remain goal-central at contract level.)

## FR-8 Independence (revised)
Independent channels (grep/rg raw-disk; WSL clang -E instrument) cross-check load-bearing claim classes. Channel representativeness (MEASURE-GAP-CHANREP) remains the top open risk. External replication required before STRONG GENERALIZATION claims — not before the first RuleCard. 10-case external replication remains a KC-2B-adjacent preregistered measurement.

## FR-9 Sequencing (revised)
S0 ADR ratified (this turn) → schema freeze → Wave-1 design (NOT started in this task) → Wave-1 build gated by the 10-item rubric + per-detector MUST-PASS. No substrate wave before the first E2E case (D01 cap). Architecture = FAULT_DISCOVERY_MVP_ARCHITECTURE_V1 (minimum proof architecture, extensible — see that document).
