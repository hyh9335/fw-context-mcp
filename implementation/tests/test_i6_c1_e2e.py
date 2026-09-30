"""I6 tests — C1 E2E fresh-context solving invariants.

The deterministic solve runs ONCE (module scope) into a scratch round dir;
tests assert the E2E contract: schema-valid promoted RuleCards, no forbidden
overclaims, two-part separation, legal refutation states, blind sampling,
custody cleanliness (no oracle/ access), fact-log replayability.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
REPO = IMPL.parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k3.e2e_runner import execute_refutation, solve_case  # noqa: E402
from fault_discovery_agent.k3.scorer import score_rulecard  # noqa: E402
from fault_discovery_agent.schemas.loader import validate  # noqa: E402
from fault_discovery_agent.schemas.refutation import acceptance_eligible, validate_refutation_result  # noqa: E402

CORPUS = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "corpus" / "C1_PFC1_SOURCE_DETERMINISTIC"
ROUND = IMPL / "scratch_i6_c1"
BUILD_DEFINES = {
    "CONFIG_ACCEL_SAMPLE": 1,
    "CONFIG_ACCEL_TRIGGER": 1,
    "CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD": 1,
}


@pytest.fixture(scope="module")
def solved():
    import shutil

    if ROUND.exists():
        shutil.rmtree(ROUND)
    rnd = solve_case("C1", CORPUS, ROUND, build_defines=BUILD_DEFINES, build_id="BUILD-C1-PINNED")
    execute_refutation(rnd, {"build_defines": BUILD_DEFINES, "freeze_id": rnd.freeze_id})
    return rnd


def test_w01_e2e_produces_rulecards_across_all_present_families(solved):
    # wave1-1 repair: the Faultness Gate demotes pattern-only candidates
    # (e.g. a bare config-macro absence = the CAUSE, not a consequence; a
    # region-OUT card with no in-round consequence evidence). C1's fault is
    # expressed by the region card with pairing-cutoff consequence and the
    # init_submit_pairing mechanism card.
    cards = [o.rulecard for o in solved.outcomes if o.kind == "rulecard"]
    assert len(cards) >= 2, "region(pairing-cutoff) + init_submit_pairing must both fire"
    fams = {c["fault_class"] for c in cards}
    assert {"condition_config_gated", "init_submit_pairing"} <= fams
    # every demoted candidate is a first-class REJECTED terminal with named roles
    rejected = [o for o in solved.outcomes if o.kind == "rejected" and getattr(o, "faultness_rejection", None)]
    assert rejected, "pattern-only candidates must be faultness-demoted, not silently dropped"
    assert all(o.faultness_rejection["failed_roles"] for o in rejected)  # noqa: B009


def test_w02_all_promoted_cards_schema_valid(solved):
    for o in solved.outcomes:
        if o.kind == "rulecard":
            # wave1-1: cards carry the validation object -> extended schema
            validate(o.rulecard, "rulecard_v1_1")
            validate(o.hypothesis, "mechanism_hypothesis")


def test_w03_dead_guard_and_pairing_mechanism_found(solved):
    """The planted C1 mechanism: THREAAD typo guard -> submit compiled out."""
    cards = [o.rulecard for o in solved.outcomes if o.kind == "rulecard"]
    dead = [c for c in cards if "GLOBAL_THREAAD" in c["trigger_conditions"][0]["statement"]]
    assert dead, "the dead THREAAD guard must be discovered"
    pair = [c for c in cards if c["fault_class"] == "init_submit_pairing"]
    assert pair, "the broken init/submit pairing must be discovered"
    assert pair[0]["polarity"] == "negative"
    assert "48" in pair[0]["trigger_conditions"][0]["statement"]


def test_w04_two_part_separation_runtime_bars_present(solved):
    """C1 two-part separation: source-level mechanism only; runtime occurrence
    required-but-unavailable — enforced as forbidden overclaims."""
    for o in solved.outcomes:
        if o.kind != "rulecard":
            continue
        bars = " ".join(o.rulecard["forbidden_overclaims"])
        if o.rulecard["fault_class"] == "init_submit_pairing":
            assert "never executes at runtime" in bars
            assert "queue-exhaustion" in bars


def test_w05_refutation_states_legal_and_terminal(solved):
    for rs in solved.refutations:
        assert rs["state"] == "NOT_REFUTED_WITHIN_SCOPE"
        rc = next(o.rulecard for o in solved.outcomes
                  if o.kind == "rulecard" and o.rulecard["rulecard_id"] == rs["rulecard_id"])
        validate_refutation_result(rc["refutation_result"], "C1")
        assert rc["refutation_result"]["counterexample_ref"] is None
        assert rc["refutation_result"]["protocol_completed"] is True
        assert rc["validation_status"] == "refutation_executed"
        assert acceptance_eligible("NOT_REFUTED_WITHIN_SCOPE", "refutation_executed", "C1")


def test_w06_blind_sampling_executed_and_no_overclaims(solved):
    sampled_any = False
    for o in solved.outcomes:
        if o.kind != "rulecard":
            continue
        comp = score_rulecard(o.rulecard, fact_log_rows=solved.gateway.log.all_facts(), sample_rate=0.30)
        assert comp.s5_verdict == "PASS", f"{o.rulecard['rulecard_id']}: {comp.overclaims}"
        assert not comp.overclaims
        assert len(comp.sampled) >= 1
        sampled_any = True
    assert sampled_any


def test_w07_custody_log_clean_no_oracle_access(solved):
    log_path = ROUND / "custody_log.jsonl"
    rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    assert not [r for r in rows if r.get("kind") == "freeze_breach"], "oracle/ access must never occur"
    kinds = {r["kind"] for r in rows}
    assert {"freeze_verified", "bundle_assembled", "solving_started", "solving_finished"} <= kinds


def test_w08_fact_log_replayable_and_pinned(solved):
    rows = [json.loads(l) for l in (ROUND / "fact_log.jsonl").read_text(encoding="utf-8").splitlines()]
    facts = [r["record"] for r in rows if r.get("kind") == "fact"]
    refusals = [r for r in rows if r.get("kind") == "refusal"]
    assert facts, "the solving round must mint facts"
    assert refusals, "the THREAAD fallback lookup must be refused (G6/C3)"
    for r in facts:
        assert r["freeze_id"] == solved.freeze_id
        assert r["build_snapshot_id"] == "BUILD-C1-PINNED"
        assert r["trust_class"] in ("T0", "T1", "T2")
