# REFUTATION STATE MODEL (Wave-1 design gate, D2)

Status: design-gate contract. Companion of RULECARD_SCHEMA.json — its `refutation_result` object conforms to the machine-readable block in §5. Governing inputs: S0_GOAL_ADR.md §1 (freeze discipline) and §2 (rubric items 8/10); FAULT_DISCOVERY_REQUIREMENTS.md FR-1/FR-2 (E1–E7)/FR-6; WAVE1_CORPUS_MATRIX.md §2–3 (two-part C1 separation; C6 negative control). Authoritative goal (verbatim): 构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。

## 0. Historical note — FR-1 two-outcome wording SUPERSEDED

FR-1 specified the refutation_protocol as "two-outcome: rule_falsified | oracle_falsified" (origin: the A2 FaultMechanismRecord + A3 RuleCard unification, R1). That wording fused the fate of the rule with the fate of the oracle into one two-valued result and cannot express the C6 expected-refutation case. **SUPERSEDED by this document**: the refutation RESULT is the four-state machine below. The two-outcome notion survives ONLY as `expected_two_outcomes {rule_holds_when, rule_falsified_when}` inside each refutation_protocol step — an a-priori prediction about observations, never a result value. No other FR-1 field changes.

## 1. States — exact semantics

Initial state **UNEXECUTED**: refutation_protocol declared but not run; no adjudication exists; nothing may be scored.

- **REFUTED** — a counterexample satisfying the protocol's `rule_falsified_when` was produced AND verified under freeze discipline (S0-Q1: counterexample and its verification chain are recorded as FactRecords under the same freeze_id; no mid-round change to case source, oracle, agent, or substrate). The rule is false as stated within its scope_stamp.
- **NOT_REFUTED_WITHIN_SCOPE** — the protocol executed to completion within the declared scope_stamp and produced no counterexample. The scope boundary is stated in scope_stamp. This is SURVIVAL, not proof of general truth: it licenses in-scope use only and never generalizes silently.
- **INCONCLUSIVE** — the protocol could not complete: missing evidence, K1 channel refusal, T2-surface unavailability, or any required fact unobtainable. MUST enumerate every unobtainable required fact in `missing_evidence_refs`. Establishes nothing in either direction.
- **ORACLE_CONFLICT** — the oracle/ground-truth itself proved inconsistent or falsified (e.g. an expected outcome contradicted by verified upstream evidence, a broken planted oracle, or two oracle sources disagreeing under independent cross-check). Distinct from rule survival: the rule's truth value is NOT established; the measurement instrument failed.

## 2. Allowed transitions

- UNEXECUTED → REFUTED | NOT_REFUTED_WITHIN_SCOPE | INCONCLUSIVE | ORACLE_CONFLICT (one adjudicated execution).
- INCONCLUSIVE → REFUTED | NOT_REFUTED_WITHIN_SCOPE | ORACLE_CONFLICT — ONLY after evidence-gap closure AND re-freeze (new freeze_id; closed gaps become new FactRecord rows under the new freeze; S0-Q1 item 4).
- ORACLE_CONFLICT → no forward transition. Re-derivation (corrected oracle; new hypothesis/RuleCard) is REQUIRED before any acceptance; the successor enters as a new artifact at UNEXECUTED under a new freeze_id.
- REFUTED and NOT_REFUTED_WITHIN_SCOPE are TERMINAL for the given (rulecard_id, freeze_id) pair. A new freeze may open a new artifact; survival does not carry over, and a past refutation never suppresses a re-test (it is prior knowledge, not a verdict on the new artifact).

## 3. Acceptance (S0 rubric items 8 and 10 bind)

1. Default (positive-claim) RuleCard: acceptance-eligible ⟺ `refutation_result.state = NOT_REFUTED_WITHIN_SCOPE` AND `validation_status ∈ {refutation_executed, accepted}`.
2. **Expected-refutation case (C6 negative control)** — a candidate RuleCard registered with `scope_stamp.corpus_case = "C6"` whose declared correct outcome is its own refutation. Acceptance-eligible for the CASE ⟺ `refutation_result.state = REFUTED` AND `validation_status = rejected` AND the counterexample was produced by the declared protocol under freeze discipline. Such a card is never promoted to accepted; the (REFUTED, rejected) pair is its scored success.
3. INCONCLUSIVE is scoreable ONLY as failure-to-establish — never as success, in any case including C6.
4. ORACLE_CONFLICT blocks all acceptance pending re-derivation.
5. Absence adjudication (E7, anti-sunk-cost): where a verdict hinges on an absence claim and the absence is ambiguous, adjudicate PRESENT — an ambiguous absence can neither produce a REFUTED verdict nor falsify a `definition_absent` predicate for scoring.
6. Zero silent unsupported claims (rubric 8): every accepted RuleCard carries a non-empty `forbidden_overclaims` list and a provenance-complete T0/T1 evidence chain (E1–E3); T2 rows are never load-bearing (E2).

## 4. validation_status mapping

| refutation_result.state | protocol_completed | allowed validation_status |
|---|---|---|
| UNEXECUTED | false | unvalidated, plant_tested |
| REFUTED | true | rejected |
| NOT_REFUTED_WITHIN_SCOPE | true | refutation_executed, accepted |
| INCONCLUSIVE | false | unvalidated, plant_tested |
| ORACLE_CONFLICT | false | unvalidated, plant_tested |

Notes: `plant_tested` additionally requires the family's detector plant tests to have passed; `refutation_executed` may appear in the execution log as the transient pre-verdict entry, but the final status for REFUTED is `rejected`; nothing is ever `accepted` with `protocol_completed = false`; ORACLE_CONFLICT never yields `rejected` (the rule was not falsified — the oracle was).

## 5. Machine-readable contract

RULECARD_SCHEMA.json `refutation_result` conforms to this block (state transition table + acceptance matrix):

```json
{
  "contract_id": "REFUTATION_STATE_MODEL.md#5",
  "refutation_result_fields": {
    "state": {
      "enum": ["UNEXECUTED", "REFUTED", "NOT_REFUTED_WITHIN_SCOPE", "INCONCLUSIVE", "ORACLE_CONFLICT"]
    },
    "protocol_completed": { "type": "boolean" },
    "counterexample_ref": {
      "type": ["string", "null"],
      "description": "Verified counterexample artifact reference when state is REFUTED; otherwise null."
    },
    "missing_evidence_refs": {
      "type": "array",
      "items": { "type": "string" },
      "description": "When state is INCONCLUSIVE: identifiers of unobtainable required facts (gap register / K1 refusal rows); otherwise empty."
    },
    "adjudicated_at": { "type": ["string", "null"], "format": "date-time" }
  },
  "state_transitions": [
    {
      "from": "UNEXECUTED",
      "to": ["REFUTED", "NOT_REFUTED_WITHIN_SCOPE", "INCONCLUSIVE", "ORACLE_CONFLICT"],
      "precondition": "one adjudicated protocol execution"
    },
    {
      "from": "INCONCLUSIVE",
      "to": ["REFUTED", "NOT_REFUTED_WITHIN_SCOPE", "ORACLE_CONFLICT"],
      "precondition": "evidence-gap closure plus re-freeze under a new freeze_id"
    },
    {
      "from": "REFUTED",
      "to": [],
      "terminal": true,
      "scope": "terminal for the (rulecard_id, freeze_id) pair"
    },
    {
      "from": "NOT_REFUTED_WITHIN_SCOPE",
      "to": [],
      "terminal": true,
      "scope": "terminal for the (rulecard_id, freeze_id) pair"
    },
    {
      "from": "ORACLE_CONFLICT",
      "to": [],
      "requirement": "re-derivation required before any acceptance; successor artifact starts at UNEXECUTED under a new freeze_id"
    }
  ],
  "acceptance_matrix": [
    {
      "state": "NOT_REFUTED_WITHIN_SCOPE",
      "protocol_completed": true,
      "validation_status_allowed": ["refutation_executed", "accepted"],
      "acceptance_eligible": true,
      "case": "default"
    },
    {
      "state": "REFUTED",
      "protocol_completed": true,
      "validation_status_allowed": ["rejected"],
      "acceptance_eligible": "expected_refutation_case_only",
      "case": "C6",
      "note": "REFUTED plus rejected is the scored success of the C6 negative control; never promoted to accepted"
    },
    {
      "state": "INCONCLUSIVE",
      "protocol_completed": false,
      "validation_status_allowed": ["unvalidated", "plant_tested"],
      "acceptance_eligible": false,
      "scoring": "failure_to_establish"
    },
    {
      "state": "ORACLE_CONFLICT",
      "protocol_completed": false,
      "validation_status_allowed": ["unvalidated", "plant_tested"],
      "acceptance_eligible": false,
      "requirement": "re-derivation before any acceptance"
    },
    {
      "state": "UNEXECUTED",
      "protocol_completed": false,
      "validation_status_allowed": ["unvalidated", "plant_tested"],
      "acceptance_eligible": false
    }
  ],
  "absence_adjudication_default": "PRESENT (E7)"
}
```
