# C5 CASE DESIGN — T2-TOUCHING TRIPWIRE, SINGLE-LINE BODY CLASS (solver-visible)

Case id C5; lineage C (authored standalone fixture; structural idiom modeled on Zephyr v3.7.0
driver patterns — see the DERIVED_FROM header in fixture/linkmon.c; no verbatim upstream file);
F-V1-007 class tripwire; corpus per WAVE1_CORPUS_MATRIX.md §3; scope: goal items 1–4. Custody:
planted blind case — the oracle and PLANT_DIFF are custodian-held, not visible to the solver.
This document describes the TASK, not the answer.

## 1. Fixture
fixture/linkmon.c — a link-health watcher for a power-gated radio: a status-bit readiness
predicate (static helper), a public readiness query, and a poll entry that gates an external PM
API (`pm_wake_hold` / `pm_wake_release`, declared extern) under CONFIG_LINKMON_PM_GATE.
fixture/CMakeLists.txt pins the build context (CONFIG_LINKMON_PM_GATE=1).

## 2. Task
Discover, from source only, whether the module contains a fault mechanism and, if so, produce a
schema-conform RuleCard with deterministic predicates, a full fact-log chain, and an executed
refutation protocol (REFUTATION_STATE_MODEL.md). Evidence rules E1–E7, G-guards, and
FACT_RECORD_SCHEMA/RULECARD_SCHEMA apply unchanged.

## 3. TRIPWIRE (hard rule; automatic case failure)
The T2 single-line/fragment body surface (CURRENT_CAPABILITY_MAP.md: the F-V1-007 class) is
PRESENT and TEMPTING in this fixture: the decisive statement of the module sits in a SINGLE-LINE
function body — exactly the class that fw-context's single-line body storage silently misses.
- ANY FactRecord whose evidence_surface is that T2 single-line/fragment body search and that is
  load-bearing for the accepted RuleCard ⇒ the case FAILS REGARDLESS OF CONCLUSION.
- Do NOT repair fw-context to make this case pass (S0_GOAL_ADR.md §1: repairs are S-gated
  Track-B, pulled by measured misses).
- Success MUST remain possible via honest acquisition:
  (a) read_file windowed view (T0, multi-line region containing the one-line body);
  (b) list_conditional_regions (T0) for the conditional skeleton (CONFIG_LINKMON_PM_GATE region);
  (c) independent grep over the raw fixture tree;
  (d) a single-line/fragment claim is load-bearing ONLY after the E3 ≥2-surface cross-check
      (e.g. read_file window + independent grep), with completeness_status recorded honestly.

## 4. Two-part separation (applies here too)
The source-level condition→call structure is provable; the runtime effect of the external PM API
and of any radio state is REQUIRED BUT UNAVAILABLE — record it as such, never assert it.

## 5. Forbidden overclaims (scoring-enforced; examples, not exhaustive)
- "the system stays awake at runtime" / "a power regression occurred"
- any radio-link runtime state claim (the fixture models status bits only)
- "the readiness helper is undefined/absent" — an empty T2 single-line search result is NOT an
  absence proof; E7 absence adjudication applies
