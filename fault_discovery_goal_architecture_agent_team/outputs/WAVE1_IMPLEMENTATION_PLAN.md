# WAVE1 IMPLEMENTATION PLAN — build order, tests, gates

Governing: S0_GOAL_ADR.md (§1 freeze, §2 rubric, §4 custody), FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md, FAULT_DISCOVERY_REQUIREMENTS.md FR-3/FR-4/FR-9, DETECTOR_ADMISSION_MATRIX.md, CORPUS_LOCK_PLAN.md, WAVE1_CORPUS_MATRIX.md, and the four interface specs (K1/K2/K3_INTERFACE_SPEC.md, FREEZE_MANIFEST_SPEC.md). Default order below; revisable only with recorded justification. Module namespace `wave1/` — new code only, NO fw-context product changes (§4).

## 1. Implementation order + per-step contracts

### Step 1 — schemas + freeze plumbing
Modules: `schemas/schema_harness` (JSON-schema validation of FACT_RECORD_SCHEMA.json / MECHANISM_HYPOTHESIS_SCHEMA.json / RULECARD_SCHEMA.json + REFUTATION_STATE_MODEL.md §5 conformance), `schemas/canonical_json` (FREEZE_MANIFEST_SPEC.md §1 serialization), `freeze/manifest_tool` (build + verify manifests, freeze_id minting). Dependencies: none.
RED tests (write FIRST): T01 FactRecord missing freeze_id rejected; T02 RuleCard with empty forbidden_overclaims rejected; T03 refutation_result REFUTED with protocol_completed=false rejected (§5 allOf); T04 canonical JSON key-sort/LF/no-trailing-whitespace deterministic; T05 combined-hash recomputation reproduces the CORPUS_LOCK_PLAN.md §5 baseline recipe; T06 any byte change ⇒ hash change + new freeze_id required.
Integration: validate existing corpus/oracle artifacts (custodian side).

### Step 2 — K1
Modules: `k1/gateway` (the 7 surface calls of K1_INTERFACE_SPEC.md §2), `k1/factlog` (append-only store + replay), `k1/tier_rules` (T0 named scopes + auto-downgrades), `k1/detectors` (G1/G4/G6/G7/G8 hooks). Dependencies: step 1; a pinned build snapshot per round.
RED tests: T07 `_fallback` row rejected + near-miss list NON-EVIDENCE (PT-G6 hazard shape); T08 truncation ⇒ truncated_lower_bound + limit metadata (PT-G7 shape); T09 single-line body ⇒ T2 + partial_cross_checked, load-bearing blocked; T10 macro-value ⇒ degraded + non-load-bearing + conflict labeled (PT-G8 shape); T11 cross-variant query REFUSED (PT-G1a shape); T12 fact-log immutability (no update path) + replay identity of exact_query.
Integration: K1 ↔ manifest_tool (freeze_id stamping).

### Step 3 — K3 minimal audit/plant infrastructure
Modules: `k3/custody` (solver-bundle assembler + custodian vault + leakage guard), `k3/plant_runner` (EXPECTED.json comparison, K3_INTERFACE_SPEC.md §3), `k3/audit_log` (receipts, FP/refutation log, blind sampler). Dependencies: step 2 (runner drives K1).
RED tests: T13 manifest hash mismatch refuses the round; T14 solver bundle contains ZERO custodian-only paths (CORPUS_LOCK_PLAN.md §4 list); T15 plant runner FAIL on fail_condition match, PASS on pass_condition; T16 blind sampler ≥30% with custodian-held list.
Integration (after K1+K3): **PT-G1/G4/G6/G7/G8 fully executable** — MUST-PASS gate runs (acceptance measurement still blocked).

### Step 4 — K2
Modules: `k2/family_library` (data loader), `k2/evidence_gate` (E1–E7), `k2/loop` (bounded missing-evidence loop), `k2/rulecard_gen`. Dependencies: steps 1–3.
RED tests: T17 E2 refusal of T2 load-bearing; T18 E6 macro-value decisive refusal; T19 abstention after loop bound with missing_evidence ≥1; T20 two-part C1 separation — runtime occurrence only as missing_evidence channel runtime_unavailable + forbidden_overclaims non-empty.
Integration (after K2): **C1 runnable E2E** (solve → freeze → score).

### Steps 5–9 — case campaign
| Step | Runs | Gate |
|---|---|---|
| 5 | first C1 E2E (QueryBundle+K1 → K2 → freeze → K3 score) | full rubric chain executes; abstention legal outcome |
| 6 | C4/C6 pair (freeze order per CORPUS_LOCK_PLAN.md §1: C4 then C6; A/B randomized custodian-side) | C6 = (REFUTED, rejected) scored success; C4 partial-blind rows |
| 7 | C5 T2 tripwire | ZERO T2 load-bearing rows; success via T0/independent channels only |
| 8 | acceptance run | rubric items 1–10 (S0_GOAL_ADR.md §2); blind sampling ≥30% executed |
| 9 | KC-2A diagnostic | KC2A_PREREG.md; bounded, non-blocking |

Which cases can run after each component: after step 1 — corpus/oracle artifact validation only; after step 2 — K1-level PT assertions in unit tests; after step 3 — PT-G1/G4/G6/G7/G8 executable; after step 4 — C1; after steps 5–7 — full E2E set C1/C4/C5/C6; after step 8 — acceptance claims; step 9 — KC-2A.

## 2. Stop / rollback conditions
- Any load-bearing plant test (G1/G4/G6/G7/G8) FAILING ⇒ STOP; fix the detector or re-tier the surface per DETECTOR_ADMISSION_MATRIX.md fail behavior; re-run the FULL MUST-PASS set. No aggregate workaround, ever.
- Oracle leakage into solver context ⇒ round VOIDED (S0_GOAL_ADR.md §1); re-freeze + new round.
- Freeze hash mismatch at any checkpoint ⇒ re-freeze + new round.
- Substrate cap rule (D01, FR-9): total substrate-side work ≤ 1 wave-equivalent UNTIL the first E2E RuleCard lands (end of step 5); enforced as a plan gate checked before each step; exceeding requires a recorded user-approved waiver, else stop.

## 3. Context budget / Agent ownership (S0_GOAL_ADR.md §4)
- Solving Agent receives: corpus snapshot/fixture + CASE_DESIGN.md + redacted brief + QueryBundle + K1 access ONLY. Never: FAULT_ORACLE.json, PLANT_DIFF.md, PLANT_SPEC/EXPECTED.json answers, SOURCE_LOCK.json, PAIRING_NOTE.md, the A/B mapping, or the blind sampling list.
- Custodian (A0): oracles, A/B mapping, sampling list, freeze execution.
- Scorer: fresh verifier context; frozen outputs + oracle only after solving (K3_INTERFACE_SPEC.md §7).

## 4. NO hidden fw-context repair (explicit clause)
All product changes remain S-gated Track-B, pulled by measured misses (FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md; CURRENT_CAPABILITY_MAP.md substrate bounding). This plan contains NO fw-context product-code work. C5 success MUST remain reachable via honest T0/independent channels — repairing the substrate to pass C5 voids the round.
