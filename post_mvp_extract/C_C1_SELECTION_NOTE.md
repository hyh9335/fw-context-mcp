# C — C1 representative RuleCard selection note

**Selected original path**: `implementation/rounds/I6_C1/rulecards/RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR.json`

**Selected RuleCard ID**: `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR` (envelope format `{hypothesis, rulecard}`; card fields under `rulecard`)

**Why this is the most mechanism-representative of the existing cards** (stated strictly from the artifact's own contents):

- Its `fault_class` is `init_submit_pairing` — the only one of the four C1 cards whose fault class names the pairing mechanism itself; the other three are `condition_config_gated` (two) and `existence_absence` (one).
- Its trigger `statement` reads: *"the submit call at fixture/accel_trigger.c:48 is guarded by dead config symbol CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD; under the pinned build it has no live caller (init/submit pairing broken at source level)"* — i.e., it ties the config/guard condition to the initialization/submission pairing failure in one claim (`kind: "call_reachable"`, `negatable: true`, `predicate_id: "P-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR-1"`).
- Its hypothesis (`H-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR`) carries three mechanism steps: `condition` (facts F-0001, F-0002, F-0005, F-0006, F-0011), `definition` (F-0003, F-0007, F-0012), and `call_flow` (F-0013) — the guard→definition→call-flow chain, whereas the two `-33`/`-47` cards carry only condition-text steps and the non-pair `...-THREAAD` card is a `definition_absent` existence claim.
- Its embedded `refutation_result` state is `NOT_REFUTED_WITHIN_SCOPE` with `protocol_completed: true`, `validation_status: "refutation_executed"`; scorer verdict `PASS` (recorded in `E2E_RECEIPT.json` `scores`).

**Other C1 cards (existing, not selected)**, all under `implementation/rounds/I6_C1/rulecards/`:

| RuleCard ID | Original path | fault_class | polarity | statement (abridged from artifact) |
|---|---|---|---|---|
| `RC-C1-ACCEL-TRIGGER-33` | `.../rulecards/RC-C1-ACCEL-TRIGGER-33.json` | condition_config_gated | positive | region `accel_trigger.c:33` guarded by `CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD` compiled OUT under the pinned build |
| `RC-C1-ACCEL-TRIGGER-47` | `.../rulecards/RC-C1-ACCEL-TRIGGER-47.json` | condition_config_gated | positive | region `accel_trigger.c:47` guarded by the same symbol compiled OUT under the pinned build |
| `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD` | `.../rulecards/RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD.json` | existence_absence | negative | symbol `CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD`-guarded definition absent (definition_absent trigger) |

All four are recorded in `implementation/rounds/I6_C1/E2E_RECEIPT.json` (extracted copy: `E_REFUTATIONS/C1/E2E_RECEIPT.json`) with refutation state `NOT_REFUTED_WITHIN_SCOPE`, RS1–RS3 steps, and scorer verdict `PASS`.

No new interpretation beyond the card contents above; no card was created or modified.
