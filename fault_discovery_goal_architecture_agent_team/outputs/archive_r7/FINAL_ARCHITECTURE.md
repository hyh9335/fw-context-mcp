# FINAL ARCHITECTURE (R7, A0)

## System shape (evidence-led convergence: A8 proposal → A5 adoption → all-role revision)

```
┌─────────────────────────────────────────────────────────────┐
│ GOAL ADR (S0 gate output) — rubric + Q1/Q2 + scope stamps    │
└─────────────────────────────────────────────────────────────┘
┌──────────────────────┐   ┌──────────────────────────────┐
│ K1 EVIDENCE GATEWAY  │   │ K3 AUDIT-AND-FP HARNESS      │
│ (C1+C2+C3)           │   │ (C7 [+C8 as artifact contract,│
│ immutable fact log   │   │  review gate preserved]      │
│ fw-context queries   │   │ custody split · freeze-hash  │
│ grep/rg raw-disk     │   │ plant-tests · guard expiry   │
│ WSL clang -E instr.  │   │ S-audit + absence adjud.     │
│ build-manifest diff  │   │ two-outcome refutation log   │
│ (G1 instrument)      │   └──────────────────────────────┘
│ detectors G1-G9 wired│              ▲ metrics/corpus
└──────────┬───────────┘              │
           ▼ facts                    │
┌─────────────────────────────────────┴───────────────────────┐
│ K2 HYPOTHESIS-AND-RULE LOOP (C4+C6; C5 = evidence-link list) │
│ family library AS DATA (missing-evidence lists → K3)         │
│ wave-1: condition/config family (R1, R3a, R4, R6-precond)    │
│ RuleCard output per FR-1 (deterministic predicates)          │
│ C5 DAG deferred (re-earn: family-#2 admission)               │
│ R3 placeholder for future dynamic leg (ownerless — M9)       │
└──────────────────────────────────────────────────────────────┘
```

C0 plumbing is a buy-candidate (S-buy contingency: >2 wave-equivalents of bespoke skeleton triggers adoption). C8 survives as the human-review artifact contract, not a component (A5 PC-1, accepted by A8).

## Trust architecture (D01/D02)
- fw-context = untrusted-but-contracted evidence provider. Evidence-with-provenance never ground truth.
- Tier-1 surfaces (CURRENT_CAPABILITY_MAP) used with named detectors; Tier-2 quarantined behind S-gated Track-B re-earn with guaranteed corpus coverage.
- Fail-safe contract semantics = written substrate SPEC (refusal-not-warn, exact-no-fallback, deterministic-enumeration, unsupported-data-refusal, context-pinning) — design-time, plant-test-validated, not assumed.
- G1 provenance instrument = out-of-band build-manifest diff (A4 UQ-1); config_hash is identity, not integrity.
- Independent channels are load-bearing for cross-checks: grep/rg raw-disk scan, WSL clang -E as instrument (representativeness unvalidated — gap-3 top risk).

## Data flow (wave-1 path)
Template → QueryBundle (K2 family) → evidence retrieval (K1: substrate queries + independent channels, each fact logged with tier+certainty) → EvidenceGate (FR-2 E1–E7) → RuleCard (K2) → refutation protocol execution + plant-tested detectors (K3) → S-audit (advisory until custody ruling) → ADR-scoped scope stamps.

## Explicitly NOT in the architecture (D10)
No runtime simulation component; no multi-variant machinery pre-U3; no compiler-oracle MVP layer (WSL clang -E = instrument only); no LLM free-text rules; no second parser (delegation map P3: parsing stays in fw-context; grep is a raw-disk channel, not a parser).

## Complexity control (V7)
Three components only. "A component needs a measured miss to exist." C0–C8 remain the domain map vocabulary; the build plan is K1–K3. Component-level re-earn events are named in the ROADMAP.
