# C5 E2E-count evidence (extraction only; no judgment on whether C5 should count)

## What the acceptance gate actually recorded

`SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json` → `gates.A1_case_rounds` (original: `implementation/outputs/wave1/WAVE1_ACCEPTANCE_RESULTS.json`):

```json
{"count": 4, "gate": ">=4 E2E case-rounds", "pass": true,
 "rounds": ["C1", "C4-twin", "C6-twin", "C5-tripwire"]}
```

## The gate logic that produced it

`implementation/run_i9_acceptance.py`, lines 56–64 (verbatim):

```python
# ── A1/A2: case-rounds and lineages ─────────────────────────────────────────
e2e_rounds = [r for r, meta in ROUNDS.items() if meta["kind"] == "e2e"]
frozen_rounds = []
for name, meta in ROUNDS.items():
    freezes = list(meta["dir"].glob("FREEZE_*.json"))
    if freezes:
        frozen_rounds.append(name)
gates["A1_case_rounds"] = {"gate": ">=4 E2E case-rounds", "count": len(frozen_rounds),
                           "rounds": frozen_rounds, "pass": len(frozen_rounds) >= 4}
```

and the round registry in the same file (verbatim):

```python
ROUNDS = {
    "C1":          {"dir": IMPL / "rounds" / "I6_C1",            "lineage": "A", "kind": "e2e"},
    "C4-twin":     {"dir": IMPL / "rounds" / "I7_C4C6_A",        "lineage": "B", "kind": "e2e"},
    "C6-twin":     {"dir": IMPL / "rounds" / "I7_C4C6_B",        "lineage": "B", "kind": "e2e"},
    "C5-tripwire": {"dir": IMPL / "rounds" / "I8_C5_T2_TRIPWIRE","lineage": "C", "kind": "tripwire"},
}
```

**Criterion by which C5 was counted (as implemented):** the A1 gate counts every round in the `ROUNDS` registry that has a `FREEZE_*.json` manifest in its round directory. `e2e_rounds` (the `kind == "e2e"` filter) is computed at line 57 but is **not referenced** by the A1 gate evaluation. C5-tripwire is registered with `kind: "tripwire"` and has `FREEZE_C5.json`, therefore it is counted; the gate label reads ">=4 E2E case-rounds" while the counting mechanism is "rounds with a freeze manifest".

## Round-receipt evidence for C5

| Field / artifact | Value | Path |
|---|---|---|
| Freeze manifest | `FREEZE_C5.json`, `freeze_id` starting `FRZ-C5-` (see file) | `B_C5/FREEZE_C5.json` (original: `implementation/rounds/I8_C5_T2_TRIPWIRE/FREEZE_C5.json`) |
| Custody events | `freeze_verified`, `bundle_assembled` (no breach events) | `B_C5/custody_log.jsonl` |
| K2 terminal outcome | `loop_outcome_kind: "insufficient_evidence"`, `promoted: false` (tripwire_1); `quarantine_proof` all `true` | `B_C5/I8_RECEIPT.json` |
| Promoted RuleCards | none — acceptance `case_rounds.C5-tripwire.cards = []` | `SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json` |
| Scorer artifacts | none for this round (no `SOLVING_SUMMARY.json`, no `rulecards/` directory) | `implementation/rounds/I8_C5_T2_TRIPWIRE/` (absence verified by directory listing) |
| Refutation artifacts | none (no card existed to refute) | `E_REFUTATIONS/C5/I8_RECEIPT.json` |

The freeze manifest for C5 was created by the round driver `implementation/run_i8_c5_tripwire.py` (calls `build_manifest`/`write_manifest` and the `CustodyLog` events `freeze_verified`/`bundle_assembled` — same manifest discipline as the e2e runner's `stage_case_round`).
