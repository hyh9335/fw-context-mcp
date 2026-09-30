# FAULT DISCOVERY REQUIREMENTS (R7, A0)

Requirements for the Agent system, derived from R1 (A2/A3), stress (R4), red team (R5), and revisions (R6). Each requirement cites its evidence basis.

## FR-1 Rule output contract (A2 FaultMechanismRecord + A3 RuleCard, unified per UQ-6)
Every discovered fault yields a RuleCard with: mechanism_id; fault_class (wave-1: condition/config family); trigger_conditions (evidence-cited, polarity-tagged); evidence[] (FactRecords: surface, query, provenance tier, certainty); refutation_protocol (two-outcome: rule_falsified | oracle_falsified — FR-R1 precedent); polarity (positive/negative/lower_bound per A3 asymmetry); validation_status; oracle_provenance (blind/commit/field-log); scope_stamp (goal item + corpus). Preconditions: UQ-2 rubric N/M (S0 gate) and UQ-3 boundary ruling (D06/D01) before schema freeze.

## FR-2 Evidence discipline (wrapper = A4 + A2 fence set, re-tiered per R6)
- E1 evidence-with-provenance only; substrate output never ground truth (AG-5).
- E2 no claim from a quarantined surface without labeled degraded provenance (Tier-2 list in CURRENT_CAPABILITY_MAP).
- E3 cross-context claims via recorded query chains; single-line sub-context claims ≥2-surface cross-check (F-V1-007).
- E4 reachability claims carry root-set attestation (F-V1-005/G3).
- E5 ANY vector-table claim requires ≥2-surface cross-check (adopted over G5's empty-only trigger); no absence conclusion from empty results without corroboration (C-03).
- E6 macro-value evidence is G8-guarded, labeled, never load-bearing (UQ-3 ruling).
- E7 absence/polarity rules: negative claims enumerate attempts; ambiguous absence ⇒ PRESENT for scoring (A6 anti-sunk-cost default).

## FR-3 Substrate contract spec (design-time, written not coded — D06)
refusal-not-warn (F-R0-06/G1); exact-no-fallback lookup (F-V2-002/G6); deterministic enumeration + explicit truncation (F-V1-008/G7); unsupported-data-refusal; out-of-band build-manifest diff as the provenance instrument (A4 UQ-1); explain_condition context pinning (F-R0-15). Each row names its detector and plant test; detectors carry expiry dates (A5 PC-2).

## FR-4 Detector set (K3 checklist, each with measured-miss citation)
G1 variant provenance (out-of-band diff) · G2 lower-bound discipline (transitive callbacks) · G3 root-attestation (dead-code) · G4 shape-based trigger (cross-context one-liners) · G5 superseded by E5 · G6 exact-lookup · G7 truncation visibility · G8 macro-observability (GA-1) · G9 slot-classification (GA-1/G10).
Admission: plant-test with the 7 FAIL_PRODUCT patterns + GA-1 macro probe + G10 slot probe; ≥6/7 required (A7 flip-evidence threshold).

## FR-5 Rule family scope (S-narrow, wave 1)
Wave-1 families: R1 (direct-flow presence), R4 (existence/absence), R3a (within-context condition flips), R6 preconditions (A3 numbering; "config-class R2" in R5/R6 language = A3's R3a — numbering fork settled: A3 taxonomy is canonical). R3b (cross-variant) blocked by GA-4. H2/H3 (callback, queue) deferred with re-earn events. C5 hypothesis-DAG deferred; K2 keeps the family library as data with missing-evidence lists feeding K3 (A5 objection preserved).

## FR-6 Evaluation requirements (D08, A6 protocol)
Custody split author≠custodian≠scorer; freeze-before-measure hash-logged; hold-one-out on the S rubric; ≥30% blind GT rows; ≤80% commit-derived; ≥1 field-log case; absence adjudication with ambiguity⇒PRESENT; S advisory until custody ruled; S-stability run before any S-gated decision; KC-2 A/B first measurement; 58-case campaign re-runs per fix-wave.

## FR-7 Runtime-facing honesty (GA-3, D10 non-goal)
For queue/resource mechanisms: emit facts (bounds, producers, consumers, drop paths) + forbidden-overclaims list ("runtime exhaustion cannot be concluded from source"). No simulation claims.

## FR-8 Independence requirements (V5/V6/O6)
Independent channels (grep/rg raw-disk scan; WSL clang -E as instrument) used for cross-checks on every load-bearing claim class; independent-channel representativeness (gap-3) is the top open risk (A5); 10-case external replication; holdout custody split.

## FR-9 Sequencing requirements (D09)
S0 user gate → auditability-fork ruling (A8 D-4) + schema freeze → wave-1 build (K1→K2→K3). RuleCard freeze only after S0. No substrate wave before first e2e case (D01 cap).
