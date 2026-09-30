# REFUTATION & VALIDATION STATE SPEC (wave1-1 revision)

Phase: WAVE-1.1 semantic-correctness repair (PMR-5, PMR-6). This revision
SEPARATES evidence validation from fault refutation and defines the
normalized top-level aggregation.

## 1. Three independent axes on every accepted card

Every accepted fault card carries a `validation` object (RuleCard schema
v1.1, package-local: `fault_discovery_agent/schemas/RULECARD_SCHEMA_V1_1.json`):

```json
"validation": {
  "evidence_integrity_status": "PASS",   // RS1: cited facts exist, are T0/T1,
                                         // pin the frozen build; DEGRADED/FAIL possible
  "faultness_status": "PASS",            // family-scoped Faultness Gate verdict
                                         // (see FAULTNESS_GATE_SPEC.md); PENDING at
                                         // promotion time, PASS after the round gate
  "refutation_state": "NOT_REFUTED_WITHIN_SCOPE" // protocol terminal
}
```

- The axes are INDEPENDENT: a card can pass evidence integrity while failing
  faultness (a well-evidenced benign conditional), and refutation never
  upgrades a faultness failure.
- The FROZEN RuleCard schema (custodian contract) is unchanged and still
  rejects the `validation` object — historical Wave-1 artifacts validate
  exactly as they did. The v1.1 schema lives in the agent package and is
  registered as `rulecard_v1_1` (`schemas/loader.PACKAGE_SCHEMA_FILES`).
- Refutation state vocabulary is the frozen set in
  `fault_discovery_agent/schemas/refutation.py`
  (`UNEXECUTED`, `NOT_REFUTED_WITHIN_SCOPE`, `REFUTED`, ...). Cards are only
  terminal at `NOT_REFUTED_WITHIN_SCOPE` or `REFUTED`;
  `UNEXECUTED` is never counted as an E2E terminal.

## 2. Refutation protocol (RS1–RS4) — unchanged semantics, tightened recording

- **RS1 evidence integrity**: every cited fact exists in the round fact log,
  is T0/T1 trust, and pins the frozen `build_snapshot_id`. Result recorded on
  `validation.evidence_integrity_status`.
- **RS2 guard-symbol presence**: re-derives the guard from the snapshot.
- **RS3 guard-flip counterexample search**: flips each guard symbol and
  re-checks the region; a surviving flip is OUT OF SCOPE (the rule pins the
  build) and is recorded as audit detail with
  `evidence_classification` ∈ {`causal_support`, `out_of_scope`} per symbol —
  a flip symbol that appears in the card's trigger statement is causal
  support; anything else is out of scope. `counterexample_ref` stays null
  for `NOT_REFUTED_WITHIN_SCOPE`.
- **RS4 score/audit**: blind evidence-internal scoring (≥30% sample).
- Refutation EXECUTES the protocol; the Faultness Gate is applied BEFORE
  refutation (a demoted candidate is never refuted — it was never a fault
  claim). `ROUND_RECEIPT.json` is finalized AFTER refutation so terminal
  states are authoritative (`finalize_round_receipt`).

## 3. Terminal round classification (executable E2E criterion)

`k3/terminal_receipt.classify_round_receipt` classifies a round from its
terminal receipt ONLY (never from freeze manifests or registry labels):

| Class | Condition (all required) |
|---|---|
| `e2e_positive` | source staged, fact records > 0, mechanism hypotheses > 0, ≥1 accepted card with faultness PASS + evidence-integrity PASS + terminal refutation state, zero coherence violations |
| `e2e_negative` | accepted empty AND (≥1 explicit faultness rejection OR any accepted card REFUTED) |
| `safety_only` | abstentions + T2-tripwire scenarios (E3 refusals), zero accepted, zero rejected |
| `not_e2e` | anything else — under-traversal, missing artifacts, non-terminal states |

Fail-closed properties (regression-tested): a freeze-only round (no
traversal) is `not_e2e`; an accepted card with `UNEXECUTED` refutation is
never positive; a coherence violation is never positive; a hypothesis-level
abstention WITHOUT an explicit no-fault terminal marker is never a negative
case (it may be an under-traversal).

## 4. Normalized top-level aggregation (`REFUTATION_RESULTS_V2.json`)

One record per terminal outcome, keyed by
`(source_artifact_path, rulecard_id)`, exactly once:

```json
{
  "source_artifact_path": "implementation/rounds/wave1_1/C1/REFUTATIONS.json",
  "round": "C1",
  "rulecard_id": "RC-C1-ACCEL-TRIGGER-47",
  "final_state": "NOT_REFUTED_WITHIN_SCOPE",
  "evidence_integrity_status": "PASS",
  "faultness_status": "PASS",
  "family": "condition_config_gated",
  "historical": false
}
```

- Current-phase records come from the round's own `REFUTATIONS.json` /
  `FAULTNESS_RESULTS.json` artifacts (demoted candidates are recorded with
  `final_state = REJECTED_FAULTNESS_GATE` so coverage is total).
- Historical Wave-1 records (4 × C1, 2 × C4-twin, 1 × C6-twin) are included
  with `historical: true`, `faultness_status: "NOT_APPLICABLE_HISTORICAL"`,
  and their receipts UNCHANGED — they are motivating evidence, never
  current validation.
- Acceptance gate G14 proves: no duplicates, and every currently-promoted
  card appears at least once (exactly-once per source artifact).
