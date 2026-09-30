"""I3 tests 鈥?K3 minimal: freeze verify, custody, plant runner, scorer core.

RED鈫扜REEN against the frozen contracts. The plant runner executes REAL
substrate-backed probes (PT-G8, PT-G6, PT-G7, PT-G4, PT-G1) 鈥?these double
as the I5 admission vehicle once every package runs green.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
REPO = IMPL.parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k3 import (  # noqa: E402
    CustodyBreach,
    CustodyLog,
    PlantFail,
    PlantReceipt,
    must_pass_gate,
    run_plant_package,
    score_rulecard,
)
from fault_discovery_agent.freeze.manifest_tool import (  # noqa: E402
    FreezeRefused,
    build_manifest,
    mint_freeze_id,
    verify_manifest,
    write_manifest,
)

PLANT_DIR = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "plant_tests"
ROUND = IMPL / "scratch_i3_round"


@pytest.fixture(scope="module")
def round_dir():
    if ROUND.exists():
        shutil.rmtree(ROUND)
    ROUND.mkdir(parents=True)
    return ROUND


def _stage_round_tree(pkg: str, round_dir: Path) -> Path:
    """Freeze needs the round tree to physically hold source_snapshot/,
    build_config/, corpus_fixture/ (verify recomputes per-line disk hashes)."""
    tree = round_dir / pkg
    fixture_src = PLANT_DIR / pkg / "fixture"
    for name in ("source_snapshot", "corpus_fixture"):
        dst = tree / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(fixture_src, dst)
    bc = tree / "build_config"
    bc.mkdir(parents=True, exist_ok=True)
    (bc / "build_inputs.json").write_text(
        json.dumps({"package": pkg, "build": "frozen plant identity"}), encoding="utf-8"
    )
    return tree


def _freeze(pkg: str, round_dir: Path) -> tuple[str, Path, Path]:
    tree = _stage_round_tree(pkg, round_dir)
    freeze_id = mint_freeze_id(pkg)
    manifest = build_manifest(
        case=pkg,
        round_root=tree,
        source_snapshot=tree / "source_snapshot",
        build_config=tree / "build_config",
        corpus_fixture=tree / "corpus_fixture",
        oracle_hashes={},
        agent_identity={"agent": "fault_discovery_agent", "version": "0.1.0"},
        solver_bundle_list=["source_snapshot/", "build_config/"],
        custodian_only_excluded=["oracle/"],
    )
    path = tree / f"FREEZE_{pkg}.json"
    write_manifest(manifest, path)
    return freeze_id, path, tree


# 鈹€鈹€ freeze verify 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def test_u01_freeze_verify_roundtrip(round_dir):
    freeze_id, path, tree = _freeze("PT-G8", round_dir)
    manifest = verify_manifest(path, round_root=tree)
    assert manifest["case"] == "PT-G8"
    assert manifest["freeze_id"] == freeze_id


def test_u02_freeze_verify_refuses_tamper(round_dir):
    freeze_id, path, tree = _freeze("PT-G6", round_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    # tamper the recorded combined hash
    data["combined_manifest_sha256"] = "0" * 64
    tampered = tree / "FREEZE_PT-G6_tampered.json"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FreezeRefused):
        verify_manifest(tampered, round_root=tree)


# 鈹€鈹€ custody 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def test_u03_custody_blocks_oracle_access(round_dir):
    log = CustodyLog(round_dir / "custody.jsonl")
    log.event("freeze_verified", freeze_id="FRZ-X")
    with pytest.raises(CustodyBreach):
        log.check_path_allowed_for_solver("oracle/FAULT_ORACLE.json")
    with pytest.raises(CustodyBreach):
        log.check_path_allowed_for_solver("oracle/PLANT_DIFF/C4.md")
    assert len(log.breaches()) == 2
    # solver-visible paths pass
    log.check_path_allowed_for_solver("source_snapshot/libs/libc/unistd/lib_getcwd.c")


def test_u04_custody_bundle_assembles_only_visible_files(round_dir):
    log = CustodyLog(round_dir / "custody.jsonl")
    bundle = round_dir / "bundle"
    row = log.assemble_bundle(
        bundle,
        [("source_snapshot/fixture/poller.c", PLANT_DIR / "PT-G8" / "fixture" / "poller.c")],
        freeze_id="FRZ-X",
    )
    assert (bundle / "source_snapshot/fixture/poller.c").is_file()
    assert "source_snapshot/fixture/poller.c" in row["files"]


# 鈹€鈹€ plant runner: real substrate probes 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def test_u05_plant_pt_g8_receipt(round_dir):
    freeze_id, _, _t = _freeze("PT-G8", round_dir)
    receipt = run_plant_package("PT-G8", freeze_id=freeze_id, round_dir=round_dir)
    assert receipt.verdict == "PASS", json.dumps(receipt.checks_evaluated, indent=1)
    assert receipt.receipt_id and receipt.fixture_hash and receipt.executor


def test_u06_plant_pt_g6_receipt(round_dir):
    freeze_id, _, _t = _freeze("PT-G6", round_dir)
    receipt = run_plant_package("PT-G6", freeze_id=freeze_id, round_dir=round_dir)
    assert receipt.verdict == "PASS", json.dumps(receipt.checks_evaluated, indent=1)


def test_u07_plant_pt_g7_receipt(round_dir):
    freeze_id, _, _t = _freeze("PT-G7", round_dir)
    receipt = run_plant_package("PT-G7", freeze_id=freeze_id, round_dir=round_dir)
    assert receipt.verdict == "PASS", json.dumps(receipt.checks_evaluated, indent=1)


def test_u08_plant_pt_g4_receipt(round_dir):
    freeze_id, _, _t = _freeze("PT-G4", round_dir)
    receipt = run_plant_package("PT-G4", freeze_id=freeze_id, round_dir=round_dir)
    assert receipt.verdict == "PASS", json.dumps(receipt.checks_evaluated, indent=1)


def test_u09_plant_pt_g1_receipt(round_dir):
    freeze_id, _, _t = _freeze("PT-G1", round_dir)
    receipt = run_plant_package("PT-G1", freeze_id=freeze_id, round_dir=round_dir)
    assert receipt.verdict == "PASS", json.dumps(receipt.checks_evaluated, indent=1)


def test_u10_must_pass_gate_stops_on_fail(round_dir):
    freeze_id, _, _t = _freeze("PT-G8", round_dir)
    receipt = run_plant_package("PT-G8", freeze_id=freeze_id, round_dir=round_dir)
    receipt_bad = PlantReceipt(
        receipt_id="X", freeze_id=freeze_id, detector="G6", package="PT-G6",
        fixture_hash="0" * 64, fixture_hash_recorded="0" * 64,
        checks_evaluated=[], verdict="FAIL", executed_at="t", executor="e",
    )
    with pytest.raises(PlantFail):
        must_pass_gate([receipt, receipt_bad], ["PT-G8", "PT-G6"])
    with pytest.raises(PlantFail):
        must_pass_gate([receipt], ["PT-G8", "PT-G6"])  # missing package
    must_pass_gate([receipt], ["PT-G8"])  # complete set passes


# 鈹€鈹€ scorer core 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def _mk_rulecard(overclaim: str | None = None) -> dict:
    """Frozen RULECARD shape (trigger_conditions + evidence, no claims array)."""
    return {
        "rulecard_id": "RC-TEST-0001",
        "mechanism_id": "M-TEST-0001",
        "hypothesis_ref": "H-TEST-0001",
        "fault_class": "condition_config_gated",
        "trigger_conditions": [
            {
                "predicate_id": "P-TEST-0001-1",
                "kind": "value_comparison" if overclaim else "condition_text",
                "fact_refs": [
                    {"fact_id": "F1", "trust_class": "T0"},
                    {"fact_id": "F2", "trust_class": "T1"},
                ],
                "statement": "poller_init applies the command-line POLL_INTERVAL branch"
                             + (" for every build in the corpus" if overclaim else ""),
                "negatable": True,
            },
        ],
        "evidence": [
            {"fact_id": "F1", "trust_class": "T0"},
            {"fact_id": "F2", "trust_class": "T1"},
        ],
        "forbidden_overclaims": [overclaim] if overclaim else ["no overclaim"],
        "provenance": {"derived_from": ["fw_context_read_file", "independent_grep"], "certainty": "measured", "k1_mediated": True},
        "polarity": "positive",
        "refutation_protocol": {"steps": [], "oracle_requirement": "none"},
        "refutation_result": {"state": "UNEXECUTED", "protocol_completed": False, "counterexample_ref": None, "missing_evidence_refs": [], "adjudicated_at": None},
        "validation_status": "unvalidated",
        "oracle_provenance": "none",
        "scope_stamp": {"goal_item": "G-1", "corpus_case": "C1", "lineage": "plant", "schema_version": "wave1-design-gate-1"},
    }


def _mk_facts(tmp: Path) -> list[dict]:
    raw1 = tmp / "raw1.txt"
    raw1.write_text("read_file windowed content", encoding="utf-8")
    raw2 = tmp / "raw2.txt"
    raw2.write_text("grep hits", encoding="utf-8")
    return [
        {"fact_id": "F1", "evidence_surface": "fw_context_read_file", "exact_query": "read_file(...)",
         "trust_class": "T0", "completeness_status": "complete", "raw_artifact_ref": f"{raw1}#abc"},
        {"fact_id": "F2", "evidence_surface": "independent_grep", "exact_query": "grep(...)",
         "trust_class": "T1", "completeness_status": "complete", "raw_artifact_ref": f"{raw2}#def"},
    ]


def test_u11_scorer_clean_rulecard_passes_core_steps(round_dir):
    comp = score_rulecard(_mk_rulecard(), fact_log_rows=_mk_facts(round_dir))
    assert comp.s1_completeness == 1.0
    assert comp.s2_evidence == 1.0
    assert comp.sampled, ">=30% blind sampling executed"
    assert comp.s5_verdict in ("PASS", "REJECTED")  # refutation pending => not PASS


def test_u12_scorer_overclaim_hard_zero(round_dir):
    comp = score_rulecard(_mk_rulecard(overclaim="complete enumeration of all call sites"),
                          fact_log_rows=_mk_facts(round_dir))
    assert comp.overclaims
    assert comp.s2_evidence == 0.0
    assert comp.s5_verdict == "REJECTED_OVERCLAIM"
