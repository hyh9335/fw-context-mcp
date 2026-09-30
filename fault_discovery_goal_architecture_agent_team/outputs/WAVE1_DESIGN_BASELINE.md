# WAVE1 DESIGN BASELINE (A0 integration) — design-gate output

State on entry: `S0_APPROVED_WITH_EXPLICIT_GAPS → READY_TO_DESIGN_WAVE1_IMPLEMENTATION`. This task = design gate only: no K1/K2/K3 implementation code, no product-code changes, strategy debate not reopened. Workers: D1 (corpus curation, 2 turns), D2 (schemas), D3 (plant tests), D4 (interfaces) — no worker spawned children. One fresh verifier audits this gate (`WAVE1_DESIGN_AUDIT.md`).

## 1. Final E2E corpus set (design-complete; frozen at implementation freeze)
| Case | Role | Identity |
|---|---|---|
| **C1** | planted E2E, lineage A (Zephyr-derived) | SOURCE-DETERMINISTIC workqueue pairing fault: one-token Kconfig misspelling (`CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD`) kills the only `k_work_submit` site under every config while handler registration + ready flag stay live; derived from real Zephyr v3.7.0 lis2dh trigger structure. Two-part RuleCard: source mechanism provable; runtime occurrence = required-but-unavailable. No runtime queue-exhaustion claims (supersedes the S0-era "queue-full" framing). |
| **C4** | real public fault, lineage B | **Apache NuttX `getcwd()` compiled out under `CONFIG_DISABLE_ENVIRON=y`** — pre-fix `526ba1ab343a00b2e1801e089392eed64758b820`, fix merge `67ed036227b14bbab01b63801bfd7e218dfd6107`, PR apache/nuttx#14365, release notes 12.8.0. Whole-file `#ifndef` guard + conditional-only build listing ⇒ no `getcwd` definition ⇒ link-time undefined reference for any consumer. Corrected-of-record: pre-fix listing is conditional-only (no duplicate); fix MOVES the entry to unconditional. |
| **C5** | T2 tripwire, lineage C | single-line body inverted comparison (`== 0` vs base `!= 0`) in `link_ready()`; decisive evidence via T0 windows + condition regions + grep; T2 single-line-body row as load-bearing ⇒ automatic case failure. fw-context NOT repaired. |
| **C6** | negative control (paired with C4) | frozen POST-FIX snapshot `67ed0362…`, presented unlabeled alongside C4; correct outcome = reject imported hypothesis or REFUTED candidate (existence probe finds definition), zero false-positive RuleCards accepted; post-fix inner `#ifndef` around `getenv("PWD")` is intentional — flagging it is a scored error. |

C2 = PT-G6 contract test, C3 = PT-G7 contract test — NOT E2E. C2r/C3r = OPTIONAL_STRETCH_CASE (independent mechanism-value justification required; corpus-count pressure rejected). **CORPUS-GAP-3: RETIRED_AS_NON_REQUIRED** — C1+C4+C5+C6 satisfy the ≥4 E2E minimum. Rubric items 2/3/4/5/6 all mapped (WAVE1_CORPUS_MATRIX §4). Lineages: A Zephyr-derived, B NuttX, C fixtures.

## 2. Schemas + refutation model (D2, frozen design)
FACT_RECORD_SCHEMA.json (20 fields, T0/T1/T2), MECHANISM_HYPOTHESIS_SCHEMA.json (minimal, NOT the C5 DAG), RULECARD_SCHEMA.json (structured predicates, free text only in rationale, forbidden_overclaims ≥1, k1_mediated const), REFUTATION_STATE_MODEL.md — four states REFUTED / NOT_REFUTED_WITHIN_SCOPE / INCONCLUSIVE / ORACLE_CONFLICT (+ UNEXECUTED initial), transition table + acceptance matrix machine-readable; FR-1's old two-outcome wording marked superseded; C6 expected-refutation = scored success (REFUTED ∧ rejected ∧ protocol-produced counterexample).

## 3. Load-bearing detector readiness (D3)
PT-G1/G4/G6/G7/G8 packages materialized under plant_tests/ (fixtures + hand-derived EXPECTED.json + hash recipe; hazard AND control per package; no fw-context tool produced any expected answer). Status: **MUST-PASS, execution pending implementation** — no aggregate score exists. G2/G3/G9 = design-only smoke stubs, non-blocking.

## 4. Interfaces + governance (D4)
K1: sole substrate mediator (T0 included), sole FactRecord creator, T1 fail-closed (`_fallback` rejection, truncation→truncated_lower_bound, run-pinned context), T2 quarantine, independent channels, detector hooks; error model REFUSED/DEGRADED/LOWER_BOUND — no silent paths. K2: QueryBundle → typed K1 requests → EvidenceGate E1–E7 → RuleCard | rejected hypothesis | abstain (first-class). K3: freeze manifest validation, oracle custody hard clause (leakage voids round), plant-test runner with 100% MUST-PASS gate, scorer incl. C6 case, ≥30% blind sampling, author≠custodian≠scorer with downgrade-and-record fallback. FREEZE_MANIFEST_SPEC: canonical recipe aligned with CORPUS_LOCK_PLAN §2.3; 10 artifact classes; re-freeze rule. KC2A (N=10 diagnostic, 3-of-5 stop, only catastrophic-dominance triggers review) / KC2B (≥30 bugs, ≥5 RTOS, preregistered margin, Wave-2 gate) — neither blocks the first E2E RuleCard.

## 5. Implementation plan (D4)
Order: schemas+freeze plumbing → K1 → K3 minimal plant/custody → K2 → C1 E2E → C4/C6 pair → C5 tripwire → acceptance run → KC-2A. 20 RED tests enumerated test-first; per-component corpus gates; stop/rollback conditions (plant FAIL ⇒ stop+fix+full re-run; oracle leakage ⇒ round void; hash mismatch ⇒ re-freeze); substrate cap ≤1 wave-equivalent until first E2E case; explicit no-hidden-fw-context-repair clause (no product-code work in Wave 1).

## 6. Corrections & honest records this gate
- Corpus-gap accounting fixed; gaps namespaced (registry in WAVE1_CORPUS_MATRIX §0); no unqualified "gap-3" in governing artifacts (historical records retain point-in-time forms, mapped by the registry).
- C1 redesigned source-deterministic (no runtime queue-exhaustion dependency).
- C4 correction of record: conditional-only pre-fix listing (from PR .patch, primary evidence).
- Consumer-pinning: PIN_FAILED_FALLBACK documented (NSH never links libc getcwd under the fault config; RC4 existential, negatable=false); upgrade path = clone nuttx-apps at era SHA where egress allows. Non-blocking; the ONLY open corpus item.
- Open non-blocking measurements carried: MEASURE-GAP-U3/CHANREP/SSTAB, SUBSTRATE-GAP-EXPORT.

## 7. Transition condition
If the verifier returns WAVE1_DESIGN_APPROVED or WAVE1_DESIGN_APPROVED_WITH_EXPLICIT_GAPS (gaps classified IMPLEMENTATION-TIME, none BLOCKING), the next phase is Wave-1 IMPLEMENTATION per WAVE1_IMPLEMENTATION_PLAN.md — a separate task, not started here.
