# STRATEGY S2 — End-to-End MVP First ("build the Agent as a skeptic; repair only what blocks it")

**Thesis.** The top-level goal has 0% Agent progress; the substrate already covers the MVP's needed surfaces on its PASSING set. Build the end-to-end Agent NOW with an evidence-discipline wrapper that treats fw-context as UNTRUSTED draft+provenance, run an independent verification channel from day one, and repair the substrate only where measured Level-2 misses prove it blocks the goal.

## Architecture (A5 decomposition, hardened per A7)
- C0 Orchestrator; C1 Build&Provenance Service; C2 Evidence Extractor (fw-context + independent channel: grep/rg raw-disk sweeps, `clang -dM -E` for macro truth, per-variant rebuild when variants matter); C3 FactGraph; C4 Hypothesis Engine (catalog proposes — A2 Template→QueryBundle→EvidenceGate→Record); C5 Causal/Evidence Graph; C6 Rule Synthesizer (A3 RuleCard: rule_type, assertion_polarity, per-claim certainty tuples, refutation protocol); C7 Refutation&Validation Harness; C8 Knowledge Output/Review Gate.
- Agent-side guards G1–G7 (A5) operationalize the fences: G1 provenance gate (F-V1-003 → MVP BANS bare-generator multi-variant; single-context per index), G2 callback uncertainty (F-V1-004 → may-targets are lower bounds), G3 dead-code root attestation (F-V1-005), G4 single-line grep fallback (F-V1-007), G5 vector two-form cross-check (F-V2-001/C-03), G6 existence re-verification (F-V2-002 `_fallback`), G7 chunked enumeration (F-V1-008).
- Polarity asymmetry (A3) as the FP-control law: exact evidence licenses positives; absence claims require multi-surface + possible-edge + coverage corroboration; sets are bounds, not closures.

## Work sequence
1. Agent MVP skeleton + distrust harness + evidence-discipline wrapper (A4) — the first Agent code is the verification harness (A7).
2. A6 evaluation corpus: 12 planted dev fixtures (existing golden bases) + 4 FP probes + sealed holdout; scenario oracles extended per A6 verdicts.
3. First end-to-end case on a PASSING surface (OD-4 converge: PFC-1 workq / unhandled-IRQ / config-gated-missing-init).
4. Measure substrate-sufficiency S (A6 OQ10; A7 KC-2 arm-baseline optional if budget allows).
5. Substrate repair ONLY where S-attributed misses are substrate-missing: expected F-V1-003 fix or permanent one-context rule; F-V2-001 before any vector-class corpus entry; raw-evidence export (macros, single-line bodies) as the highest-leverage/lowest-LOC interface-decay repair (A7-Q3, A5 REPLACEABLE tier).

## fw-context role
Untrusted evidence provider; NECESSARY/OPTIONAL/REPLACEABLE tiers per A5's capability map; all output passes G1–G7.

## Mandatory fixes / deferred
Mandatory: none to start; S-gated: F-V1-003 (or ban), F-V2-001 (before vector class). Deferred: F-V1-001/002/004/005/006/007/008, F-V2-002, F-V4-001 as product fixes — each converted to a bounded operating rule instead (A4's wrapper converts 6 degrading findings without product changes).

## First corpus
12 planted fixtures + 4 FP probes + sealed holdout (A6); first case per OD-4.

## Success / kill criteria
Success (A1-P1 rubric): ≥N source-derived fault hypotheses with evidence links + causal chain + conditions + confidence/limits; reproducible tool-query evidence chains (no silent log-mining); ≥M auditable hypothesis→rule conversions; FP control measured on labeled fixtures with independent oracle; S ≥ 80% on dev set.
Kill: A7 KC-1 (macro-exposure deadline), KC-3 (>10% substrate false negatives → zero trust for negatives), KC-4 (C-01 unadjudicated at R7); premature-MVP tripwire: if dev-set S < 50%, the guard set is insufficient → re-scope (NOT silently push substrate fixes).

## Local-optimum risk
Moderate: premature-MVP bias (guarded by S-metric tripwire + planted-fixture FP probes); single-corpus overfitting (guarded by sealed holdout + public bug-fix commits layer); the strategy inherits substrate interface-decay risk until the export fix lands.

## Evidence base
F-R0-13 (passing surfaces suffice), F-R0-18 (no Agent code exists), C-04; R1: A1-P1/P2, A2 (operational definition, fences, FaultMechanismRecord), A3 (polarity law, RuleCard), A4 (only F-V1-003 blocking; wrapper), A5 (9 components, guards, delegation map), A6 (evaluation design, S, PFC-1), A7 (skeptic harness, kill criteria), A8 (kernel-kind survives, defer variant/compare).
