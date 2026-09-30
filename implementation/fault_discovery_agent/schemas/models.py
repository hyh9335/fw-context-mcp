"""Frozen-data model constructors: FactRecord, MechanismHypothesis, RuleCard.

These are thin, validating constructors over plain dicts (the frozen JSON
schemas are the single source of shape truth). Only K1 mints FactRecords
(K1_INTERFACE_SPEC.md HARD RULE); K2 mints hypotheses/RuleCards.
"""
from __future__ import annotations

from typing import Any, Optional

from . import loader
from .refutation import validate_refutation_result

WAVE1_FAULT_CLASSES = (
    "condition_config_gated",
    "init_submit_pairing",
    "existence_absence",
    "direct_flow_presence",
)


def new_fact_record(
    *,
    fact_id: str,
    source_snapshot_id: str,
    build_snapshot_id: str,
    repository: str,
    commit: str,
    file: str,
    range_start: int,
    range_end: int,
    evidence_surface: str,
    exact_query: str,
    raw_artifact_ref: str,
    trust_class: str,
    provenance: dict,
    certainty: str,
    polarity: str,
    context_identity: str,
    detector_checks: list,
    completeness_status: str,
    freeze_id: str,
    recorded_at: str,
) -> dict:
    """Validate-then-return a FactRecord dict. Sole path through which K1
    materializes evidence rows.

    *provenance* is the closed shape {instrument, channel, k1_mediated}
    (FACT_RECORD_SCHEMA.json additionalProperties: false). *context_identity*
    is a string (run-pinned context id)."""
    provenance = dict(provenance)
    provenance.setdefault("k1_mediated", True)
    extra = set(provenance) - {"instrument", "channel", "k1_mediated"}
    if extra:
        raise ValueError(
            f"provenance carries non-schema keys (additionalProperties: false): {sorted(extra)}"
        )
    record = {
        "fact_id": fact_id,
        "source_snapshot_id": source_snapshot_id,
        "build_snapshot_id": build_snapshot_id,
        "repository": repository,
        "commit": commit,
        "file": file,
        "range_start": range_start,
        "range_end": range_end,
        "evidence_surface": evidence_surface,
        "exact_query": exact_query,
        "raw_artifact_ref": raw_artifact_ref,
        "trust_class": trust_class,
        "provenance": provenance,
        "certainty": certainty,
        "polarity": polarity,
        "context_identity": context_identity,
        "detector_checks": detector_checks,
        "completeness_status": completeness_status,
        "freeze_id": freeze_id,
        "recorded_at": recorded_at,
    }
    return loader.validate(record, "fact_record")


def fact_ref(fact_id: str, trust_class: str, fact_log=None) -> dict:
    """Canonical factRef shape {fact_id, trust_class} shared by the hypothesis
    and rulecard schemas. When *fact_log* is supplied the trust class is
    checked against the recorded row (inherited-trust check; E2)."""
    if fact_log is not None:
        recorded = fact_log.get(fact_id)
        if recorded is None:
            raise ValueError(f"fact_id {fact_id!r} not present in the K1 fact log")
        if recorded["trust_class"] != trust_class:
            raise ValueError(
                f"inherited-trust mismatch for {fact_id}: claimed {trust_class}, "
                f"recorded {recorded['trust_class']}"
            )
    return {"fact_id": fact_id, "trust_class": trust_class}


def new_mechanism_hypothesis(
    *,
    hypothesis_id: str,
    fault_class: str,
    mechanism_steps: list,
    required_facts: list,
    assumptions: list,
    missing_evidence: list,
    forbidden_overclaims: list,
    scope: dict,
    status: str = "candidate",
) -> dict:
    if fault_class not in WAVE1_FAULT_CLASSES:
        raise ValueError(
            f"fault_class {fault_class!r} outside the Wave-1 family set "
            f"{WAVE1_FAULT_CLASSES}"
        )
    hypothesis = {
        "hypothesis_id": hypothesis_id,
        "fault_class": fault_class,
        "mechanism_steps": mechanism_steps,
        "required_facts": required_facts,
        "assumptions": assumptions,
        "missing_evidence": missing_evidence,
        "forbidden_overclaims": forbidden_overclaims,
        "scope": scope,
        "status": status,
    }
    return loader.validate(hypothesis, "mechanism_hypothesis")


def new_rulecard(
    *,
    rulecard_id: str,
    mechanism_id: str,
    hypothesis_ref: str,
    fault_class: str,
    scope_stamp: dict,
    trigger_conditions: list,
    evidence: list,
    provenance: dict,
    polarity: str,
    forbidden_overclaims: list,
    refutation_protocol: dict,
    refutation_result: Optional[dict] = None,
    validation_status: str = "unvalidated",
    oracle_provenance: str = "none",
    validation: Optional[dict] = None,
) -> dict:
    if fault_class not in WAVE1_FAULT_CLASSES:
        raise ValueError(
            f"fault_class {fault_class!r} outside the Wave-1 family set "
            f"{WAVE1_FAULT_CLASSES}"
        )
    if not forbidden_overclaims:
        # S0_GOAL_ADR rubric item 8: free-text rationale never substitutes for
        # structured predicates, and an accepted card always carries
        # forbidden_overclaims (REFUTATION_STATE_MODEL.md §3.6).
        raise ValueError("RuleCard forbidden_overclaims must be non-empty")
    if refutation_result is None:
        refutation_result = {
            "state": "UNEXECUTED",
            "protocol_completed": False,
            "counterexample_ref": None,
            "missing_evidence_refs": [],
            "adjudicated_at": None,
        }
    validate_refutation_result(refutation_result, scope_stamp.get("corpus_case"))
    card = {
        "rulecard_id": rulecard_id,
        "mechanism_id": mechanism_id,
        "hypothesis_ref": hypothesis_ref,
        "fault_class": fault_class,
        "scope_stamp": scope_stamp,
        "trigger_conditions": trigger_conditions,
        "evidence": evidence,
        "provenance": provenance,
        "polarity": polarity,
        "forbidden_overclaims": forbidden_overclaims,
        "refutation_protocol": refutation_protocol,
        "refutation_result": refutation_result,
        "validation_status": validation_status,
        "oracle_provenance": oracle_provenance,
    }
    if validation is not None:
        # wave1-1-semantic-gate-1: evidence_integrity / faultness / refutation
        # are reported SEPARATELY (PMR-4). Cards carrying a validation object
        # validate against the extended package-local schema.
        card["validation"] = validation
        return loader.validate(card, "rulecard_v1_1")
    return loader.validate(card, "rulecard")


def new_refutation_protocol(
    steps: list, oracle_requirement: str = "blind_oracle_custodian"
) -> dict:
    """refutation_protocol object per RULECARD_SCHEMA: steps with
    expected_two_outcomes {rule_holds_when, rule_falsified_when} per step
    (REFUTATION_STATE_MODEL.md §0: the two-outcome notion survives as a-priori
    predictions inside each step, never as a result value)."""
    return {"steps": steps, "oracle_requirement": oracle_requirement}


def new_protocol_step(
    *,
    step_id: str,
    action: str,
    inputs: dict,
    rule_holds_when: str,
    rule_falsified_when: str,
) -> dict:
    if action not in (
        "query_substrate_via_k1",
        "independent_crosscheck",
        "inspect_condition",
        "construct_counterexample_search",
    ):
        raise ValueError(f"protocol action {action!r} outside the frozen action set")
    return {
        "step_id": step_id,
        "action": action,
        "inputs": inputs,
        "expected_two_outcomes": {
            "rule_holds_when": rule_holds_when,
            "rule_falsified_when": rule_falsified_when,
        },
    }
