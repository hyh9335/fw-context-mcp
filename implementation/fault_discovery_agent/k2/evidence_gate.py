"""K2 EvidenceGate (FR-2 E1–E7; K2_INTERFACE_SPEC.md §4).

Every decision is logged with the rule id; a refusal always names the
failing rule. REFUSE blocks promotion of the claim/hypothesis; DEGRADE
marks provenance degraded and bars load-bearing use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

COMPLETE_CLASSES = ("T0", "T1")


@dataclass
class GateDecision:
    rule: str
    outcome: str  # ALLOW | DEGRADE | REFUSE
    detail: str


@dataclass
class GateReport:
    decisions: list[GateDecision] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return any(d.outcome == "REFUSE" for d in self.decisions)

    def refusals(self) -> list[GateDecision]:
        return [d for d in self.decisions if d.outcome == "REFUSE"]


def _facts_by_id(fact_rows: list[dict]) -> dict:
    return {r["fact_id"]: r for r in fact_rows}


def run_evidence_gate(
    rich_steps: list[dict],
    fact_rows: list[dict],
    *,
    pinned_build_snapshot_id: str,
    hypothesis_text: str = "",
) -> GateReport:
    """E1–E7 over the loop's RICH steps (the frozen MECHANISM_HYPOTHESIS_SCHEMA
    step shape is closed — {step_id, description, required_fact_ids,
    step_kind} — so discipline fields live on the rich form, gate-side only).
    factRef trust_class must match the fact log UNCHANGED — E1."""
    report = GateReport()
    by_id = _facts_by_id(fact_rows)
    steps = rich_steps
    refs = []
    for step in steps:
        refs.extend(step.get("required_fact_ids", []))
    refs = sorted(set(refs))
    missing = [r for r in refs if r not in by_id]
    if missing:
        report.decisions.append(GateDecision("E1", "REFUSE", f"fact refs absent from fact log: {missing}"))
        return report

    # E1: provenance-complete chain; trust inherited unchanged
    for r in refs:
        row = by_id[r]
        prov = row.get("provenance") or {}
        if not (prov.get("instrument") and prov.get("channel") is not None and "k1_mediated" in prov):
            report.decisions.append(GateDecision("E1", "REFUSE", f"fact {r} provenance incomplete"))
        if row.get("raw_artifact_ref") in (None, "", "inline") and row.get("evidence_surface") != "other_independent":
            report.decisions.append(GateDecision("E1", "REFUSE", f"fact {r} has no raw output anchor"))

    # E2: T2 never load-bearing; T1 INDEPENDENT-channel rows must not claim
    # K1 mediation (gateway-disciplined T1 facts on substrate surfaces are
    # legitimate — the rule targets independent_* surfaces only)
    load_bearing_ids = []
    for step in steps:
        if step.get("step_kind") in ("condition", "definition", "call_flow", "config"):
            load_bearing_ids.extend(step.get("required_fact_ids", []))
    load_bearing_ids = sorted(set(load_bearing_ids))
    for r in load_bearing_ids:
        row = by_id[r]
        if row.get("trust_class") == "T2":
            report.decisions.append(GateDecision("E2", "REFUSE", f"T2 fact {r} is load-bearing"))
        if row.get("trust_class") == "T1" and str(row.get("evidence_surface", "")).startswith("independent_") \
                and (row.get("provenance") or {}).get("k1_mediated") is True:
            report.decisions.append(GateDecision("E2", "REFUSE", f"independent-channel fact {r} claims K1 mediation it cannot have"))
        if row.get("trust_class") == "T0" and row.get("completeness_status") not in ("complete", "partial_cross_checked"):
            report.decisions.append(GateDecision("E2", "REFUSE", f"T0 fact {r} incomplete without cross-check marker"))
    # snapshot compatibility (GA-4): every load-bearing fact pinned to the
    # round's build identity
    for r in load_bearing_ids:
        row = by_id[r]
        if row.get("build_snapshot_id") != pinned_build_snapshot_id:
            report.decisions.append(GateDecision("E2", "REFUSE", f"fact {r} pinned to foreign build snapshot {row.get('build_snapshot_id')}"))

    # E3: single-line/fragment load-bearing needs >=2 surfaces
    by_surface: dict[str, set] = {}
    for step in steps:
        for r in step.get("required_fact_ids", []):
            row = by_id.get(r)
            if row:
                by_surface.setdefault(str(step.get("step_id")), set()).add(row.get("evidence_surface"))
    for step in steps:
        if step.get("single_line") and step.get("load_bearing", True):
            surfaces = by_surface.get(str(step.get("step_id")), set())
            if len(surfaces) < 2:
                report.decisions.append(GateDecision(
                    "E3", "REFUSE",
                    f"single-line step {step.get('step_id')} has {len(surfaces)} surface(s) (<2): {sorted(surfaces)}"
                ))

    # E4: reachability NEGATIVE needs root-set attestation (enumerated attempts)
    for step in steps:
        if step.get("polarity") == "negative" and step.get("step_kind") == "call_flow":
            attempts = step.get("enumerated_attempts") or []
            if len(attempts) < 1:
                report.decisions.append(GateDecision("E4", "REFUSE", f"negative reachability step {step.get('step_id')} without enumerated attempts"))

    # E6: macro-value evidence value-decisive or load-bearing
    for r in load_bearing_ids:
        row = by_id[r]
        if (row.get("certainty") == "degraded") or any(
            str(c).startswith("G8") for c in (row.get("detector_checks") or [])
        ):
            report.decisions.append(GateDecision("E6", "DEGRADE", f"fact {r} is macro-value/degraded — never load-bearing"))
            # degraded fact in a load-bearing step => the claim cannot carry
            # a value-decisive predicate
            if step_is_value_decisive(hypothesis_text):
                report.decisions.append(GateDecision("E6", "REFUSE", "value-decisive claim built on degraded macro-value evidence"))

    # E7: negative claim without enumerated attempts; ambiguous absence used as absence
    for step in steps:
        if step.get("step_kind") == "absence":
            row = by_id.get((step.get("required_fact_ids") or [None])[0], {})
            if row.get("polarity") != "negative" or not row.get("exact_query"):
                report.decisions.append(GateDecision("E7", "REFUSE", f"absence step {step.get('step_id')} lacks an explicit enumerated probe"))
            if row.get("completeness_status") == "partial_cross_checked":
                report.decisions.append(GateDecision("E7", "REFUSE", f"ambiguous absence in step {step.get('step_id')}"))
    return report


def step_is_value_decisive(text: str) -> bool:
    markers = ("value is", "equals", "==", "!=", "value comparison decides", "the value of", "decides the branch")
    return any(m in text for m in markers)


def json_text(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
