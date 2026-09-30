# GOAL BASELINE (S0-revised, A0) — supersedes archive_r7/GOAL_BASELINE.md

## Authoritative goal (verbatim, immutable)
"构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。"
(Build an Agent that discovers firmware/software fault mechanisms from source code and derives corresponding fault-detection/diagnosis rules.)

This statement is the reference frame for every scope decision. No artifact, wave, or metric may redefine it (A1-I1 drift history; A1-F2). fw-context-mcp is a subordinate evidence provider, not the goal.

## Decomposed commitments (goal items)
1. **Mechanism discovery from source** — concrete fault mechanisms: build/config-conditioned faults, registration/invocation mismatches, resource-bound violations, startup/vector faults, reachability errors, cross-context state propagation.
2. **Rule derivation** — RuleCards: deterministic predicates, explicit trigger conditions, evidence chain, provenance/certainty, scope stamp, executable/refutable refutation protocol.
3. **Honesty of evidence** — provenance + certainty tier + explicit uncertainty on every claim; polarity-controlled absence claims; unsupported data refused, not warned about.

## S0-Q1 freeze discipline (anti-test-time-adaptation, ratified)
Once measurement begins on a frozen case: target source, oracle, frozen Agent implementation, and frozen fw-context implementation MUST NOT be modified to make the case pass. Planted faults go in BEFORE freeze; development happens BETWEEN rounds; every change ⇒ new freeze/hash + new round. (Full text: S0_GOAL_ADR.md §1.)

## Non-goals (GOAL_CONTRACT + D10, unchanged)
fw-context product polish; green tests as success; universal static analysis; multi-variant support pre-U3; runtime queue-semantics simulation; standalone release engineering; goal replacement or silent narrowing (scope stamps cite goal items, never redefine them).

## Wave-1 MVP acceptance rubric (S0-Q2 — replaces the retired undefined N/M)
All ten are MINIMUM requirements (full text S0_GOAL_ADR.md §2; case-level mapping WAVE1_CORPUS_MATRIX.md):
1. ≥4 end-to-end evaluation cases;
2. ≥1 real/public-source fault case;
3. ≥1 planted blind case (oracle hidden from the solving Agent);
4. ≥1 case intentionally touching a currently quarantined (T2) surface;
5. ≥1 negative/no-fault case (correct result = rejection/refutation);
6. ≥2 independent repository/fixture lineages;
7. ≥30% of load-bearing evidence rows blind-oracle checked;
8. zero silent unsupported claims on accepted RuleCards;
9. every Wave-1 load-bearing detector passes 100% of its mandatory plant tests (per-detector; no aggregate);
10. RuleCard completeness per goal item 2.

An E2E case counts only if it executes `source evidence → mechanism hypothesis → RuleCard → refutation`. Tool contract tests count only when reshaped into that chain.

## KC-2 positioning (S0-Q2)
KC-2A (small early diagnostic, after first E2E RuleCard path works) ≠ KC-2B (preregistered ≥30 bugs / ≥5 RTOS histories A/B governing large substrate investment). Neither gates the FIRST E2E RuleCard. Anti-self-grading intent (D08) preserved via pragmatic custody (S0_GOAL_ADR §4).

## Bias ledger at S0 (ANTI_ANCHOR, standing answers)
- Completion bias: answered (agent-first wave, substrate cap).
- Test-green bias: answered (custody + freeze discipline + blind oracles; S0-Q1 rule).
- Static-analysis maximalism / LLM maximalism: not present (scope-stamped corpus; deterministic predicates).
- Sunk-cost bias: answered (kind survives, scope re-derived; substrate cap stands).
- Premature-MVP bias: watched — 10-item rubric is the floor, not a license; scope stamps guarded (M6).
- Single-corpus bias: answered (≥2 lineages, real + planted + negative mix, blind rows).
- Consensus laundering: answered (user-ratified rubric = externally-owned success criteria; scorer = fresh verifier context; external replication before strong generalization claims).

## Contradiction rulings carried into baseline (unchanged)
- C-01: both verdicts scope-true; readiness = hypothesis pending a V6-disciplined measurement event.
- C-03: honest-empty (asm form) vs false-missing (C form) distinct; no "no interrupt surface" conclusion from empty results without corroboration.
- C-04: fix-impact rule binds all substrate work.
