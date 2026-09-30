# E — Refutation artifacts: C1 and C5

## C1 (`E_REFUTATIONS/C1/E2E_RECEIPT.json`)

Original path: `implementation/rounds/I6_C1/E2E_RECEIPT.json`. Round `I6_C1` predates the per-round `REFUTATIONS.json` persistence (introduced during the I7 re-run); its refutation records live inside the round's E2E receipt under the key `refutations`. Contents as recorded:

| RuleCard/Outcome ID | Original path | RS1 | RS2 | RS3 | Final refutation state | Promoted? |
|---|---|---|---|---|---|---|
| `RC-C1-ACCEL-TRIGGER-33` | `implementation/rounds/I6_C1/E2E_RECEIPT.json` (`refutations[0]`) | present | present | present | `NOT_REFUTED_WITHIN_SCOPE` | yes (scorer PASS) |
| `RC-C1-ACCEL-TRIGGER-47` | same file, `refutations[1]` | present | present | present | `NOT_REFUTED_WITHIN_SCOPE` | yes (scorer PASS) |
| `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD` | same file, `refutations[2]` | present | present | present | `NOT_REFUTED_WITHIN_SCOPE` | yes (scorer PASS) |
| `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR` | same file, `refutations[3]` | present | present | present | `NOT_REFUTED_WITHIN_SCOPE` | yes (scorer PASS) |

Each record's `steps` carry `step_id` `RS1`, `RS2`, `RS3` (RS2 entries include the independent cross-check hits; RS3 entries include guard-flip counterexample search output). The same file's `scores` array records `verdict: "PASS"` for all four.

## C5 (`E_REFUTATIONS/C5/`)

**No refutation artifact exists for C5, and none should exist: the K2 terminal outcome was `insufficient_evidence` — the loop refused the T2-only promotion before any RuleCard was created, so there was nothing to refute.**

The artifact proving this is `E_REFUTATIONS/C5/I8_RECEIPT.json` (original: `implementation/rounds/I8_C5_T2_TRIPWIRE/I8_RECEIPT.json`):

- `tripwire_1_t2_only.loop_outcome_kind: "insufficient_evidence"`, `tripwire_1_t2_only.promoted: false`
- refusal reason: `"E3: single-line step S2 has 1 surface(s) (<2): ['fw_context_lookup_symbol']"`
- `quarantine_proof.no_degraded_promotion: true`
- the round directory contains no `rulecards/` directory and no `REFUTATIONS.json`
- top-level acceptance (`SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json`) records `case_rounds.C5-tripwire.cards = []`

The second-surface pass recorded in the same receipt (`tripwire_2_two_surfaces.gate_two_surface_refused: false`, second surface = fact `F-0003`, `independent_grep`, T1) was a gate-level re-evaluation demonstrating that the refusal is surface-count-conditional; it did not produce a promoted RuleCard and therefore no refutation was run for it. No refutation result has been fabricated.
