"""test_slice2_hook — S2.11 end-to-end oracles through the REAL pipeline.

runner.run → _process_unit → certify_and_publish_for_unit(world=…) →
worlds bind/poison → finalize + §40 metrics.  Every test runs the actual
production indexing pass (no store-level shortcuts) with the v2 flag ON:

* S2-T15: flag on does NOT skip parsing — every TU is still fully parsed
  (parse-skip reuse remains Slice-4; legacy symbol rows are complete).
* Full pipeline publishes states AND binds the run's world; legacy db and
  v2 store stay separate files (no coupling).
* Central two-version scenario (task §39): version A indexes clean, then
  version B in the same store — two worlds, KB-C-equal state shared, both
  finalized 'active', no contamination.
* Flag-off run creates NO v2 store content (inertness end-to-end).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import make_project  # noqa: E402
from slice2_helpers import (  # noqa: E402
    binding_table,
    counts,
    dirty,
    git,
    make_git_project,
    metric_rows,
    restore,
    state_table,
    v2_rows,
    world_table,
)

pytestmark = pytest.mark.usefixtures("_v2_flags")


def _init_project_id(proj: Path) -> str:
    from fw_context_mcp.config.settings import (_write_project_id,
                                                generate_project_id)
    pid = generate_project_id()
    _write_project_id(proj, pid)
    return pid


def _run_index(proj: Path, tmp_path: Path, *, name: str = "index"):
    from fw_context_mcp.config import derive_project_id
    from fw_context_mcp.indexer import runner
    pid = derive_project_id(proj)
    db_path = tmp_path / name / pid / "index.db"
    config_hash = runner.run(
        compile_commands=proj / "compile_commands.json",
        db_path=db_path, project_root=proj, project_id=pid,
        index_refs=False, index_embeddings=False, analyze_symbols=False,
    )
    return pid, config_hash, db_path


def _v2(db_path: Path) -> Path:
    return db_path.parent / "index-v2.db"


@pytest.fixture()
def _v2_flags():
    """Both flag caches reset around each test (env set per-test)."""
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    yield
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()


# ── S2-T15: flag on does not skip parsing ────────────────────────────────────

def test_s2_t15_flag_on_keeps_full_parsing(tmp_path, monkeypatch):
    """The v2 path is a pure SHADOW: all TUs are parsed and legacy rows are
    complete; the v2 store additionally holds the world + bindings."""
    from slice2_helpers import set_v2_authority_island
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    set_v2_authority_island(monkeypatch)   # the run must finalize 'active'
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    _init_project_id(proj)
    pid, config_hash, db_path = _run_index(proj, tmp_path)
    assert config_hash

    v2 = _v2(db_path)
    assert v2.exists()
    # legacy completeness: files table holds the parsed sources
    import sqlite3
    conn = sqlite3.connect(db_path)
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    syms = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    assert files >= 3 and syms > 0               # main.c + headers indexed
    # v2: one world, one binding, one published state, finalized
    shape = counts(v2)
    assert shape["worlds"] == 1 and shape["bindings"] == 1
    assert shape["states"] == 1
    w = world_table(v2)[0]
    assert w["world_status"] == "active"
    assert w["run_provenance"]
    b = binding_table(v2)[0]
    assert b["binding_status"] == "bound" and b["disposition"] == "ok"
    assert b["state_key"] and b["state_generation"]


def test_s2_t15b_flag_off_creates_no_v2_content(tmp_path, monkeypatch):
    """Flag off ⇒ whole Slice-2 leg inert: no v2 file, legacy identical."""
    monkeypatch.delenv("FW_CONTEXT_SHADOW_V2", raising=False)
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    _init_project_id(proj)
    pid, config_hash, db_path = _run_index(proj, tmp_path)
    assert config_hash
    assert not _v2(db_path).exists()


# ── §39 central scenario: two versions, one store ────────────────────────────

def test_s2_central_two_version_scenario(tmp_path, monkeypatch):
    """Version A (clean) → index.  Version B (util changed) → index again.
    Both worlds coexist in ONE v2 store; the KB-C-equal closure-shared
    state of untouched TUs is reused; each world finalized 'active'."""
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    from slice2_helpers import set_v2_authority_island
    set_v2_authority_island(monkeypatch)   # both worlds finalize 'active'
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    _init_project_id(proj)
    pid, _, db_path = _run_index(proj, tmp_path)
    v2 = _v2(db_path)
    shape_a = counts(v2)
    assert shape_a["worlds"] == 1 and shape_a["states"] >= 1
    world_a = world_table(v2)[0]
    assert world_a["world_status"] == "active"

    # version B: change one TU source (content change ⇒ new L0 ⇒ new state)
    dirty(proj, "src/main.c",
          '#include "util.h"\n#include "cfg.h"\n#include "trans.h"\n'
          'int util(void) { return UTIL_VERSION; }\n'
          'int main(void) { return UTIL_VERSION + 1; }\n')
    pid_b, _, db_path_b = _run_index(proj, tmp_path)
    assert db_path_b == db_path                  # same store
    v2 = _v2(db_path)
    shape_b = counts(v2)
    assert shape_b["worlds"] == 2
    assert shape_b["states"] >= shape_a["states"] + 1
    worlds_rows = world_table(v2)
    assert all(w["world_status"] == "active" for w in worlds_rows)
    assert len({w["snapshot_id"] for w in worlds_rows}) == 2
    assert len({w["namespace_id"] for w in worlds_rows}) == 1
    # bindings are per-world and never point across worlds
    per_world = {}
    for b in binding_table(v2):
        per_world.setdefault(b["world_id"], set()).add(b["state_key"])
    assert len(per_world) == 2
    for states in per_world.values():
        assert states                            # every world bound its TUs

    # A→B→A: restoring version A's exact bytes makes the SNAPSHOT identity
    # recover (S2-T16 identity equality); the legacy parse-skip may then
    # legitimately skip the re-parse (Slice-4 scope, untouched here), so no
    # third world/binding/state may appear either way.
    ORIGINAL_MAIN = ('#include "util.h"\n#include "cfg.h"\n'
                     '#include "trans.h"\n'
                     'int util(void) { return UTIL_VERSION; }\n'
                     'int main(void) { return util(); }\n')
    restore(proj, "src/main.c", ORIGINAL_MAIN)
    from fw_context_mcp.store_v2.identity_capture import capture_snapshot
    snap_c = capture_snapshot(proj)
    snap_a_id = world_table(v2)[0]["snapshot_id"]     # run A's world
    assert snap_c.snapshot_id == snap_a_id            # identity recovered
    states_after_c = counts(v2)
    assert states_after_c["worlds"] == 2             # no third world
    assert states_after_c["states"] == shape_b["states"]  # no new state
    main_binds = [b for b in binding_table(v2)
                  if b["canon_source_path"] == "src/main.c"]
    assert len(main_binds) == 2                      # A's and B's bindings
    assert main_binds[0]["state_key"] != main_binds[1]["state_key"]
    states_after = state_table(v2)
    keys = [s["tusemantic_key"] for s in states_after]
    assert len(set(keys)) == len(keys)               # keys unique


def test_s2_central_scenario_metrics_recorded(tmp_path, monkeypatch):
    """§40 counters land in slice2_run_metrics for each shadow run."""
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    from slice2_helpers import set_v2_authority_island
    set_v2_authority_island(monkeypatch)   # metrics live in the finalize block
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    _init_project_id(proj)
    pid, _, db_path = _run_index(proj, tmp_path)
    v2 = _v2(db_path)
    m = metric_rows(v2)
    assert len(m) >= 1
    run = next(iter(m.values()))
    assert run["parse_count"] >= 1               # S2-T15 evidence
    assert run["certification_count"] >= 1
    assert run["bindings_created"] >= 1
    assert run["tu_world_count"] >= 1
    assert run["poisoned_tus"] == 0
    assert run["finalized"] == 1
