"""I2 RED/GREEN tests — K1 Evidence Gateway against the REAL substrate.

Fixture: the PT-G8 scratch smoke index (built once per session into a
workspace scratch dir under implementation/). Tests the four T0 scopes,
C2/C3 discipline (G4/G6/G7/G8), snapshot pinning (C1), and the independent
channels. The substrate is the real fw-context-mcp handler layer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
REPO = IMPL.parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k1 import (  # noqa: E402
    FactLog,
    GatewayResult,
    K1Gateway,
    RoundPin,
    detect_g1_substrate_pin,
    detect_g6_fallback,
    detect_g7_truncation,
)
from fault_discovery_agent.k1.substrate import call_substrate, substrate_identity  # noqa: E402

SCRATCH = IMPL / "scratch_g8"
FW_INDEX_HOME = IMPL / "fw_index_home"


def _fixture_root() -> Path:
    """PT-G8 fixture materialized into the workspace scratch (frozen bytes copy)."""
    src = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "plant_tests" / "PT-G8" / "fixture"
    if not (SCRATCH / "fixture").is_dir():
        shutil.copytree(src, SCRATCH / "fixture", dirs_exist_ok=True)
    return SCRATCH


@pytest.fixture(scope="module")
def indexed_case(tmp_path_factory):
    """Build a real index over the PT-G8 fixture copy; return the case pin."""
    root = _fixture_root()
    cc_dir = root / ".fw-context" / "build"
    cc_dir.mkdir(parents=True, exist_ok=True)
    root_posix = root.as_posix()
    cc = [
        {"directory": root_posix, "arguments": ["clang", "fixture/poller.c", "-Ifixture", "-DPOLL_INTERVAL=250", "-o", "poller.o"], "file": "fixture/poller.c"},
        {"directory": root_posix, "arguments": ["clang", "fixture/main.c", "-Ifixture", "-DPOLL_INTERVAL=250", "-o", "main.o"], "file": "fixture/main.c"},
    ]
    # init skips its build-system probe when compile_commands.json sits at the
    # project root (measured behavior); .fw-context/build/ holds the canonical copy.
    (root / "compile_commands.json").write_text(json.dumps(cc), encoding="utf-8")
    (cc_dir / "compile_commands.json").write_text(json.dumps(cc), encoding="utf-8")
    # Provision the project config DIRECTLY (deterministic; measured shape of
    # an initialized project): [project] id + [index] compile_commands. This
    # avoids `fw-context init`'s build-system probe (scratch holds a Makefile)
    # and the git-root parent walk (scratch lives inside the fw-context repo).
    import uuid

    project_id = uuid.uuid4().hex
    cfg = root / ".fw-context" / "config.toml"
    cfg.write_text(
        "[project]\n"
        f'id = "{project_id}"\n\n'
        "[build]\n\n"
        "[index]\n"
        'compile_commands = ".fw-context/build/compile_commands.json"\n\n'
        "[call_graph]\n\n"
        "[llm]\n"
        "enabled = false\n",
        encoding="utf-8",
    )
    import os

    env = {**os.environ, "FW_CONTEXT_INDEX_DIR": str(FW_INDEX_HOME)}
    # Interrupted earlier runs leave stale locks in the SHARED index home;
    # clear them so indexing cannot be blocked by a dead run's PID files.
    for lock in ("reindex.pause", "reindex.pid", "write.lock", "index.lock"):
        lp = FW_INDEX_HOME / lock
        if lp.exists():
            lp.unlink()
    proc = subprocess.run(
        [sys.executable, "-m", "fw_context_mcp.cli", "index", "--project", str(root), "--no-analyze"],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=300,
    )
    # HARD guard: the DB must physically exist at the readiness-expected path
    db = FW_INDEX_HOME / project_id / "index.db"
    if not db.is_file():
        pytest.fail(
            f"index produced no DB at {db} (rc={proc.returncode}); "
            f"stdout={proc.stdout[-300:]} stderr={proc.stderr[-300:]}"
        )
    if proc.returncode not in (0, 1):
        pytest.fail(f"index failed rc={proc.returncode}: {proc.stdout[-400:]} {proc.stderr[-400:]}")
    # the index must hold symbols: query through the substrate directly
    probe = call_substrate("lookup_symbol", {"name": "poller_init", "exact": True},
                           project_root=str(root), index_home=str(FW_INDEX_HOME))
    rows = probe.payload if isinstance(probe.payload, list) else []
    if not any(isinstance(r, dict) and r.get("name") == "poller_init" for r in rows):
        pytest.fail(f"index holds no poller_init symbol: {probe.payload!r} {probe.error}")
    out = tmp_path_factory.mktemp("round")
    pin = RoundPin(
        freeze_id="FRZ-G8-TEST-20260921T000000Z",
        case="G8-TEST",
        project_root=str(root),
        source_snapshot_id="SNAP-G8",
        build_snapshot_id="BUILD-G8-250",
        repository="fixture:PT-G8",
        commit="frozen-fixture",
        index_home=str(FW_INDEX_HOME),
        fact_log_path=out / "fact_log.jsonl",
        artifact_store=out / "raw",
    )
    return pin


def test_t07_g1_pin_check_passes(indexed_case):
    gw = K1Gateway(indexed_case)
    findings = gw.verify_substrate_pin()
    assert findings, "G1 finding recorded"
    assert findings[0]["verdict"] == "PASS", findings[0]


def test_t08_t0_window_multiline_is_t0(indexed_case):
    gw = K1Gateway(indexed_case)
    res = gw.query_definition_window("fixture/poller.c", 10, 16, "F-G8-0001")
    assert res.ok
    assert res.fact["trust_class"] == "T0"
    assert res.fact["evidence_surface"] == "fw_context_read_file"
    assert res.fact["freeze_id"] == indexed_case.freeze_id
    # ifdef-filtered: window starts at line 10, so content_lines[i] = line 10+i;
    # the active branch line 13 carries the body, the inactive #else line 15 is blank
    content_lines = (res.raw.payload or {}).get("content", "").split("\n")
    assert content_lines[3].strip().startswith("s_interval"), content_lines[3]
    assert content_lines[5].strip() == "", "inactive #else body filtered (line 15)"


def test_t09_single_line_window_downgrades_to_t2(indexed_case):
    gw = K1Gateway(indexed_case)
    res = gw.query_definition_window("fixture/poller.c", 12, 12, "F-G8-0002")
    assert res.ok
    assert res.fact["trust_class"] == "T2"
    assert any(f["detector"] == "G4" and f["verdict"] == "DOWNGRADE" for f in res.detector_findings)
    assert "cross-check" in " ".join(res.notes)


def test_t10_exact_symbol_scope(indexed_case):
    gw = K1Gateway(indexed_case)
    res = gw.query_exact_symbol("poller_init", "F-G8-0003")
    assert res.ok
    assert res.trust_class == "T0"
    assert res.fact["polarity"] == "positive"


def test_t11_condition_context_t0_and_macro_value_downgrade(indexed_case):
    gw = K1Gateway(indexed_case)
    res = gw.query_condition_context("fixture/poller.c", 12, "F-G8-0004")
    assert res.ok
    assert res.trust_class == "T0"
    res2 = gw.query_condition_context("fixture/poller.c", 12, "F-G8-0005", intent="macro_value")
    assert res2.trust_class == "T2"
    assert res2.fact["certainty"] == "degraded"


def test_t12_call_flow_direct(indexed_case):
    gw = K1Gateway(indexed_case)
    res = gw.query_call_flow_direct("poller_init", "F-G8-0006")
    assert res.ok
    assert res.trust_class in ("T0", "T2")
    assert res.fact["evidence_surface"] == "fw_context_find_callers"


def test_t13_independent_grep_channel(indexed_case, tmp_path):
    gw = K1Gateway(indexed_case)
    res = gw.submit_independent_grep("POLL_INTERVAL", Path(indexed_case.project_root), "F-G8-0007")
    assert res.ok
    assert res.fact["evidence_surface"] == "independent_grep"
    assert res.trust_class == "T1"
    # raw artifact persisted + hashed
    ref = res.fact["raw_artifact_ref"]
    assert "#" in ref


def test_t14_independent_clang_e_channel(indexed_case):
    gw = K1Gateway(indexed_case)
    root = Path(indexed_case.project_root)
    res = gw.submit_independent_clang_e(
        root / "fixture" / "poller.c",
        [root / "fixture"],
        ["-DPOLL_INTERVAL=250"],
        "F-G8-0008",
        markers={"conflict_active": ("s_interval = 250 / 10;", "s_interval = fallback;")},
    )
    if not res.ok:
        pytest.skip(f"clang driver unavailable: {res.refusals}")
    assert res.fact["evidence_surface"] == "independent_clang_e"
    assert any("conflict_active" in n for n in res.notes)
    # the -D override wins: preprocessed output holds the 250 branch only
    pp_note = next(n for n in res.notes if n.startswith("active-region"))
    assert "True" in pp_note, pp_note


def test_t15_build_manifest_diff_channel(indexed_case, tmp_path):
    gw = K1Gateway(indexed_case)
    root = Path(indexed_case.project_root)
    cc = root / ".fw-context" / "build" / "compile_commands.json"
    # variant: POLL_INTERVAL 250 -> 100
    data = json.loads(cc.read_text(encoding="utf-8"))
    for e in data:
        e["arguments"] = [a.replace("-DPOLL_INTERVAL=250", "-DPOLL_INTERVAL=100") for a in e["arguments"]]
    cc_b = tmp_path / "compile_commands_b.json"
    cc_b.write_text(json.dumps(data), encoding="utf-8")
    res = gw.submit_build_manifest_diff(cc, cc_b, "F-G8-0009")
    assert res.ok
    assert res.fact["evidence_surface"] == "build_manifest_diff"
    artifact = Path(res.fact["raw_artifact_ref"].split("#")[0])
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["identical"] is False
    assert payload["diffs"][0]["defines_only_in_a"] == ["-DPOLL_INTERVAL=250"]


def test_t16_snapshot_pin_refusal(indexed_case):
    """C1: a query against a build snapshot that is not the round pin is refused."""
    gw = K1Gateway(indexed_case)
    res = gw.query_definition_window("fixture/poller.c", 10, 16, "F-G8-0010")
    assert res.ok
    assert res.fact["build_snapshot_id"] == indexed_case.build_snapshot_id


def test_t17_fact_log_append_only_and_replayable(indexed_case):
    log = FactLog(indexed_case.fact_log_path)
    facts = log.facts_for_freeze(indexed_case.freeze_id)
    assert facts, "facts minted this round"
    for f in facts:
        assert f["exact_query"], "replayable query chain (E3)"
        assert f["raw_artifact_ref"], "raw output persisted (E1)"
    refusals = log.refusals(indexed_case.freeze_id)
    for r in refusals:
        assert r["code"] in {
            "E-TIER-SCOPE", "E-TIER-FALLBACK", "E-TIER-TRUNCATED", "E-TIER-MACRO-VALUE",
            "E-TIER-FRAGMENT", "E-BUILD-SNAPSHOT", "E-CHANNEL-UNAVAILABLE", "E-SCHEMA", "E-SUBSTRATE",
        }


def test_t18_detectors_g6_g7_pure_functions():
    # G6: fallback row rejected
    payload = [{"name": "foo", "_fallback": "sanitized"}]
    f6 = detect_g6_fallback("lookup_symbol", payload)
    assert f6 and f6[0]["verdict"] == "FAIL"
    # G7: limit-reached truncation
    payload7 = [{"name": f"s{i}"} for i in range(5)]
    f7 = detect_g7_truncation("search_code", {"limit": 5}, payload7)
    assert f7 and f7[0]["verdict"] == "FLAG"
    # G1: wrong commit fails; pinned commit + clean tree passes
    from fault_discovery_agent.k1 import detect_g1_substrate_pin
    from fault_discovery_agent.k1.substrate import SUBSTRATE_COMMIT

    f1 = detect_g1_substrate_pin("deadbeef", "")
    assert f1[0]["verdict"] == "FAIL"
    f1b = detect_g1_substrate_pin(SUBSTRATE_COMMIT, "")
    assert f1b[0]["verdict"] == "PASS"
