# ROADMAP (S0-revised) — supersedes archive_r7/ROADMAP.md

Strategy: hybrid (D01/D09 as amended by D02R/D07R/D08R/D09R). Architecture label: **FAULT_DISCOVERY_MVP_ARCHITECTURE_V1** (minimum proof architecture — extensible, not final). Acceptance: 10-item rubric (S0_GOAL_ADR §2) + per-detector MUST-PASS (DETECTOR_ADMISSION_MATRIX.md). Project state target: `S0_APPROVED → READY_TO_DESIGN_WAVE1_IMPLEMENTATION` — **the project is NOT DONE**.

## Wave 0 — S0 reconciliation (COMPLETE this turn)
- S0 decisions ratified (Q1 freeze rule, Q2 rubric, T0/T1/T2, per-detector gates, corpus matrix, pragmatic custody, KC-2 split).
- Bookkeeping corrected (false R8-acceptance record; N/M retired; 15-ID finding count authoritative; dangling citation fixed via F-R0-03 + V2-Z3-ZEPHYR-WORKQ evidence note).
- Schema freeze remains open → next step is Wave-1 DESIGN (not implementation).

## Wave 1 — DESIGN (next phase; no code in this task)
1. Close corpus gaps GAP-1/2/3 (WAVE1_CORPUS_MATRIX.md): pin the real public-source fault case; design the negative/no-fault control; reshape ≥1 contract test into a full E2E case (or add an equivalent planted case).
2. Freeze RuleCard schema (FR-1) under the ratified ADR.
3. Build plant-test fixtures for load-bearing detectors G1/G4/G6/G7/G8 (planted BEFORE freeze; hashes recorded).
4. Pre-register KC-2A design (small early diagnostic) and KC-2B protocol (governs large substrate investment only).

## Wave 1 — IMPLEMENTATION (explicitly NOT started)
- Build K1/K2/K3 per FAULT_DISCOVERY_MVP_ARCHITECTURE_V1 (contract SPEC written-not-coded first).
- Plant + freeze corpus cases; run measurement under S0-Q1 freeze discipline and pragmatic custody (author = solving Agent; custodian = A0 with blinded answers; scorer = fresh verifier context post-solving).
- Acceptance gate: ALL 10 rubric items + 100% pass on every load-bearing detector's plant tests. NO aggregate detector score exists.
- Measurements: (a) detector plant tests; (b) blind-oracle coverage ≥30% of load-bearing rows; (c) KC-2A after the first E2E RuleCard path works (diagnostic only); (d) custody-limitation downgrades recorded if Harness separation is impossible.
- Track-B named pull event: raw-evidence export (macro values + single-line bodies) with SUBSTRATE-GAP-EXPORT costing before admission (starvation guard M5 stands).

## Wave 2 (conditional)
- KC-2B preregistered A/B (≥30 real bugs, ≥5 RTOS histories, blind oracles) → governs LARGE substrate investment decisions ONLY.
- S-gated Track-B repairs by measured misses: F-V2-001 → F-V1-003 → F-V1-007 → F-V1-005 → F-V1-001 value surface → F-V1-004. Each: impact statement + 58-case campaign re-run + Level-2 attribution before merge.
- Family-#2 admission review (re-earn events: C5 DAG, callback propagation, dynamic-leg ownership per M9).

## Deferred / not scheduled (fw-context fixes)
Track-B queue: F-V2-001, F-V1-003, F-V1-007, F-V1-005, F-V1-001, F-V1-004. Not scheduled: F-V1-006 (T1-discipline coverage), F-V1-008 (T1 truncation detection covers honesty), F-V2-002 (T1 `_fallback` rejection covers), F-V4-001 (long-session watch, M8). All 15 finding IDs dispositioned: CURRENT_CAPABILITY_MAP.md.

## Non-goals (D10, unchanged)
Standalone product polish; green tests as success; universal static analysis; multi-variant pre-U3; runtime simulation; compiler-oracle MVP layer; LLM free-text rules; goal replacement/silent narrowing; MVP corpus without the registered gaps closed.

## Kill / revisit triggers (S0-revised)
- ANY load-bearing detector failing its plant test ⇒ Wave-1 gate fails (per-detector rule; no aggregate).
- KC-2A shows catastrophic dominance of a simpler evidence path ⇒ substrate-value review BEFORE further substrate work (diagnostic, not verdict; KC-2B decides investment).
- KC-2B grep-arm ≥ substrate-arm ⇒ substrate demoted to cache (D01 revisit).
- S-stability Δ>20 ⇒ narrow corpus invalid.
- Channel representativeness FAIL (MEASURE-GAP-CHANREP) ⇒ per-surface trust re-derivation.
- External replication refutes FAIL_PRODUCT as oracle defects ⇒ gates loosen (D01/D02 revisit).
- Track idle >1 cycle BY CALENDAR ⇒ explicit delete-or-build.
- Two rounds with neither macro-probe miss nor export pull ⇒ delete-or-build on export (M5).
- U3 measured detectable+cheap ⇒ variant machinery re-enters core (D03 revisit).
- Custody separation impossible in-Harness ⇒ affected metrics downgraded and recorded (never faked).
- User amends the ADR ⇒ all scope stamps re-derived.
