"""Terminal round receipts + executable E2E classification (PMR-1 / §3).

The E2E criterion is derived from ACTUAL EXECUTION RECEIPTS, never from
registry labels, directory existence, or freeze manifests alone.

Positive case counts as E2E only if the receipt shows the FULL traversal:
source → FactRecords → MechanismHypothesis → Faultness Gate → accepted fault
RuleCard → refutation/validation terminal state.

Negative case counts as E2E only if it traverses the same solver path far
enough to EXPLICITLY establish: candidate fault hypothesis rejected at the
Faultness Gate, candidate RuleCard REFUTED, or a defined NO_FAULT terminal.

A mere freeze manifest does not count. A detector-only or EvidenceGate-only
tripwire does not count. An ``insufficient_evidence`` safety result is useful
but is NOT a full fault-discovery E2E case — it classifies as SAFETY_ONLY
when it is the T2-tripwire scenario (E3 single-surface refusal recorded).
"""
from __future__ import annotations

TERMINAL_CLASSES = ("e2e_positive", "e2e_negative", "safety_only", "not_e2e")

# Frozen refutation/validation terminal states for an accepted fault card
# (REFUTATION_STATE_MODEL.md set; REFUTED arrives via the 5-state machine).
CARD_TERMINAL_STATES = ("NOT_REFUTED_WITHIN_SCOPE", "REFUTED")


def build_round_receipt(
    *,
    case_id: str,
    round_dir,
    staged: dict,
    accepted_cards: list[dict],
    demotions: list[dict],
    outcomes: list,
    coherence_violations: list[str],
    fact_rows: list[dict],
) -> dict:
    """Assemble the terminal execution receipt for one round. The acceptance
    runner reads THIS file — not directory listings, not registry kinds."""
    from ..k2.statement_render import CONFIG_SYMBOL_RE

    refusals = staged["custody"].rows() if hasattr(staged["custody"], "rows") else []
    t2_scenarios = []
    for r in refusals:
        if str(r.get("kind", "")) == "refusal" and r.get("code") == "E3":
            t2_scenarios.append({"code": "E3", "detail": str(r.get("detail"))[:200]})
    abstentions = []
    for oc in outcomes:
        if oc.kind == "insufficient_evidence":
            abstentions.append({"kind": "insufficient_evidence",
                                "missing_evidence": oc.missing_evidence})
            # T2-touching scenario: an E3 (two-surface) gate refusal named in
            # the outcome's missing-evidence list
            for me in oc.missing_evidence or []:
                if isinstance(me, dict) and me.get("rule") == "E3":
                    t2_scenarios.append({"code": "E3", "detail": str(me.get("why_needed"))[:200]})
    return {
        "case_id": case_id,
        "round_dir": str(round_dir),
        "freeze_id": staged["freeze_id"],
        "build_id": staged["build_id"],
        "lineage": staged["build_inputs"].get("lineage", ""),
        "executor": staged["build_inputs"].get("executor", ""),
        "traversal": {
            "source_staged": True,
            "fact_records": len(fact_rows),
            "mechanism_hypotheses": sum(1 for oc in outcomes if oc.hypothesis),
            "faultness_gate_evaluated": len(accepted_cards) + len(demotions),
            "faultness_gate_demoted": len(demotions),
            "rulecards_accepted": len(accepted_cards),
        },
        "accepted_fault_cards": [
            {"rulecard_id": c["rulecard_id"], "family": c["fault_class"], "polarity": c["polarity"],
             "faultness_status": c["validation"]["faultness_status"],
             "evidence_integrity_status": c["validation"]["evidence_integrity_status"],
             "refutation_state": c["validation"]["refutation_state"]}
            for c in accepted_cards
        ],
        "rejected_candidates": [
            {"rulecard_id": d["rulecard_id"], "family": d["family"],
             "failed_roles": d["failed_roles"], "reason": "faultness_gate_failed"}
            for d in demotions
        ],
        "abstentions": abstentions,
        "t2_scenarios": t2_scenarios,
        "semantic_coherence_violations": coherence_violations,
    }


def classify_round_receipt(receipt: dict) -> str:
    """Executable E2E criterion (§3). Pure function of the receipt content;
    fail-closed on anything it cannot establish."""
    if not isinstance(receipt, dict):
        return "not_e2e"
    t = receipt.get("traversal") or {}
    accepted = receipt.get("accepted_fault_cards") or []
    rejected = receipt.get("rejected_candidates") or []
    abstentions = receipt.get("abstentions") or []
    t2 = receipt.get("t2_scenarios") or []
    refutation_states = {c.get("refutation_state") for c in accepted}

    # positive: full traversal with >=1 accepted fault card at a terminal
    # refutation state, faultness PASS, no coherence violations
    if (t.get("source_staged") and t.get("fact_records", 0) > 0
            and t.get("mechanism_hypotheses", 0) > 0
            and accepted
            and all(c.get("faultness_status") == "PASS" for c in accepted)
            and all(c.get("evidence_integrity_status") == "PASS" for c in accepted)
            and all(s in CARD_TERMINAL_STATES for s in refutation_states)
            and not (receipt.get("semantic_coherence_violations") or [])):
        return "e2e_positive"

    # negative: explicit rejection/REFUTED/NO_FAULT terminal from the solver path
    if accepted and any(c.get("refutation_state") == "REFUTED" for c in accepted):
        return "e2e_negative"
    if rejected and not accepted:
        return "e2e_negative"

    # safety: T2-tripwire scenario (E3 refusal recorded), no fault promotion
    if abstentions and t2 and not accepted and not rejected:
        return "safety_only"

    # no-fault terminal: abstentions only, but a fault hypothesis was traversed
    # and rejected/abstained — accepted as a negative E2E ONLY with an explicit
    # no-fault terminal marker (hypothesis-level abstention alone is not
    # enough; without the marker it is an under-traversal, never a negative).
    if abstentions and not accepted and t.get("mechanism_hypotheses", 0) > 0:
        return "e2e_negative" if receipt.get("no_fault_terminal") else "not_e2e"

    if abstentions:
        return "not_e2e"
    return "not_e2e"
