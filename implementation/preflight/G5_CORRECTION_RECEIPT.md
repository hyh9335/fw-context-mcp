# G5_CORRECTION_RECEIPT — PT-G8 Make.defs line reference correction (preflight, before first freeze)

Status: **COMPLETE** (2026-06, Wave-1 implementation preflight, state I0_PRECHECK → I1).
Task mandate: "PT-G8 `Make.defs` evidence line reference is 3 vs 4. Verify the actual frozen
fixture bytes and correct the reference BEFORE any freeze manifest is produced."

## 1. Old reference (as materialized by the design gate)

| File | Site | Old text |
|---|---|---|
| `plant_tests/PT-G8/PLANT_SPEC.md` | Fixture layout (≈line 19) | "`fixture/Make.defs` — passes `-DPOLL_INTERVAL=250` **(line 3)**: CONFLICT BY CONSTRUCTION" |
| `plant_tests/PT-G8/PLANT_SPEC.md` | Build truth (≈line 27) | "effective POLL_INTERVAL = 250 (from Make.defs **line 3**), NOT the stored 100;" |
| `plant_tests/PT-G8/PLANT_SPEC.md` | Hazard case (≈line 44) | "the -D flag `-DPOLL_INTERVAL=250` (Make.defs **line 3**, out-of-band) plus the" |
| `plant_tests/PT-G8/EXPECTED.json` | `independent_expected_result.conflict_by_construction.build_flag` | `"-DPOLL_INTERVAL=250 (Make.defs **line 3**)"` |
| `plant_tests/PT-G8/EXPECTED.json` | `pass_condition` | "…cites -DPOLL_INTERVAL=250 (Make.defs **line 3**) plus the condition text…" |

(3 sites in PLANT_SPEC.md + 2 sites in EXPECTED.json = 5 citation sites, matching
WAVE1_DESIGN_AUDIT.md spot-check note and gap table G-5.)

## 2. Actual line (verified against frozen fixture bytes)

`fixture/Make.defs` was dumped byte-wise (UTF-8, 285 bytes, 6 content lines):

```
line 1: # Make.defs — PT-G8 build fragment: conflict by construction.
line 2: # -DPOLL_INTERVAL=250 overrides the stored header default 100 (poll_cfg.h);
line 3: # no -DCTRL_INTERVAL flag exists (control macro has no conflict).
line 4: CFLAGS += -DPOLL_INTERVAL=250
line 5: CFLAGS += -I$(PTG8_ROOT)
line 6: SRCS += poller.c main.c
```

**Actual line of `-DPOLL_INTERVAL=250` = 4.** Line 3 is a comment. The audit finding is confirmed.

Cross-check of all other PT-G8 citations against fixture bytes (no further defects found):
`poll_cfg.h` line 10 `#ifndef POLL_INTERVAL`, line 11 `#define POLL_INTERVAL 100`, line 14
`#define CTRL_INTERVAL 40`; `poller.c` line 12 `#if POLL_INTERVAL > 200` (region 12–16),
line 13 active assignment, line 14 `#else`, line 15 inactive assignment, line 21
`#if CTRL_INTERVAL > 0`, line 22 active assignment. All correct as cited.

## 3. Correction applied

All five citation sites corrected **3 → 4**. No other text changed. No fixture byte changed.

## 4. Fixture hash before / after

| Artifact | SHA-256 before | SHA-256 after |
|---|---|---|
| `plant_tests/PT-G8/fixture/Make.defs` | `1262f2bdfde89d19a8bbe8297e4e9e1b6b0367ea3ebc8c931e8c6a0ab2943da5` | `1262f2bdfde89d19a8bbe8297e4e9e1b6b0367ea3ebc8c931e8c6a0ab2943da5` (**unchanged**) |
| `plant_tests/PT-G8/PLANT_SPEC.md` | `325a1314f8137a42c928fb1e847f74c5e6b10033f65cd5e22cef5a99d6c6791b` | `3260115ce5ab8851b2ff3c4028f3e684f525652d4ffb0bf9a1cad265848f9089` |
| `plant_tests/PT-G8/EXPECTED.json` | `37254dc5fd2cf7306ade81360fd0f1b1406e4904723af9e90414c88c3ca37e8b` | `ceacf0a3425cc1baf81870bedd56c6cc58de7957549068cc6eff7bc959163bcb` |

The **fixture** hash is identical: the frozen fixture bytes were never touched. The PLANT_SPEC /
EXPECTED hashes change because those two documents are the corrected artifacts themselves; per
CORPUS_LOCK_PLAN.md §9 / FREEZE_MANIFEST_SPEC.md §4 they are re-hashed at freeze time, and this
correction happened BEFORE the first freeze manifest was produced, as mandated.

## 5. Why the semantic oracle is unchanged

The PT-G8 semantic oracle is: effective build value of POLL_INTERVAL = 250; `#if POLL_INTERVAL >
200` TRUE ⇒ poller.c line 13 active / line 15 inactive; CTRL_INTERVAL control: effective 40, line
22 active; macro-value rows degraded + never load-bearing. Every element of that oracle is derived
from the **fixture bytes** (`Make.defs` flag content, `poll_cfg.h` stored value + `#ifndef` guard,
`poller.c` condition sites) and the standard macro-override rule. The line number of the flag
inside `Make.defs` is provenance metadata *citing* the fixture; it participates in no expected
branch, value, or verdict. Correcting the citation does not move any expected result, so
`pass_condition`, `fail_condition`, `expected_k1_behavior`, and all hand-derived values stand
unchanged. Grep confirms zero residual "Make.defs line 3" / "(line 3)" citations in PT-G8.

## 6. G-6 disposition (baseline-reference formatting issue) — NON-BLOCKING, recorded

G-6 (WAVE1_DESIGN_AUDIT.md): `WAVE1_DESIGN_BASELINE.md` cites `S0_GOAL_ADR.md` as governing input
instead of quoting the authoritative goal verbatim. Assessment: **purely cosmetic** — no machine
consumer parses the goal string out of WAVE1_DESIGN_BASELINE.md; all machine-facing sites (the
three schemas' `description` fields, `S0_GOAL_ADR.md:5`, `GOAL_BASELINE.md:4`,
`REFUTATION_STATE_MODEL.md:3`) carry the goal byte-identically (verified by the design audit at 11
sites), and the schema validator for this wave pins the goal via S0_GOAL_ADR.md, not the baseline.
Per the task instruction ("if purely cosmetic, record it as non-blocking rather than spending
engineering time") G-6 is recorded here as NON-BLOCKING; the design-gate artifact is left
byte-stable to avoid churning frozen design documents during implementation.

## 7. Freeze sequencing note

First freeze manifest for any round may now be produced. The corrected PLANT_SPEC.md /
EXPECTED.json hashes above are the ones the custodian (K3) will record at freeze time.
