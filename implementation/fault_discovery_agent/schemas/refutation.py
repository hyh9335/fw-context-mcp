"""Refutation state machine (REFUTATION_STATE_MODEL.md, frozen Wave-1 contract).

Implements exactly the five states, the allowed transitions (§2), the
validation_status mapping (§4) and the acceptance matrix (§3). The state
``NOT_REFUTED_WITHIN_SCOPE`` is NEVER collapsed into "proven true".
"""
from __future__ import annotations

from typing import Optional

STATES = (
    "UNEXECUTED",
    "REFUTED",
    "NOT_REFUTED_WITHIN_SCOPE",
    "INCONCLUSIVE",
    "ORACLE_CONFLICT",
)

VALIDATION_STATUS_ALLOWED = {
    "UNEXECUTED": {"unvalidated", "plant_tested"},
    "REFUTED": {"rejected"},
    "NOT_REFUTED_WITHIN_SCOPE": {"refutation_executed", "accepted"},
    "INCONCLUSIVE": {"unvalidated", "plant_tested"},
    "ORACLE_CONFLICT": {"unvalidated", "plant_tested"},
}

# §3 acceptance matrix (machine block of REFUTATION_STATE_MODEL.md §5).
ACCEPTANCE_ELIGIBLE = {
    # (state, validation_status, corpus_case, is_expected_refutation_candidate)
    ("NOT_REFUTED_WITHIN_SCOPE", "refutation_executed", None, False): True,
    ("NOT_REFUTED_WITHIN_SCOPE", "accepted", None, False): True,
    # C6 expected-refutation case: scored success, never promoted to accepted.
    ("REFUTED", "rejected", "C6", True): True,
}


class RefutationTransitionError(ValueError):
    pass


def check_transition(from_state: str, to_state: str, *, re_frozen: bool = False) -> None:
    """Validate a refutation_result state transition (§2)."""
    if from_state not in STATES or to_state not in STATES:
        raise RefutationTransitionError(f"unknown state: {from_state!r} -> {to_state!r}")
    if from_state == "UNEXECUTED":
        allowed = {"REFUTED", "NOT_REFUTED_WITHIN_SCOPE", "INCONCLUSIVE", "ORACLE_CONFLICT"}
    elif from_state == "INCONCLUSIVE":
        allowed = {"REFUTED", "NOT_REFUTED_WITHIN_SCOPE", "ORACLE_CONFLICT"}
        if not re_frozen:
            raise RefutationTransitionError(
                "INCONCLUSIVE -> forward requires evidence-gap closure AND re-freeze "
                "(new freeze_id) per REFUTATION_STATE_MODEL.md §2"
            )
    else:
        allowed = set()
        if from_state == "ORACLE_CONFLICT":
            raise RefutationTransitionError(
                "ORACLE_CONFLICT has no forward transition; re-derivation under a new "
                "freeze_id is required (§2)"
            )
    if to_state not in allowed:
        raise RefutationTransitionError(
            f"transition {from_state} -> {to_state} not allowed (§2)"
        )


def check_status_mapping(state: str, protocol_completed: bool, validation_status: str) -> None:
    """Validate validation_status against the §4 mapping table."""
    if state not in STATES:
        raise RefutationTransitionError(f"unknown state: {state!r}")
    allowed = VALIDATION_STATUS_ALLOWED[state]
    if validation_status not in allowed:
        raise RefutationTransitionError(
            f"validation_status {validation_status!r} not allowed for state "
            f"{state} (allowed: {sorted(allowed)})"
        )
    if state in {"REFUTED", "NOT_REFUTED_WITHIN_SCOPE"} and not protocol_completed:
        raise RefutationTransitionError(
            f"state {state} requires protocol_completed=true (§4)"
        )
    if validation_status == "accepted" and not protocol_completed:
        raise RefutationTransitionError(
            "nothing is ever accepted with protocol_completed=false (§4)"
        )


def acceptance_eligible(
    state: str,
    validation_status: str,
    corpus_case: Optional[str],
    *,
    is_expected_refutation_candidate: bool = False,
) -> bool:
    """§3 acceptance matrix. INCONCLUSIVE is never a success; ORACLE_CONFLICT
    blocks acceptance; the C6 expected-refutation candidate scores via
    (REFUTED, rejected) and is never promoted to accepted."""
    if state == "INCONCLUSIVE":
        return False
    if state == "ORACLE_CONFLICT":
        return False
    if is_expected_refutation_candidate:
        return (state == "REFUTED" and validation_status == "rejected")
    return state == "NOT_REFUTED_WITHIN_SCOPE" and validation_status in {
        "refutation_executed",
        "accepted",
    }


def validate_refutation_result(refutation_result: dict, corpus_case: Optional[str] = None) -> None:
    """Cross-check a rulecard's refutation_result object against the §5 block
    (mirrors the RULECARD_SCHEMA allOf clauses; defense in depth on top of the
    JSON-schema validation done by schemas.loader)."""
    state = refutation_result.get("state")
    if state not in STATES:
        raise RefutationTransitionError(f"unknown refutation state: {state!r}")
    protocol_completed = refutation_result.get("protocol_completed")
    cex = refutation_result.get("counterexample_ref")
    if state == "REFUTED":
        if protocol_completed is not True:
            raise RefutationTransitionError("REFUTED requires protocol_completed=true")
        if not cex or not isinstance(cex, str):
            raise RefutationTransitionError("REFUTED requires a non-empty counterexample_ref")
    elif state == "NOT_REFUTED_WITHIN_SCOPE":
        if protocol_completed is not True:
            raise RefutationTransitionError(
                "NOT_REFUTED_WITHIN_SCOPE requires protocol_completed=true"
            )
        if cex is not None:
            raise RefutationTransitionError(
                "NOT_REFUTED_WITHIN_SCOPE requires counterexample_ref=null"
            )
        if refutation_result.get("missing_evidence_refs"):
            raise RefutationTransitionError(
                "NOT_REFUTED_WITHIN_SCOPE requires empty missing_evidence_refs"
            )
    else:
        if cex is not None:
            raise RefutationTransitionError(
                f"counterexample_ref must be null unless state is REFUTED (got {state})"
            )
    if state == "INCONCLUSIVE":
        if not refutation_result.get("missing_evidence_refs"):
            raise RefutationTransitionError(
                "INCONCLUSIVE must enumerate unobtainable required facts "
                "(missing_evidence_refs)"
            )
    is_c6_candidate = corpus_case == "C6"
    if is_c6_candidate and state == "NOT_REFUTED_WITHIN_SCOPE":
        # A negative-control card whose protocol found no counterexample is a
        # FALSE POSITIVE (accepted false RuleCard on C6) — refused at validation.
        raise RefutationTransitionError(
            "C6 candidate with NOT_REFUTED_WITHIN_SCOPE is an accepted false-positive "
            "(major Wave-1 failure); correct outcomes are outright rejection or "
            "(REFUTED, rejected)"
        )
