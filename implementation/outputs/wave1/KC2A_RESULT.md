# I10 — KC-2A Bounded Diagnostic Result

**Status**: COMPLETE (diagnostic only — never gates acceptance)
**Date**: frozen run `I10_KC2A_DIAGNOSTIC` · script: `implementation/run_i10_kc2a.py`
**Machine-readable result**: `implementation/outputs/wave1/KC2A_RESULT.json`

---

## 1. Prereg deviation receipt (mandatory)

| | Preregistered KC-2A | This run |
|---|---|---|
| Pool | Independent held-out corpus | **Wave-1 corpus** (the same case-rounds acceptance just measured) |
| N | ≥ 20 adjudication units | **12** (bounded to what the rounds produced) |
| Power | Sized for rate estimation | **UNDERPOWERED** — results are agreement diagnostics, not rate estimates |

**Reason**: the wave-1 budget provisioned no independent held-out corpus; every
corpus artifact went into the acceptance case-rounds (C1, C4-twin, C6-twin,
C5-tripwire). Re-measuring on the same pool is a **consistency check, not a
generalization claim**, and is recorded as such.

**Gating**: KC-2A never gates acceptance (frozen design constraint). The
acceptance verdict in `WAVE1_ACCEPTANCE_RESULTS.json` is unaffected either way.

## 2. Units and outcome

12 adjudication units, one per K2 terminal outcome across the four case-rounds:

| Unit | Kind | Expected | Observed | Correct |
|---|---|---|---|---|
| C4-twin:absence-count | absence_count | 1 | 1 | ✅ |
| C4-twin:RC-I7-A-LIB-GETCWD-38 | card | s5 PASS | PASS | ✅ |
| C4-twin:RC-I7-A-API-GETCWD | card | s5 PASS | PASS | ✅ |
| C6-twin:no-false-absence | absence_count | 0 | 0 | ✅ |
| C6-twin:RC-I7-B-LIB-GETCWD-107 | card | s5 PASS | PASS | ✅ |
| C6-twin:abstention | abstention | honest abstention | abstained, no fabricated missing-evidence | ✅ |
| C1:RC-C1-ACCEL-TRIGGER-33 | card | s5 PASS | PASS | ✅ |
| C1:RC-C1-ACCEL-TRIGGER-47 | card | s5 PASS | PASS | ✅ |
| C1:RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD | card | s5 PASS | PASS | ✅ |
| C1:RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR | card | s5 PASS | PASS | ✅ |
| C1:promoted-card-set | card_set | the 4 expected ids | exact match | ✅ |
| C5-tripwire:t2-quarantine | quarantine | no degraded promotion; first-class refusal; second-surface pass | all three held | ✅ |

**Agreement: 12/12 (1.0).** Zero incorrect units. Zero overclaims on any card.

## 3. Interpretation (underpowered — bounded claim)

Every promoted RuleCard carries a PASS s5 verdict with zero overclaim hits; the
C6 negative-control twin produced **no false absence card**; the C5 T2-only
attempt was quarantined first-class with no degraded promotion and the same
mechanism passing once a second independent surface was supplied. Under the
recorded underpowered condition this is a **consistency signal only**: it says
the wave-1 pipeline is self-consistent against the custodian-held case truths
on the corpus it was built with. It does **not** estimate detection accuracy on
unseen cases.

## 4. Acceptance impact

None. Diagnostic only. Acceptance verdict lives in
`outputs/wave1/WAVE1_ACCEPTANCE_RESULTS.json` (10/10 gates PASS →
`WAVE1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS`).
