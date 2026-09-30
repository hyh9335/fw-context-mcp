# B — C5 (I8_C5_T2_TRIPWIRE) execution artifacts

Round identity: `C5-tripwire`, round dir `implementation/rounds/I8_C5_T2_TRIPWIRE/`, freeze `FREEZE_C5.json`.

Extraction note (fact, from directory listing): **no `SOLVING_SUMMARY.json` exists for this round** — the round was driven by `implementation/run_i8_c5_tripwire.py`, which calls `K2Loop.run` directly rather than the `solve_case` persistence path. The most complete existing execution artifact is `I8_RECEIPT.json`.

| Original path | Artifact role | Contains initial T2 refusal? | Contains second-surface pass? | Contains RuleCard ID? | Contains refutation state? |
|---|---|---|---|---|---|
| `implementation/rounds/I8_C5_T2_TRIPWIRE/I8_RECEIPT.json` | round receipt / terminal-outcome ledger | YES — `tripwire_1_t2_only.missing_evidence[0].why_needed` = `"E3: single-line step S2 has 1 surface(s) (<2): ['fw_context_lookup_symbol']"`, `loop_outcome_kind: "insufficient_evidence"`, `promoted: false` | YES — `tripwire_2_two_surfaces.gate_two_surface_refused: false`, `second_surface_fact_id: "F-0003"`, `second_surface_trust: "T1"`; `quarantine_proof.same_mechanism_passes_with_second_surface: true` | NOT_FOUND_IN_ARTIFACT (no card was created) | NOT_FOUND_IN_ARTIFACT (no refutation executed; nothing to refute) |
| `implementation/rounds/I8_C5_T2_TRIPWIRE/fact_log.jsonl` | K1 fact ledger for the round | NO (refusal text lives in I8_RECEIPT.json; the log's refusal rows from the E3 attempt are not persisted in this file) | YES — `F-0003`, `evidence_surface: "independent_grep"`, `trust_class: "T1"` (the second surface) | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT |
| `implementation/rounds/I8_C5_T2_TRIPWIRE/custody_log.jsonl` | custody event log | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT |
| `implementation/rounds/I8_C5_T2_TRIPWIRE/FREEZE_C5.json` | freeze manifest (round identity) | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT | NOT_FOUND_IN_ARTIFACT |

Execution path as recorded by the artifacts (no interpretation beyond their fields):

1. Initial T2-only attempt: K2 loop outcome `insufficient_evidence`; gate refusal reason `E3: single-line step S2 has 1 surface(s) (<2)`; `promoted: false`.
2. Second surface: independent-grep fact `F-0003` (T1) attached to the same load-bearing step at the gate; gate refusal list empty (`gate_two_surface_refused: false`).
3. Final K2 terminal outcome: **no RuleCard created** — the top-level acceptance (`SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json`, A10 entry and `case_rounds.C5-tripwire.cards = []`) lists zero promoted RuleCards for C5.
4. Refutation: **not executed** — there is no RuleCard and no `REFUTATIONS.json` in the round directory.
