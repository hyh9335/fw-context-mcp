# C6 semantic index — `RC-I7-B-LIB-GETCWD-107` (extraction only; no judgment)

All values below are quoted from existing artifacts. Where a field is split across artifacts, both values and their paths are reported.

| Field | Value as recorded | Artifact path |
|---|---|---|
| rulecard_id | `RC-I7-B-LIB-GETCWD-107` | `A_C6_RULECARD.json` (original: `implementation/rounds/I7_C4C6_B/rulecards/RC-I7-B-LIB-GETCWD-107.json`) |
| mechanism_id / fault_class | `M-I7-B-LIB-GETCWD-107` / `condition_config_gated` | `A_C6_RULECARD.json` (`rulecard.mechanism_id`, `rulecard.fault_class`) |
| hypothesis_ref | `H-I7-B-LIB-GETCWD-107` | `A_C6_RULECARD.json` |
| trigger predicate(s) | 1 predicate: `predicate_id: "P-I7-B-LIB-GETCWD-107-1"`, `kind: "condition_text"`, `negatable: true`, `statement: "region fixture/libs/libc/unistd/lib_getcwd.c:107-110 guarded by 'CONFIG_DISABLE_ENVIRON' is compiled OUT under the pinned build (BUILD-C4C6-DISABLE-ENVIRON)"` | `A_C6_RULECARD.json` (`rulecard.trigger_conditions[0]`) |
| polarity | `positive` | `A_C6_RULECARD.json` (`rulecard.polarity`) |
| validation_status | `unvalidated` (in the persisted card file) | `A_C6_RULECARD.json` (`rulecard.validation_status`) |
| evidence refs | `F-0001`, `F-0002`, `F-0003` — all with `trust_class: "T0"` (3 evidence entries) | `A_C6_RULECARD.json` (`rulecard.evidence[]`) |
| forbidden_overclaims | `["no complete-enumeration claims", "T2 rows never load-bearing", "runtime queue-exhaustion claims", "any claim about runtime occurrence of the guarded behavior"]` | `A_C6_RULECARD.json` (`rulecard.forbidden_overclaims`) |
| refutation state — SPLIT | In the persisted card file: `refutation_result.state = "UNEXECUTED"`, `protocol_completed: false`, `adjudicated_at: null`. In the per-round refutation ledger and downstream records: `state = "NOT_REFUTED_WITHIN_SCOPE"`, `protocol_completed = true` (RS1 `holds: true`; RS2 `holds: true, symbol_hits: {"CONFIG_DISABLE_ENVIRON": 4}`; RS3 `holds: true`, guard_flips `["CONFIG_DISABLE_ENVIRON"]`, `scope_note` records flipped configurations as OUT OF SCOPE because the rule pins `build_snapshot_id`) | Card-embedded value: `A_C6_RULECARD.json`. Executed value: `A_C6_SUPPORTING/REFUTATIONS.json` (original: `implementation/rounds/I7_C4C6_B/REFUTATIONS.json`); also `SUPPORTING/REFUTATION_RESULTS.json` (`refutations[2]`) and `SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json` (C6-twin refutations) |
| scorer verdict | `s1: 1.0, s2: 1.0, s3: 1.0, s4: 1.0, s5_verdict: "PASS"`, `sampled_rows: 2`, `overclaims: []` | `A_C6_SUPPORTING/SOLVING_SUMMARY.json` (original: `implementation/rounds/I7_C4C6_B/SOLVING_SUMMARY.json`) |
| Card class / how counted | Counted as the single promoted RuleCard of case-round `C6-twin` (`case_rounds.C6-twin.cards = ["RC-I7-B-LIB-GETCWD-107"]`); it is a `condition_config_gated` card, **not** an `existence_absence`/absence card — acceptance gate A10 counts `absence_cards = 0` for C6-twin and passes | `SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json` (`case_rounds.C6-twin`, `gates.A10_c6_negative_control`) |
| KC-2A treatment | One adjudication unit `C6-twin:RC-I7-B-LIB-GETCWD-107`, `kind: "card"`, `s5_verdict: "PASS"`, `correct: true` | `outputs/wave1/KC2A_RESULT.json` (`units[]`) — cited by path; file not duplicated into this package |

Extraction note (artifact-provenance fact, no judgment): the card file under `rounds/I7_C4C6_B/rulecards/` is written by the solving stage, before refutation executes; the executed refutation state (`NOT_REFUTED_WITHIN_SCOPE`) is recorded in the per-round ledger and downstream aggregates rather than backfilled into the persisted card file. Both values are reported verbatim above.
