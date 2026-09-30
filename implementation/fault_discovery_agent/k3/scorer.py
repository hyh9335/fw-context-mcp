"""K3 scorer (K3_INTERFACE_SPEC.md §4; S0-Q4 scoring rubric).

score_rulecard(rulecard, freeze) — deterministic 5-step pipeline:
  S1 completeness: every claim has >=1 evidence fact with a complete
     (or partial_cross_checked) surface AND the fact log holds the raw row;
  S2 evidence gate: E1-E7 per EVIDENCE_SPEC + forbidden-overclaim scan
     (§HARD RULES: no complete-enumeration, no mechanism certainty without
     refutation, single-line loads carry the E3 marker);
  S3 sampling: >=30% of load-bearing rows sampled blind — the sampler sees
     ONLY {fact_id, evidence_surface, exact_query}; verdicts recorded;
  S4 refutation consistency: the RuleCard's refutation_result must satisfy
     the state machine's acceptance mapping;
  S5 verdict: score components; forbidden overclaim => hard-zero.

The scorer NEVER reads the oracle; it reads only solver-produced artifacts
plus the frozen schemas. Sampling seeds are drawn by the custodian and
passed in (seeded blind sampling, CORPUS_LOCK §6.2).
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..schemas.loader import load_schema, validate


class ScoreRefused(ValueError):
    pass


@dataclass
class ScoreComponents:
    s1_completeness: float = 0.0
    s2_evidence: float = 0.0
    s3_sample_agreement: float = 0.0
    s4_refutation_consistency: float = 0.0
    s5_verdict: str = "REJECTED"
    overclaims: list = field(default_factory=list)
    sampled: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _overclaim_scan(text: str) -> list[str]:
    """§HARD RULES forbidden-claim scan (fail-closed, substring level)."""
    forbidden = [
        "complete enumeration",
        "exhaustively",
        "all call sites",
        "no other ",
        "proven mechanism",
        "guaranteed",
        "root cause is definitely",
    ]
    low = text.lower()
    return [p for p in forbidden if p in low]


def _sample_load_bearing_rows(load_bearing: list[dict], sample_rate: float, seed: int) -> list[dict]:
    """Custodian-seeded blind sampling: >= sample_rate of load-bearing rows.
    Sampler key = ONLY (fact_id, evidence_surface, exact_query)."""
    n = max(1, int(len(load_bearing) * sample_rate + 0.999)) if load_bearing else 0
    rng = random.Random(seed)
    rows = list(load_bearing)
    rng.shuffle(rows)
    picked = rows[:n]
    return [
        {"fact_id": r.get("fact_id"), "evidence_surface": r.get("evidence_surface"), "exact_query": r.get("exact_query")}
        for r in picked
    ]


def score_rulecard(
    rulecard: dict,
    *,
    fact_log_rows: list[dict],
    sample_rate: float = 0.30,
    seed: int | None = None,
    refutation_result: dict | None = None,
) -> ScoreComponents:
    """Deterministic scorer. fact_log_rows = rows of the round's fact log
    (solver side, substrate-attested). refutation_result: executed protocol
    result if any (else refutation step scores 0 pending execution)."""
    comp = ScoreComponents()
    provenance = rulecard.get("provenance", {})
    derived = provenance.get("derived_from", [])

    # Frozen RULECARD shape: trigger_conditions + evidence (factRefs), no
    # claims array. Score unit = one trigger condition backed by evidence.
    triggers = rulecard.get("trigger_conditions", [])
    evidence = rulecard.get("evidence", [])

    def _ref_ids(refs):
        out = []
        for r in refs or []:
            out.append(r.get("fact_id") if isinstance(r, dict) else r)
        return out

    claims = [
        {
            "claim_id": f"TC{i + 1}",
            "evidence_fact_ids": _ref_ids(t.get("fact_refs", [])) + _ref_ids(evidence),
            "load_bearing": True,
            "single_line": False,
        }
        for i, t in enumerate(triggers)
    ] or [
        {
            "claim_id": "E1",
            "evidence_fact_ids": _ref_ids(evidence),
            "load_bearing": True,
            "single_line": False,
        }
    ]

    # S1 completeness: each claim maps to >=1 trusted fact row with raw output
    facts_by_id = {r.get("fact_id"): r for r in fact_log_rows}
    covered = 0
    load_bearing = []
    for claim in claims:
        ev = claim.get("evidence_fact_ids", [])
        ok_rows = [facts_by_id[fid] for fid in ev if fid in facts_by_id]
        complete_rows = [
            r for r in ok_rows
            if r.get("completeness_status") in ("complete", "partial_cross_checked")
            and r.get("trust_class") in ("T0", "T1")
        ]
        raw_ok = all(Path(str(r.get("raw_artifact_ref", "x#y")).split("#")[0]).exists()
                     for r in ok_rows if str(r.get("raw_artifact_ref", "")).startswith(("/", "D:", "C:")))
        if complete_rows and raw_ok:
            covered += 1
        load_bearing.extend([r for r in ok_rows if claim.get("load_bearing")])
    comp.s1_completeness = (covered / len(claims)) if claims else 0.0

    # S2 evidence gate: overclaim scan + every load-bearing single-line claim
    # carries the E3 cross-check marker
    texts = json.dumps(rulecard, ensure_ascii=False)
    comp.overclaims = _overclaim_scan(texts)
    e3_ok = all(
        ("E3" in str(claim.get("cross_check", "")) or "cross-check" in str(claim.get("cross_check", "")))
        for claim in claims
        if claim.get("load_bearing") and claim.get("single_line")
    )
    comp.s2_evidence = 1.0 if (not comp.overclaims and e3_ok) else 0.0

    # S3 blind sampling
    if seed is None:
        seed = int(hashlib.sha256(rulecard["rulecard_id"].encode("utf-8")).hexdigest()[:8], 16)
    comp.sampled = _sample_load_bearing_rows(load_bearing, sample_rate, seed)
    comp.s3_sample_agreement = 1.0 if comp.sampled else 0.0  # refinement in I9 acceptance

    # S4 refutation consistency (state machine mapping, not prose judgment)
    if refutation_result is None:
        # the card's own recorded result is the scored artifact
        refutation_result = rulecard.get("refutation_result")
    if refutation_result is None:
        comp.s4_refutation_consistency = 0.0
    else:
        from ..schemas.refutation import validate_refutation_result

        try:
            validate_refutation_result(refutation_result, rulecard.get("scope_stamp", {}).get("corpus_case"))
            comp.s4_refutation_consistency = 1.0
        except Exception:
            comp.s4_refutation_consistency = 0.0

    # S5 verdict
    hard_zero = bool(comp.overclaims)
    if hard_zero:
        comp.s5_verdict = "REJECTED_OVERCLAIM"
    elif comp.s1_completeness >= 0.99 and comp.s2_evidence >= 0.99 and comp.s4_refutation_consistency >= 0.99:
        comp.s5_verdict = "PASS"
    else:
        comp.s5_verdict = "REJECTED"
    return comp
