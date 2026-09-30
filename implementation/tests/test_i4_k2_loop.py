"""I4 tests — K2 hypothesis-and-rule loop against the real K1 gateway.

Uses the PT-G8 scratch index (built once here with the measured provisioning
recipe). Covers: promotion to RuleCard, abstention (first-class), gate
refusals (E1/E2/E6), family-library-as-data loading, bounded loop.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
REPO = IMPL.parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k1 import FactLog, K1Gateway, RoundPin  # noqa: E402
from fault_discovery_agent.k2 import K2Loop, load_family_library, run_evidence_gate  # noqa: E402
from fault_discovery_agent.schemas.loader import validate  # noqa: E402

PLANT_DIR = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "plant_tests"
SCRATCH = IMPL / "scratch_g8"
FW_INDEX_HOME = IMPL / "fw_index_home"


def _fixture_root() -> Path:
    src = PLANT_DIR / "PT-G8" / "fixture"
    if not (SCRATCH / "fixture").is_dir():
        shutil.copytree(src, SCRATCH / "fixture", dirs_exist_ok=True)
    return SCRATCH


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    root = _fixture_root()
    cc_dir = root / ".fw-context" / "build"
    cc_dir.mkdir(parents=True, exist_ok=True)
    root_posix = root.as_posix()
    cc = [
        {"directory": root_posix, "arguments": ["clang", "fixture/poller.c", "-Ifixture", "-DPOLL_INTERVAL=250", "-o", "poller.o"], "file": "fixture/poller.c"},
        {"directory": root_posix, "arguments": ["clang", "fixture/main.c", "-Ifixture", "-DPOLL_INTERVAL=250", "-o", "main.o"], "file": "fixture/main.c"},
    ]
    cc_text = json.dumps(cc, indent=1)
    (root / "compile_commands.json").write_text(cc_text, encoding="utf-8")
    (cc_dir / "compile_commands.json").write_text(cc_text, encoding="utf-8")
    cfg = root / ".fw-context" / "config.toml"
    if "[project]" not in cfg.read_text(encoding="utf-8"):
        cfg.write_text(
            "[project]\n" + f'id = "{uuid.uuid4().hex}"\n\n' + "[build]\n\n" + "[index]\n"
            + 'compile_commands = ".fw-context/build/compile_commands.json"\n\n'
            + "[call_graph]\n\n" + "[llm]\nenabled = false\n",
            encoding="utf-8",
        )
    env = {**os.environ, "FW_CONTEXT_INDEX_DIR": str(FW_INDEX_HOME)}
    for lock in ("reindex.pause", "reindex.pid", "write.lock", "index.lock"):
        lp = FW_INDEX_HOME / lock
        if lp.exists():
            lp.unlink()
    subprocess.run(
        [sys.executable, "-m", "fw_context_mcp.cli", "index", "--project", str(root), "--no-analyze"],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    out = tmp_path_factory.mktemp("k2round")
    pin = RoundPin(
        freeze_id="FRZ-K2-TEST",
        case="PT-G8",
        project_root=str(root),
        source_snapshot_id="SNAP-G8",
        build_snapshot_id="BUILD-G8-250",
        repository="fixture:PT-G8",
        commit="frozen-fixture",
        index_home=str(FW_INDEX_HOME),
        fact_log_path=out / "fact_log.jsonl",
        artifact_store=out / "raw",
    )
    return K1Gateway(pin)


def test_v01_family_library_is_data_and_frozen_set(gw):
    lib = load_family_library()
    assert lib["library_version"] == "wave1-families-1"
    assert set(lib["families"].keys()) == {
        "condition_config_gated", "init_submit_pairing", "existence_absence", "direct_flow_presence"
    }


def test_v02_condition_config_gated_promotes_to_rulecard(gw):
    loop = K2Loop(gw)
    bundle = {"bundle_id": "B1", "family": "condition_config_gated", "corpus_case": "C1", "lineage": "plant", "goal_item_ref": "G-1", "template_ref": "ccg-1", "freeze_id": gw.pin.freeze_id}
    target = {
        "tag": "poller",
        "file": "fixture/poller.c", "line": 12, "start_line": 12, "end_line": 16,
        "condition_text_head": "POLL_INTERVAL > 200",
        "root": gw.pin.project_root,
        "predicate_statement": "poller_init applies the command-line POLL_INTERVAL branch (condition POLL_INTERVAL > 200 active under -DPOLL_INTERVAL=250)",
        "title": "Poll-interval branch is config/macro gated",
        "claim_text": "Under the pinned build (-DPOLL_INTERVAL=250), the condition POLL_INTERVAL > 200 selects the branch at fixture/poller.c:13",
    }
    outcome = loop.run(bundle, target)
    assert outcome.kind == "rulecard", json.dumps(outcome.missing_evidence, default=str) + json.dumps(outcome.gate_decisions, default=str)
    rc = outcome.rulecard
    # wave1-1-semantic-gate-1: loop cards carry the `validation` object and
    # validate against the extended package-local schema.
    validate(rc, "rulecard_v1_1")
    assert rc["validation"]["faultness_status"] == "PENDING"
    assert rc["hypothesis_ref"] == outcome.hypothesis["hypothesis_id"]
    kinds = {s["step_kind"] for s in outcome.hypothesis["mechanism_steps"]}
    assert {"condition", "definition"} <= kinds


def test_v03_existence_absence_abstains_on_missing_symbol(gw):
    loop = K2Loop(gw)
    bundle = {"bundle_id": "B2", "family": "existence_absence", "corpus_case": "C1", "lineage": "plant", "goal_item_ref": "G-4", "template_ref": "ea-1", "freeze_id": gw.pin.freeze_id}
    target = {
        "tag": "missing",
        "symbol": "no_such_handler_anywhere_xyz",
        "symbol_definition_pattern": "no_such_handler_anywhere_xyz",
        "root": gw.pin.project_root,
    }
    outcome = loop.run(bundle, target)
    # the absence IS satisfiable (explicit no-result + grep) -> may promote an
    # absence RuleCard OR abstain; never a false PRESENT
    assert outcome.kind in ("rulecard", "insufficient_evidence")
    if outcome.kind == "rulecard":
        assert outcome.rulecard["trigger_conditions"][0]["kind"] == "definition_absent"


def test_v04_direct_flow_presence_control(gw):
    loop = K2Loop(gw)
    bundle = {"bundle_id": "B3", "family": "direct_flow_presence", "corpus_case": "C1", "lineage": "plant", "goal_item_ref": "G-1", "template_ref": "dfp-1", "freeze_id": gw.pin.freeze_id}
    target = {
        "tag": "flow",
        "symbol": "poller_init",
        "file": "fixture/poller.c", "line": 10, "start_line": 10, "end_line": 17,
        "root": gw.pin.project_root,
    }
    outcome = loop.run(bundle, target)
    assert outcome.kind in ("rulecard", "insufficient_evidence")
    for step in outcome.hypothesis["mechanism_steps"]:
        for fr in step["required_fact_ids"]:
            row = next(r for r in loop._fact_rows if r["fact_id"] == fr)
            assert row["trust_class"] != "T2" or step["step_kind"] == "config", "E2: T2 never load-bearing"


def test_v05_gate_e2_refuses_t2_load_bearing():
    rich_steps = [{"step_id": "S1", "description": "d", "required_fact_ids": ["F1"], "step_kind": "call_flow"}]
    rows = [{"fact_id": "F1", "trust_class": "T2", "provenance": {"instrument": "i", "channel": "c", "k1_mediated": True},
             "raw_artifact_ref": "x#y", "evidence_surface": "fw_context_find_callers", "completeness_status": "complete",
             "build_snapshot_id": "B", "context_identity": "ctx"}]
    report = run_evidence_gate(rich_steps, rows, pinned_build_snapshot_id="B")
    assert report.refused
    assert any(d.rule == "E2" for d in report.refusals())


def test_v06_gate_e6_refuses_value_decisive_macro_claim():
    rich_steps = [{"step_id": "S1", "description": "the value of POLL_INTERVAL decides the branch", "required_fact_ids": ["F1"], "step_kind": "config"}]
    rows = [{"fact_id": "F1", "trust_class": "T2", "certainty": "degraded",
             "detector_checks": ["G8"], "provenance": {"instrument": "i", "channel": "c", "k1_mediated": True},
             "raw_artifact_ref": "x#y", "evidence_surface": "fw_context_explain_condition",
             "completeness_status": "complete", "build_snapshot_id": "B", "context_identity": "ctx"}]
    report = run_evidence_gate(rich_steps, rows, pinned_build_snapshot_id="B",
                               hypothesis_text="the value of POLL_INTERVAL decides the branch")
    assert report.refused
    assert any(d.rule == "E6" and d.outcome == "REFUSE" for d in report.decisions)


def test_v07_gate_e1_refuses_missing_fact_chain():
    rich_steps = [{"step_id": "S1", "description": "d", "required_fact_ids": ["F-MISSING"], "step_kind": "condition"}]
    report = run_evidence_gate(rich_steps, [], pinned_build_snapshot_id="B")
    assert report.refused
    assert report.refusals()[0].rule == "E1"
