# C1 CASE DESIGN — PFC-1 SOURCE-DETERMINISTIC WORKQUEUE PAIRING (solver-visible)

Case id C1; lineage A (Zephyr-derived); corpus per WAVE1_CORPUS_MATRIX.md §2–3; scope: goal items
1–3. Custody: planted blind case — the oracle and PLANT_DIFF are custodian-held and NOT visible to
the solving agent. This document describes the TASK, not the answer.

## 1. Fixture
`fixture/` is a trimmed standalone slice derived from REAL Zephyr driver structure (each file
carries a DERIVED_FROM header citing the exact upstream files and version):
- upstream base: zephyrproject-rtos/zephyr tag v3.7.0, drivers/sensor/st/lis2dh/
  — lis2dh_trigger.c (work-item init/submit idiom under CONFIG_*_TRIGGER_GLOBAL_THREAD),
    Kconfig (trigger-mode choice with hidden selected symbol), CMakeLists.txt
    (conditional trigger-source listing).
- slice: accel_core.c (driver init + readiness), accel_trigger.c (trigger arming, work handler,
  scheduling), accel_local.h (inert kernel-API stand-ins), Kconfig, CMakeLists.txt.
- pinned build context: CONFIG_ACCEL_SAMPLE=y, CONFIG_ACCEL_TRIGGER=y,
  CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD=y (Kconfig default; pinned by CMakeLists compile definitions).

## 2. Task
Discover, from source only, whether the module contains a fault mechanism and, if so, produce a
MECHANISM_HYPOTHESIS promoted to a schema-conform RuleCard whose trigger_conditions are
deterministic, machine-evaluable predicates over recorded FactRecords.

Requirements:
1. Discovery must be grounded in cross-file condition-chain evidence: Kconfig defaults/selection +
   conditional guard text + call/reachability analysis. A trivial grep for a single API name is
   neither sufficient nor accepted as the load-bearing chain.
2. TWO-PART SEPARATION IS MANDATORY (WAVE1_CORPUS_MATRIX.md §2):
   (a) the source-level mechanism must be provable from T0/T1 surfaces;
   (b) runtime occurrence is REQUIRED BUT UNAVAILABLE: whether the work item is ever actually
       queued/executed at runtime, and any runtime effect, is NOT a source-derived fact. Record it
       as required-but-unavailable; never assert it.
3. Every accepted RuleCard must carry a non-empty forbidden_overclaims list and a scope_stamp.
4. Evidence rules E1–E7, the G-guards, and FACT_RECORD_SCHEMA/RULECARD_SCHEMA apply unchanged.
   fw-context v0.31.0 surfaces plus independent channels (grep, WSL clang -E, build-manifest diff)
   are available; single-line/fragment claims need the E3 ≥2-surface cross-check.

## 3. Forbidden overclaims (scoring-enforced; examples, not exhaustive)
- "queue full occurred" / any resource-exhaustion claim
- "submission was lost at runtime"
- "the work handler crashed at runtime" / "NULL-handler crash at runtime"
- any claim that the work item never executes at runtime
The correct RuleCard asserts the SOURCE-level mechanism only and stamps its scope.

## 4. Expected artifacts
One RuleCard (schema-conform), fact-log rows for every load-bearing predicate, a refutation
protocol executed under the round freeze_id, and a refutation_result adjudicated per
REFUTATION_STATE_MODEL.md. Absence claims must be enumerated over ≥2 surfaces (E4/E7).
