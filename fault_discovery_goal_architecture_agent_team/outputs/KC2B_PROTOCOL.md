# KC2B PROTOCOL — Preregistered substrate A/B (Wave-2 investment decision gate)

Status: pre-registered NOW, executed later (Wave-2). Per S0_GOAL_ADR.md §3 and FAULT_DISCOVERY_REQUIREMENTS.md FR-6: KC-2B governs LARGE substrate investment decisions; it does NOT gate the first E2E RuleCard and does not block WAVE1_IMPLEMENTATION_PLAN.md steps 1–9.

## 1. Preserved requirements (verbatim commitments from FR-6 / S0)
- ≥30 real bugs (total, across arms).
- ≥5 RTOS histories represented in the pool.
- Blind oracle throughout: custodian-held; hashes public; content hidden (CORPUS_LOCK_PLAN.md §2.5 discipline, scaled to the larger pool).
- Preregistered margin: §3 is fixed BEFORE execution opens; no post-hoc threshold moves.
- Decision gate: the outcome decides whether substrate investment beyond the Wave-1 load-bearing set (G1/G4/G6/G7/G8) proceeds.

## 2. Design
- Arms: S = substrate-assisted (K1-mediated fw-context + independent channels; the K1/K2/K3 stack as built in Wave-1); N = grep/rebuild + clang/LLM (same definition as KC2A_PREREG.md Arm N).
- Assignment: balanced, stratified by RTOS history and fault family; same pool per arm; N ≥ 30 total (≥15 per arm; larger if the pool allows).
- Scoring: K3 scorer + blind-row sampling ≥30% (K3_INTERFACE_SPEC.md §6); custody per S0_GOAL_ADR.md §4; freeze per FREEZE_MANIFEST_SPEC.md.
- Metrics (pre-registered): primary = correct-mechanism-ID rate (blind); secondary = time-to-evidence, silent-error count, rule survival rate (NOT_REFUTED_WITHIN_SCOPE per REFUTATION_STATE_MODEL.md), per-family breakdown.

## 3. Preregistered margin (fixed before execution opens)
The binding thresholds are written HERE at execution preregistration and hashed into the Wave-2 freeze before the first case runs; they may not move afterwards. Registration template with the provisional binding values:
- GO (substrate investment proceeds): S−N ≥ +6 correct-mechanism-ID cases of 30 (≥20% absolute) AND S silent-error count ≤ 3.
- NO-GO (substrate capped at the Wave-1 load-bearing set): |S−N| ≤ 2, or S silent errors ≥ N+3.
- Intermediate results ⇒ decision DEFERRED with a written analysis; no incremental substrate work until decided.

## 4. External replication tie-in (FR-8)
A 10-case external replication arm (independent environment/executor, same protocol shape) runs adjacent to KC-2B. It is REQUIRED before any STRONG GENERALIZATION claim; it is NOT required for producing RuleCards or for Wave-1 acceptance.

## 5. Sequencing and non-goals
- Wave-2; no KC-2B infrastructure build before the Wave-2 gate decision (evaluation governance must not become another infrastructure local optimum — S0_GOAL_ADR.md §3).
- KC-2B results feed the extensibility decisions of FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md (family #2, multi-variant, vector/startup) — each still requires its own re-earn event or user-visible scope stamp.
- No mid-run changes to arms, pool, oracles, or thresholds (S0-Q1 discipline; S0_GOAL_ADR.md §1).
