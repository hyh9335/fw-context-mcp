"""I1 RED/GREEN tests — schemas + freeze plumbing (WAVE1_IMPLEMENTATION_PLAN.md step 1).

RED tests (written FIRST, per plan):
  T01 FactRecord missing freeze_id rejected
  T02 RuleCard with empty forbidden_overclaims rejected
  T03 refutation_result REFUTED with protocol_completed=false rejected (§5 allOf)
  T04 canonical JSON key-sort/LF/no-trailing-whitespace deterministic
  T05 combined-hash recomputation reproduces the CORPUS_LOCK_PLAN §5 baseline recipe
  T06 any byte change => hash change + new freeze_id required
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.schemas import (  # noqa: E402
    SchemaValidationError,
    canonical_json_bytes,
    combined_manifest_hash,
    file_sha256,
    fixture_tree_hash,
    manifest_line,
    validate_refutation_result,
)
from fault_discovery_agent.schemas.loader import load_schema, validate  # noqa: E402
from fault_discovery_agent.schemas.models import (  # noqa: E402
    fact_ref,
    new_fact_record,
    new_mechanism_hypothesis,
    new_protocol_step,
    new_refutation_protocol,
    new_rulecard,
)
from fault_discovery_agent.schemas.refutation import (  # noqa: E402
    RefutationTransitionError,
    acceptance_eligible,
    check_status_mapping,
    check_transition,
)
from fault_discovery_agent.freeze import (  # noqa: E402
    FreezeRefused,
    build_manifest,
    mint_freeze_id,
    verify_manifest,
    write_manifest,
)

SPEC = IMPL.parent / "fault_discovery_goal_architecture_agent_team" / "outputs"


def _fact(**over):
    base = dict(
        fact_id="F-C1-0001",
        source_snapshot_id="SNAP-C1",
        build_snapshot_id="BUILD-C1-A",
        repository="fixture:C1_PFC1_SOURCE_DETERMINISTIC",
        commit="frozen-fixture",
        file="fixture/accel_trigger.c",
        range_start=40,
        range_end=55,
        evidence_surface="fw_context_read_file",
        exact_query="read_file(file_path='fixture/accel_trigger.c')",
        raw_artifact_ref="rounds/R-C1/fact_log.jsonl#1",
        trust_class="T0",
        provenance={
            "instrument": "fw-context 0.31.0 @ d8f08a4",
            "channel": "in-band substrate",
            "k1_mediated": True,
        },
        certainty="measured",
        polarity="positive",
        context_identity="run-ctx-1",
        detector_checks=[],
        completeness_status="complete",
        freeze_id="FRZ-C1-20260921T000000Z",
        recorded_at="2026-09-21T00:00:00Z",
    )
    base.update(over)
    return base


def _rulecard(**over):
    base = dict(
        rulecard_id="RC-C1-001",
        mechanism_id="M-C1-001",
        hypothesis_ref="H-C1-PAIRING-01",
        fault_class="init_submit_pairing",
        scope_stamp={
            "goal_item": "1-3",
            "corpus_case": "C1",
            "lineage": "A",
            "schema_version": "wave1-design-gate-1",
        },
        trigger_conditions=[
            {
                "predicate_id": "P1",
                "kind": "config_symbol",
                "fact_refs": [fact_ref("F-C1-0001", "T0")],
                "statement": "CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD selected",
                "negatable": True,
            }
        ],
        evidence=[fact_ref("F-C1-0001", "T0")],
        provenance={
            "derived_from": ["fw_context_read_file"],
            "certainty": "measured",
            "k1_mediated": True,
        },
        polarity="positive",
        forbidden_overclaims=["queue full occurred"],
        refutation_protocol=new_refutation_protocol(
            [
                new_protocol_step(
                    step_id="S1",
                    action="inspect_condition",
                    inputs={"file": "fixture/accel_trigger.c", "line": 47},
                    rule_holds_when="guard text uses the misspelled token",
                    rule_falsified_when="guard uses the correct token",
                )
            ]
        ),
    )
    base.update(over)
    return base


# ── T01 ──────────────────────────────────────────────────────────────────────
def test_t01_fact_record_missing_freeze_id_rejected():
    bad = _fact()
    del bad["freeze_id"]
    with pytest.raises(SchemaValidationError):
        validate(bad, "fact_record")


# ── T02 ──────────────────────────────────────────────────────────────────────
def test_t02_rulecard_empty_forbidden_overclaims_rejected():
    with pytest.raises(ValueError):
        new_rulecard(**_rulecard(forbidden_overclaims=[]))


# ── T03 ──────────────────────────────────────────────────────────────────────
def test_t03_refuted_with_protocol_incomplete_rejected():
    rr = {
        "state": "REFUTED",
        "protocol_completed": False,
        "counterexample_ref": "F-C6-0002",
        "missing_evidence_refs": [],
        "adjudicated_at": "2026-09-21T00:00:00Z",
    }
    with pytest.raises(RefutationTransitionError):
        validate_refutation_result(rr)
    with pytest.raises(RefutationTransitionError):
        check_status_mapping("REFUTED", False, "rejected")


# ── T04 ──────────────────────────────────────────────────────────────────────
def test_t04_canonical_json_deterministic():
    a = {"b": 1, "a": {"z": [1, 2], "y": "x"}, "c": 1.5}
    b = {"c": 1.5, "a": {"y": "x", "z": [1, 2]}, "b": 1}
    ba, bb = canonical_json_bytes(a), canonical_json_bytes(b)
    assert ba == bb
    assert b"\n" not in ba  # no insignificant whitespace/newlines inside

    def _check_sorted(obj, path="<root>"):
        if isinstance(obj, dict):
            keys = list(obj.keys())
            assert keys == sorted(keys), f"keys not sorted at {path}: {keys}"
            for k, v in obj.items():
                _check_sorted(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check_sorted(v, f"{path}[{i}]")

    _check_sorted(json.loads(ba))
    assert json.loads(ba) == a


# ── T05 ──────────────────────────────────────────────────────────────────────
def test_t05_combined_hash_reproduces_corpus_lock_recipe():
    """Reproduce the CORPUS_LOCK_PLAN §5 baseline recipe: per-file sha256 lines,
    two spaces, sorted byte-wise, sha256 over the concatenation."""
    fixture_root = SPEC / "corpus" / "C1_PFC1_SOURCE_DETERMINISTIC" / "fixture"
    if not fixture_root.is_dir():
        pytest.skip("C1 fixture not materialized")
    fh, entries = fixture_tree_hash(fixture_root)
    # PLANT_SPEC frozen recipe: relpath-first lines, sorted by relpath
    import hashlib

    blob = "".join(f"{r}  {d}\n" for r, d in entries).encode("utf-8")
    assert hashlib.sha256(blob).hexdigest() == fh
    # CORPUS_LOCK §2.3 combined-manifest recipe over the same file set is a
    # DIFFERENT canonical line form (digest-first); hand-recompute it:
    lines = [manifest_line(d, rel) for rel, d in entries]
    ordered = sorted(lines, key=lambda l: l[66:].encode("utf-8"))
    assert combined_manifest_hash(lines) == hashlib.sha256(
        "".join(ordered).encode("utf-8")
    ).hexdigest()


# ── T06 ──────────────────────────────────────────────────────────────────────
def test_t06_byte_change_changes_hash_and_forces_new_freeze(tmp_path):
    round_root = tmp_path / "round"
    snap = round_root / "source_snapshot"
    fixture = snap / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "a.txt").write_bytes(b"one\n")
    cf = round_root / "corpus_fixture"
    cf.mkdir()
    (cf / "a.txt").write_bytes(b"one\n")
    build_cfg = round_root / "build_config"
    build_cfg.mkdir()
    (build_cfg / "cc.json").write_text("[]")
    manifest = build_manifest(
        case="C1",
        round_root=round_root,
        source_snapshot=snap,
        build_config=build_cfg,
        corpus_fixture=cf,
        oracle_hashes={"FAULT_ORACLE.json": "0" * 64},
        agent_identity={"impl": "fault_discovery_agent 0.1.0"},
        solver_bundle_list=["source_snapshot/fixture/a.txt"],
        custodian_only_excluded=["oracle/FAULT_ORACLE.json"],
    )
    mpath = round_root / "FREEZE_MANIFEST.json"
    write_manifest(manifest, mpath)
    verify_manifest(mpath, round_root)  # ok
    # any byte change => hash change + new freeze_id required
    (cf / "a.txt").write_bytes(b"one!\n")
    with pytest.raises(FreezeRefused):
        verify_manifest(mpath, round_root)
    # a changed artifact re-runs the round under a NEW hash (S0-Q1 item 4);
    # freeze_id format has 1-second granularity, so mint the successor +1s
    from datetime import datetime, timedelta, timezone

    base_ts = datetime.strptime(
        manifest["freeze_id"].rsplit("-", 1)[1], "%Y%m%dT%H%M%SZ"
    ).replace(tzinfo=timezone.utc)
    manifest2 = build_manifest(
        case="C1",
        round_root=round_root,
        source_snapshot=snap,
        build_config=build_cfg,
        corpus_fixture=cf,
        oracle_hashes={"FAULT_ORACLE.json": "0" * 64},
        agent_identity={"impl": "fault_discovery_agent 0.1.0"},
        solver_bundle_list=["source_snapshot/fixture/a.txt"],
        custodian_only_excluded=["oracle/FAULT_ORACLE.json"],
        freeze_id=mint_freeze_id("C1", now=base_ts + timedelta(seconds=1)),
    )
    assert manifest2["combined_manifest_sha256"] != manifest["combined_manifest_sha256"]
    assert manifest2["freeze_id"] != manifest["freeze_id"]
    m2 = round_root / "FREEZE_MANIFEST_2.json"
    write_manifest(manifest2, m2)
    verify_manifest(m2, round_root)


# ── schema frozen-file integrity ─────────────────────────────────────────────
def test_schemas_load_from_frozen_outputs():
    for kind in ("fact_record", "mechanism_hypothesis", "rulecard"):
        s = load_schema(kind)
        assert s["$id"]


def test_valid_fact_and_rulecard_roundtrip():
    f = validate(_fact(), "fact_record")
    assert f["fact_id"] == "F-C1-0001"
    card = new_rulecard(**_rulecard())
    assert card["refutation_result"]["state"] == "UNEXECUTED"


def test_transitions_matrix():
    check_transition("UNEXECUTED", "REFUTED")
    check_transition("UNEXECUTED", "NOT_REFUTED_WITHIN_SCOPE")
    check_transition("UNEXECUTED", "INCONCLUSIVE")
    check_transition("UNEXECUTED", "ORACLE_CONFLICT")
    with pytest.raises(RefutationTransitionError):
        check_transition("INCONCLUSIVE", "REFUTED", re_frozen=False)
    check_transition("INCONCLUSIVE", "REFUTED", re_frozen=True)
    with pytest.raises(RefutationTransitionError):
        check_transition("ORACLE_CONFLICT", "NOT_REFUTED_WITHIN_SCOPE")
    with pytest.raises(RefutationTransitionError):
        check_transition("REFUTED", "NOT_REFUTED_WITHIN_SCOPE")


def test_not_refuted_is_never_proven_true():
    """The NOT_REFUTED_WITHIN_SCOPE collapse guard."""
    assert acceptance_eligible("NOT_REFUTED_WITHIN_SCOPE", "refutation_executed", "C1")
    assert acceptance_eligible("NOT_REFUTED_WITHIN_SCOPE", "accepted", "C1")
    assert not acceptance_eligible("INCONCLUSIVE", "unvalidated", "C1")
    assert not acceptance_eligible("ORACLE_CONFLICT", "unvalidated", "C1")
    # C6 candidate: success ONLY via (REFUTED, rejected); never accepted.
    assert acceptance_eligible("REFUTED", "rejected", "C6", is_expected_refutation_candidate=True)
    assert not acceptance_eligible(
        "NOT_REFUTED_WITHIN_SCOPE", "accepted", "C6", is_expected_refutation_candidate=True
    )


def test_c6_accepted_false_positive_refused_at_validation():
    rr = {
        "state": "NOT_REFUTED_WITHIN_SCOPE",
        "protocol_completed": True,
        "counterexample_ref": None,
        "missing_evidence_refs": [],
        "adjudicated_at": "2026-09-21T00:00:00Z",
    }
    with pytest.raises(RefutationTransitionError):
        new_rulecard(**_rulecard(scope_stamp={
            "goal_item": "1-5",
            "corpus_case": "C6",
            "lineage": "B",
            "schema_version": "wave1-design-gate-1",
        }, refutation_result=rr))


def test_inherited_trust_check():
    class _Log:
        def get(self, fid):
            return {"trust_class": "T1"} if fid == "F-X" else None

    assert fact_ref("F-X", "T1", _Log()) == {"fact_id": "F-X", "trust_class": "T1"}
    with pytest.raises(ValueError):
        fact_ref("F-X", "T0", _Log())


def test_hypothesis_rejects_non_wave1_family():
    with pytest.raises(ValueError):
        new_mechanism_hypothesis(
            hypothesis_id="H-X-01",
            fault_class="callback_propagation",
            mechanism_steps=[
                {"step_id": "S1", "description": "d", "required_fact_ids": ["F-C1-0001"], "step_kind": "condition"}
            ],
            required_facts=[fact_ref("F-C1-0001", "T0")],
            assumptions=[],
            missing_evidence=[],
            forbidden_overclaims=["x"],
            scope={"goal_item_ref": "1", "corpus_ref": "C1", "lineage": "A"},
        )
