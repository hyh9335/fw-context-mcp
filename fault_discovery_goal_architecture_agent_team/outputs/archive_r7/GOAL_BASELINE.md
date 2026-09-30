# GOAL BASELINE (R7, A0)

## Authoritative goal (verbatim, immutable)
"构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。"
(Build an Agent that discovers firmware/software fault mechanisms from source code and derives corresponding fault-detection/diagnosis rules.)

This statement is the reference frame for every scope decision. No artifact, wave, or metric may redefine it (A1-I1: 945:0 commit history shows goal substitution by default; A1-F2: "fault discover*" appears in no tracked file before the 09-20 pack).

## Decomposed commitments (goal items)
1. **Mechanism discovery from source** — from C/C++ source (plus build artifacts as evidence), identify concrete fault mechanisms: build/config-conditioned faults, registration/invocation mismatches, resource-bound violations, startup/vector faults, reachability errors, cross-context state propagation.
2. **Rule derivation** — output machine-checkable diagnosis rules (RuleCard class) whose trigger conditions, evidence chain, refutation protocol, and polarity are explicit; rules are deterministic predicates (P-2b), not LLM free text.
3. **Honesty of evidence** — every claim carries provenance, certainty tier, and explicit uncertainty; absence claims are polarity-controlled; unsupported data is refused, not warned about.

## Non-goals (from GOAL_CONTRACT + D10)
fw-context product polish; green tests as success; universal static analysis; multi-variant support pre-U3; runtime queue-semantics simulation; standalone release engineering; goal replacement or silent narrowing (any scope stamp must cite the goal item it serves).

## Acceptance rubric (A1-P1 basis — PENDING S0 USER GATE)
- **R-1**: ≥1 end-to-end case: a fault mechanism planted or real in a target repo, discovered from source by the Agent, producing a RuleCard whose refutation protocol executes and whose evidence chain is independently auditable (blind oracle on ≥30% of evidence rows).
- **R-2**: rule quality floor: on the wave-1 corpus, no RuleCard may be derived from a quarantined surface without labeled degraded provenance; zero silent-error findings on the planted cases.
- **R-3**: harness honesty: evaluation runs under custody split + freeze-before-measure; the S metric is advisory until custody feasibility is ruled (D08).
- **R-4**: goal-record in-repo: an ADR records rubric confirmation, Q1/Q2 answers, and the wave scope stamp (A1-P2), committed before the agent wave starts.

**A1-Q1/Q2 (open, must be answered at the S0 gate)**: Q1 — does the rubric's "discovered from source" require zero product-code modification during the trial run? Q2 — N/M quantification for the rubric's corpus breadth.

## S0 USER GATE (precondition to any implementation wave)
One capped user turn (A8 amendment): confirm/amend the rubric above; answer A1-Q1/Q2; confirm the wave-1 scope stamp (config/condition family). Controller-owned items (custody, corpus construction, KC-2 design) are outside the turn. Output: in-repo goal ADR.

## Bias ledger at decision time (ANTI_ANCHOR, final answers)
- Completion bias: PRESENT, answered by D01 (agent wave, substrate cap).
- Test-green bias: PRESENT, answered by D08 (custody split, hold-one-out).
- Static-analysis maximalism: NOT PRESENT (scope-stamped corpus, D10).
- LLM maximalism: NOT PRESENT (rules are deterministic predicates).
- Sunk-cost bias: PRESENT, answered: zero-sunk-cost = NO to rebuilding, YES to re-scoping — the kind survives (evidence kernel + disciplined agent), the scope does not (D01 keep-list).
- Premature-MVP bias: WATCHED — S-narrow adopted with honest loss recorded (call-graph differentiator untested in wave 1) and scope-stamp guard against silent narrowing (D-6).
- Single-corpus bias: PRESENT, answered by GA-2 round-2 plant + ≥20% non-commit GT + field-log case.
- Consensus laundering: PRESENT, answered by S0 external gate + blind-oracle floors + external replication (D08).

## Contradiction rulings carried into baseline
- C-01: both verdicts scope-true; readiness = hypothesis pending V6-disciplined trial (D02).
- C-03: honest-empty (asm form) vs false-missing (CMSIS6 C form) are distinct; residual rule: no "no interrupt surface" conclusion from empty get_vector_table without corroboration (D05).
- C-04: fix-impact rule binds all substrate work (D01 cap).
