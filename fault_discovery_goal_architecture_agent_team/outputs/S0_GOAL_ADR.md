# S0 GOAL ADR — Wave-1 Acceptance Contract (ADR-001)

Status: RATIFIED by user S0 decision (this reconciliation turn). Supersedes the "pending S0 gate" placeholders in R7 artifacts. Preserves the authoritative goal verbatim:

> 构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。

fw-context-mcp remains a subordinate evidence provider, not the project goal.

## 1. S0-Q1 — Meaning of "discovered from source" (anti-test-time-adaptation rule)

For any frozen evaluation case:

1. Once measurement begins for that case, the following MUST NOT be modified to make the case pass: the target case source, the evaluation oracle, the frozen Agent implementation, the frozen fw-context implementation.
2. Planted faults may be introduced BEFORE freeze.
3. Development changes are allowed BETWEEN evaluation rounds.
4. After any change: new freeze/hash + new evaluation round. A changed artifact re-runs the round under the new hash.

This is an anti-test-time-adaptation rule, NOT a prohibition on normal development. Measurement-time adaptation in any form (prompt tweaks, oracle inspection, code patches mid-round) voids the round.

## 2. S0-Q2 — Wave-1 MVP acceptance rubric (replaces undefined N/M)

The symbols N/M are RETIRED everywhere. Wave-1 MVP acceptance requires AT MINIMUM all ten:

1. ≥ **4 end-to-end evaluation cases**;
2. ≥ **1 real/public-source fault case**;
3. ≥ **1 planted blind case** whose oracle is not visible to the solving Agent;
4. ≥ **1 case intentionally touching a currently quarantined (T2) evidence surface**;
5. ≥ **1 negative / no-fault case** where the correct result is rejection/refutation rather than rule creation;
6. ≥ **2 independent repository / fixture lineages** across the Wave-1 corpus;
7. ≥ **30% of load-bearing evidence rows** independently checked by blind oracle;
8. **zero silent unsupported claims** on accepted RuleCards;
9. every detector load-bearing for the Wave-1 architecture MUST pass its mandatory plant tests (see DETECTOR_ADMISSION_MATRIX.md — per-detector MUST-PASS, no aggregate);
10. RuleCards contain deterministic predicates, evidence chain, provenance/certainty, scope stamp, and an executable/refutable refutation protocol.

An E2E case counts only if it genuinely executes `source evidence → mechanism hypothesis → RuleCard → refutation`. Tool contract tests do not count as E2E cases unless reshaped to execute that chain (WAVE1_CORPUS_MATRIX.md adjudicates each candidate).

## 3. KC-2 positioning (S0-Q2 clarification)

The large-scale KC-2 experiment (≥30 real bugs, ≥5 RTOS histories, substrate vs grep/rebuild/clang/LLM A/B, external replication) is NOT the gate for producing the first E2E RuleCard.

- **KC-2A** (small early diagnostic): after the first E2E RuleCard path works — bounded comparison sufficient to expose catastrophic dominance of a simpler evidence path. Diagnostic, not an architecture verdict.
- **KC-2B** (full architecture comparison): preregistered ≥30 bugs / ≥5 RTOS histories / blind-oracle A/B. Governs LARGE future substrate investment decisions.

D08's anti-self-grading intent is preserved (custody, blind oracles, freeze-before-measure); evaluation-governance construction must not become another infrastructure local optimum.

## 4. Custody decision (pragmatic, Wave 1)

- **author**: implementation/solving Agent;
- **custodian**: A0/controller (or separate context) holding blinded expected answers;
- **scorer**: fresh verifier context that receives frozen outputs and the oracle only after solving completes;
- immutable hashes/timestamps recorded before measurement.

If separation is technically impossible in the local Harness: downgrade the affected metric, record the limitation explicitly, do NOT fake independence. External replication remains required before strong generalization claims — not before producing the first RuleCard.

## 5. Trust model (ratified)

Three classes replace the two-tier terminology — full adjudication in CURRENT_CAPABILITY_MAP.md, admission gates in DETECTOR_ADMISSION_MATRIX.md:

- **T0** directly trusted WITHIN A NAMED SCOPE (validated semantics, no contradicting defect, explicit scope; still carries provenance — trusted never means ground truth);
- **T1** gateway-safe / fail-closed (raw surface has a known defect; K1 deterministically detects and refuses/repairs/marks; T1 claims used ONLY through K1);
- **T2** quarantined (defects not safely compensable by a deterministic wrapper for the intended claim).

Detector admission rule: every Wave-1 load-bearing detector MUST PASS 100% of its mandatory plant tests. There is NO aggregate score (the former "≥6/7" is retired everywhere). Non-load-bearing detectors are measured and reported without blocking Wave 1.

## 6. Architecture label

The K1/K2/K3 shape is ratified as **FAULT_DISCOVERY_MVP_ARCHITECTURE_V1** — the MINIMUM architecture for proving `source → evidence → mechanism hypothesis → RuleCard → refutation`. It is NOT a claim that the total Fault Discovery Agent architecture is complete. Later evidence may add/restructure: runtime/dynamic evidence, state/data propagation, callback-heavy family #2, multi-variant analysis, vector/startup analysis, other fault families (see FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md §Extensibility; Minority M1/M9 preserved).

## 7. Process-state correction (audit trail)

The prior controller record claiming R8 APPROVED_WITH_OPEN_DECISIONS was user-accepted and the project DONE was INVALID — user acceptance had not occurred at that time. Legal pre-S0 state: `R8_APPROVED_WITH_OPEN_DECISIONS → AWAITING_USER_S0`. R0–R8 remain valid; originals archived under `outputs/archive_r7/`. This ADR + the present user authorization constitute the genuine S0 act. Target final state: `S0_APPROVED → READY_TO_DESIGN_WAVE1_IMPLEMENTATION` (NOT DONE).

## 8. Scope stamp (Wave 1)

Wave-1 rule families: condition/config (A3 taxonomy R3a), direct-flow presence (R1), existence/absence (R4), R6 preconditions — cited against goal items 1–3, never redefining them. Corpus per WAVE1_CORPUS_MATRIX.md. Non-goals unchanged (D10).
