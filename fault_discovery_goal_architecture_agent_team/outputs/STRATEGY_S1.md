# STRATEGY S1 — Semantic-Substrate-First ("certify the kernel, then build")

**Thesis.** The Agent is only as good as its evidence. Certify fw-context-mcp as a trusted semantic substrate FIRST (close the semantic-correctness gap to READY on the full 58-case surface), then build the Fault Discovery Agent on certified ground.

## Architecture
- fw-context-mcp as the trusted evidence kernel, full ~30-tool surface, upgraded until every SILENT_DATA_LOSS / WRONG_ANSWER / INDEX_DIVERGENCE finding is fixed or refuted.
- Agent layer built afterwards on top, consuming substrate output as ground-truth-grade facts.
- Agent-side validation per A3's five-check gate and A2's EvidenceGate retained as defense-in-depth.

## Work sequence
1. Substrate repair wave 2: F-V1-003 (variant isolation / per-variant compile_commands), F-V2-001 (CMSIS6 C-form vector recognition), F-V1-001 (macro-value exposure), F-V1-002/007 (single-line + conditional body storage), F-V1-004 (transitive callback registration targets), F-V1-005 (compile_commands-only root set), F-V2-002 (exact=True contract), F-V1-008 (pagination).
2. Full 58-case revalidation campaign to NOT_READY→READY under the same verdict rule.
3. Only then: Agent MVP build (A5 decomposition C0–C8) + A6 evaluation corpus.

## fw-context role
Trusted kernel; all surfaces available without consumer-side distrust machinery.

## Mandatory fixes / deferred
Mandatory: all 8 open findings. Deferred: none. (F-V4-001 watcher assertion rides along.)

## First corpus
Deferred until substrate READY; then A6's planted-fixture corpus + PFC-1.

## Success / kill criteria
Success: full-surface READY verdict + Agent MVP built on certified substrate.
Kill: repair wave reopens regressions or exceeds ~2 wave-equivalents of effort without READY; kill criteria A7 KC-1..KC-4 apply with substrate-fix substitution.

## Local-optimum risk
HIGHEST of the three. Directly reenacts the pattern A1-F3 documents (945 product commits, 0 Agent commits, wave-local success definitions) and the biases A7 quantifies as PRESENT (completion, test-green, sunk-cost, single-corpus, consensus laundering). Contradicts the evidence: A4 classifies only F-V1-003 as BLOCKING_FOR_AGENT_MVP (F-V2-001 roadmap-critical only when vector-class rules enter the corpus); A6 shows the decision-relevant number S is measurable now; A2 shows the MVP needs only surfaces that already PASS. EVIDENCE_RULES test: most of the 8 fixes have no stated fault/rule-discovery impact → not roadmap-critical.

## Evidence base
F-R0-02..05, F-R0-10..13; CONTRADICTIONS C-01/C-04; R1: A1-F3, A2 (MVP fence set), A3 (MVP verdict), A4 (goal-impact table), A5 (zero-substrate-change MVP), A6 (S metric), A7 (zero-sunk-cost NO).
