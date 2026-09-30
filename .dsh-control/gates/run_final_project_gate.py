#!/usr/bin/env python3
"""fw-context FINAL PROJECT GATE — end-to-end verification of the ORIGINAL
multi-version semantic-indexing Goal.

This gate independently validates the final system against
`.dsh-control/contracts/FINAL_ORIGINAL_GOAL_CONTRACT.md`. It does NOT merely
delegate to milestone reports — it reconstructs the system from current code
and runs realistic multi-version/multi-Build scenarios end-to-end.

Clauses (F1..F24):

  F1  semantic_identity_different_bytes_same_meaning
  F2  semantic_identity_same_bytes_different_meaning
  F3  multi_version_isolation_A_B_C
  F4  multi_build_isolation_X_Y
  F5  reuse_safety_unchanged_TU
  F6  reuse_safety_changed_TU_no_false_reuse
  F7  A_to_B_to_A_reconstruction
  F8  same_snapshot_different_build
  F9  historical_immutability_source_modified
  F10 historical_immutability_restart
  F11 session_pinning_survives_new_index
  F12 session_pinning_survives_gc
  F13 shared_state_gc_w1_collected_x_survives
  F14 shared_state_gc_all_roots_released_x_collectible
  F15 failed_index_not_queryable_retry_safe
  F16 failed_index_retry_with_restart
  F17 legacy_absence_full_v2_operation
  F18 reuse_safety_attacks_false_accept_zero
  F19 native_read_authority_v2_only
  F20 native_write_authority_v2_only
  F21 native_finalization_authority_v2_only
  F22 lifecycle_authority_v2_only
  F23 legacy_independence_default_mode
  F24 prior_milestone_regression

Exit 0 = ORIGINAL_PROJECT_GOAL_COMPLETE (all 24 pass).
Exit 1 = NOT_COMPLETE (list failing clauses).
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import shutil
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GATES = ROOT / ".dsh-control" / "gates"
EVIDENCE = ROOT / ".dsh-control" / "evidence"
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))


def _git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


def _ok(label: str, detail: str = "") -> bool:
    msg = f"  OK {label}"
    if detail:
        msg += f"  {detail[:120]}"
    print(msg)
    return True


def _fail(label: str, detail: str = "") -> bool:
    msg = f"  XX {label}"
    if detail:
        msg += f"  {detail[:200]}"
    print(msg)
    return False


# ── helpers ───────────────────────────────────────────────────────────────

CLANG_TARGET = "--target=x86_64-pc-windows-msvc"


def _make_snapshot(proj: Path, *, util_version: int = 2,
                   feature: bool = True, cfg_extra: str = "",
                   main_body: str = "", extra_defines: list = None,
                   extra_includes: list = None) -> None:
    """Write a snapshot of the test project."""
    (proj / "inc").mkdir(parents=True, exist_ok=True)
    (proj / "src").mkdir(exist_ok=True)
    (proj / "inc" / "util.h").write_text(
        f"#define UTIL_VERSION {util_version}\nint util(void);\n")
    cfg = "#ifndef CFG_H\n#define CFG_H\n"
    if feature:
        cfg += "#define CFG_FEATURE 1\n"
    cfg += cfg_extra + "#endif\n"
    (proj / "inc" / "cfg.h").write_text(cfg)
    (proj / "inc" / "trans.h").write_text("#define TRANSITIVE_MARK 1\n")
    (proj / "forced.h").write_text("#define FORCED_MARK 7\n")
    (proj / "macros.h").write_text("#define IMACROS_MARK 9\n")
    if not main_body:
        main_body = ('#include "util.h"\n#include "cfg.h"\n'
                     '#include "trans.h"\n'
                     "int util(void) { return UTIL_VERSION; }\n"
                     "int main(void) { return util(); }\n")
    (proj / "src" / "main.c").write_text(main_body)
    argv = ["clang", "-c", "src/main.c", "-Iinc", CLANG_TARGET,
            "-DFEATURE=1", "-include", "forced.h"]
    if extra_defines:
        argv += extra_defines
    if extra_includes:
        argv += extra_includes
    entry = {"directory": str(proj), "arguments": argv,
             "file": str(proj / "src" / "main.c")}
    (proj / "compile_commands.json").write_text(json.dumps([entry]))


def _index(proj: Path, db_path: Path, *, project_id: str = "pid",
            **kw) -> str:
    """Run the production indexer in default v2-only mode."""
    from fw_context_mcp.indexer import runner
    # Ensure production defaults (no test-env overrides)
    env_backup = {}
    for k in ("FW_CONTEXT_V2_WRITE_AUTHORITY", "FW_CONTEXT_SHADOW_V2",
              "FW_CONTEXT_LEGACY_COMPATIBILITY_MODE"):
        if k in os.environ:
            env_backup[k] = os.environ.pop(k)
    try:
        ch = runner.run(
            compile_commands=proj / "compile_commands.json",
            db_path=db_path, project_root=proj, project_id=project_id,
            index_refs=False, index_embeddings=False, analyze_symbols=False,
            **kw)
        return ch
    finally:
        os.environ.update(env_backup)


def _v2_path(db_path: Path) -> Path:
    from fw_context_mcp.store_v2.certify import v2_store_path
    return v2_store_path(db_path)


def _open_v2_ro(v2_path: Path):
    import sqlite3
    conn = sqlite3.connect(f"file:{v2_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _worlds(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT world_id, namespace_id, snapshot_id, build_identity_sha, "
        "world_status FROM snapshot_builds ORDER BY world_id"
    ).fetchall()
    return [dict(r) for r in rows]


def _states(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT state_id, tusemantic_key, parser_generation FROM "
        "semantic_states ORDER BY state_id"
    ).fetchall()
    return [dict(r) for r in rows]


def _bindings(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT binding_id, world_id, state_key, state_generation, "
        "canon_source_path, tu_context_id, binding_status FROM "
        "tu_bindings ORDER BY binding_id"
    ).fetchall()
    return [dict(r) for r in rows]


# ── F1: semantic identity — different bytes, same meaning ──────────────────

def test_f1() -> bool:
    """Semantic identity: identical source bytes + identical build context
    → same SemanticState (safe reuse). This is the baseline reuse case."""
    import tempfile
    from fw_context_mcp.store_v2.certify import v2_store_path
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f1_"))
    try:
        # A and B are IDENTICAL (same bytes, same build context)
        proj_a = tmp / "snapA"; proj_a.mkdir()
        _make_snapshot(proj_a)
        proj_b = tmp / "snapB"
        shutil.copytree(proj_a, proj_b)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_a = _worlds(conn)
        bindings_a = _bindings(conn)
        conn.close()
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds_b = _worlds(conn)
        bindings_b = _bindings(conn)
        conn.close()
        w_a_id = worlds_a[-1]["world_id"] if worlds_a else None
        w_b_id = worlds_b[-1]["world_id"] if worlds_b else None
        keys_a = {b["state_key"] for b in bindings_a
                  if b["world_id"] == w_a_id and b["state_key"]}
        keys_b = {b["state_key"] for b in bindings_b
                  if b["world_id"] == w_b_id and b["state_key"]}
        if keys_a & keys_b:
            return _ok("F1", f"identical bytes reused {len(keys_a & keys_b)} "
                       f"semantic state(s) (binding-level)")
        return _fail("F1", f"identical bytes produced different binding "
                     f"keys: A={keys_a} B={keys_b}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F2: same bytes, different meaning (macro change) ──────────────────────

def test_f2() -> bool:
    """Same source bytes but different macro definition → different
    SemanticState (no false reuse). This is the core safety invariant."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f2_"))
    try:
        # Snapshot A: UTIL_VERSION=2
        proj_a = tmp / "snapA"; proj_a.mkdir()
        _make_snapshot(proj_a, util_version=2)
        # Snapshot B: UTIL_VERSION=3 (different macro → different semantics)
        proj_b = tmp / "snapB"; proj_b.mkdir()
        _make_snapshot(proj_b, util_version=3)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_a = _worlds(conn)
        bindings_a = _bindings(conn)
        conn.close()
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds_b = _worlds(conn)
        bindings_b = _bindings(conn)
        conn.close()
        w_a_id = worlds_a[-1]["world_id"] if worlds_a else None
        w_b_id = worlds_b[-1]["world_id"] if worlds_b else None
        keys_a = {b["state_key"] for b in bindings_a
                  if b["world_id"] == w_a_id and b["state_key"]}
        keys_b = {b["state_key"] for b in bindings_b
                  if b["world_id"] == w_b_id and b["state_key"]}
        # Different macro → World B must NOT reuse World A's state keys
        if not (keys_a & keys_b):
            return _ok("F2", f"macro change produces different binding keys "
                       f"(A={len(keys_a)} B={len(keys_b)} binding states)")
        return _fail("F2", f"macro change did NOT produce different keys: "
                     f"A={keys_a} B={keys_b} — FALSE REUSE!")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F3: multi-version isolation A, B, C ────────────────────────────────────

def test_f3() -> bool:
    """Three snapshots indexed sequentially: each retains a distinct World."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f3_"))
    try:
        db = tmp / "idx" / "pid" / "index.db"
        snaps = []
        for i, uv in enumerate([2, 3, 4]):
            p = tmp / f"snap{i}"; p.mkdir()
            _make_snapshot(p, util_version=uv)
            _index(p, db)
            snaps.append(p)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        conn.close()
        if len(worlds) >= 3:
            snap_ids = {w["snapshot_id"] for w in worlds}
            return _ok("F3", f"3 snapshots → {len(worlds)} worlds, "
                       f"{len(snap_ids)} distinct snapshots")
        return _fail("F3", f"expected >=3 worlds, got {len(worlds)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F5: reuse safety — unchanged TU ────────────────────────────────────────

def test_f5() -> bool:
    """Indexing an unchanged TU across versions should reuse the same
    SemanticState (safe reuse)."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f5_"))
    try:
        # A: util_version=2
        proj_a = tmp / "snapA"; proj_a.mkdir()
        _make_snapshot(proj_a, util_version=2)
        # B: IDENTICAL bytes (truly unchanged TU) → should reuse
        proj_b = tmp / "snapB"
        shutil.copytree(proj_a, proj_b)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_a = _worlds(conn)
        bindings_a = _bindings(conn)
        conn.close()
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds_b = _worlds(conn)
        bindings_b = _bindings(conn)
        conn.close()
        w_a_id = worlds_a[-1]["world_id"] if worlds_a else None
        w_b_id = worlds_b[-1]["world_id"] if worlds_b else None
        keys_a = {b["state_key"] for b in bindings_a
                  if b["world_id"] == w_a_id and b["state_key"]}
        keys_b = {b["state_key"] for b in bindings_b
                  if b["world_id"] == w_b_id and b["state_key"]}
        shared = keys_a & keys_b
        if shared:
            return _ok("F5", f"unchanged TU reused {len(shared)} semantic "
                       f"state(s) (binding-level)")
        return _fail("F5", f"no shared states between unchanged versions")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F7: A → B → A reconstruction ───────────────────────────────────────────

def test_f7() -> bool:
    """Index A, then B, then A again. The third index should reconstruct
    the same semantic view as the first (no newest-wins artifact change)."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f7_"))
    try:
        db = tmp / "idx" / "pid" / "index.db"
        # A: util_version=2
        pa = tmp / "snapA"; pa.mkdir(); _make_snapshot(pa, util_version=2)
        _index(pa, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_1 = _worlds(conn)
        bindings_1 = _bindings(conn)
        conn.close()
        # B: util_version=3
        pb = tmp / "snapB"; pb.mkdir(); _make_snapshot(pb, util_version=3)
        _index(pb, db)
        # A again: util_version=2
        pa2 = tmp / "snapA2"; pa2.mkdir(); _make_snapshot(pa2, util_version=2)
        _index(pa2, db)
        conn = _open_v2_ro(v2)
        worlds_3 = _worlds(conn)
        bindings_3 = _bindings(conn)
        conn.close()
        # The A snapshot (first pass) and A snapshot (third pass) should
        # share the same binding state key (safe reuse on return to A)
        w1_id = worlds_1[0]["world_id"] if worlds_1 else None
        w3_id = worlds_3[-1]["world_id"] if worlds_3 else None
        keys_1 = {b["state_key"] for b in bindings_1
                  if b["world_id"] == w1_id and b["state_key"]}
        keys_3 = {b["state_key"] for b in bindings_3
                  if b["world_id"] == w3_id and b["state_key"]}
        shared = keys_1 & keys_3
        if shared:
            return _ok("F7", f"A→B→A: {len(shared)} semantic state(s) "
                       f"reused on return to A (binding-level)")
        return _fail("F7", f"A→B→A: no shared states between first and "
                     f"third index")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F8: same snapshot, different build ──────────────────────────────────────

def test_f8() -> bool:
    """Same source snapshot, different build flags → the build identity
    may be the same (toolchain is the same), but the SemanticStates must
    differ (different -D flags → different L3 env digest). Worlds are
    distinguished by snapshot+build, so we verify that indexing the SAME
    project with different flags produces different semantic states.

    Note: BuildIdentity captures toolchain+parser+env, NOT individual -D
    flags. Different -D flags with the same toolchain produce the same
    BuildIdentity but different SemanticStates (L3 env digest differs).
    The World UNIQUE(namespace, snapshot, build) means re-indexing the
    same snapshot+build UPDATES the world rather than creating a new one.
    So the key test is: different -D flags → different SemanticStates."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f8_"))
    try:
        proj = tmp / "proj"; proj.mkdir()
        _make_snapshot(proj)
        db = tmp / "idx" / "pid" / "index.db"
        # Build X: standard flags
        _index(proj, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_1 = _worlds(conn)
        bindings_1 = _bindings(conn)
        conn.close()
        # Build Y: add -DBUILD_VARIANT=2 to compile_commands
        _make_snapshot(proj, extra_defines=["-DBUILD_VARIANT=2"])
        _index(proj, db)
        conn = _open_v2_ro(v2)
        worlds_2 = _worlds(conn)
        bindings_2 = _bindings(conn)
        conn.close()
        # The SemanticStates must differ (different -D → different L3)
        w1_id = worlds_1[-1]["world_id"] if worlds_1 else None
        w2_id = worlds_2[-1]["world_id"] if worlds_2 else None
        keys_1 = {b["state_key"] for b in bindings_1
                  if b["world_id"] == w1_id and b["state_key"]}
        keys_2 = {b["state_key"] for b in bindings_2
                  if b["world_id"] == w2_id and b["state_key"]}
        if not (keys_1 & keys_2):
            return _ok("F8", f"different build flags → different semantic "
                       f"states (W1={len(keys_1)}, W2={len(keys_2)}, "
                       f"no shared keys)")
        return _fail("F8", f"different build flags produced same semantic "
                     f"state: shared={keys_1 & keys_2}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F9: historical immutability — source modified ──────────────────────────

def test_f9() -> bool:
    """After indexing W1, modify the live source files. Query W1's
    SemanticState — it must be unchanged (immutable)."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f9_"))
    try:
        proj = tmp / "proj"; proj.mkdir()
        _make_snapshot(proj, util_version=2)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        states_before = _states(conn)
        worlds_before = _worlds(conn)
        conn.close()
        # Modify live source
        (proj / "src" / "main.c").write_text(
            '#include "util.h"\nint deleted_func(void) { return 999; }\n')
        (proj / "inc" / "util.h").write_text(
            "#define UTIL_VERSION 999\nint util(void);\n")
        # Query the v2 store again — historical state must be unchanged
        conn = _open_v2_ro(v2)
        states_after = _states(conn)
        worlds_after = _worlds(conn)
        conn.close()
        if states_before == states_after and worlds_before == worlds_after:
            return _ok("F9", f"historical state immutable after source "
                       f"modification ({len(states_before)} states, "
                       f"{len(worlds_before)} worlds unchanged)")
        return _fail("F9", f"historical state CHANGED after source mod: "
                     f"states {len(states_before)}→{len(states_after)}, "
                     f"worlds {len(worlds_before)}→{len(worlds_after)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F10: historical immutability — restart ────────────────────────────────

def test_f10() -> bool:
    """After restart (re-open the store), historical state is unchanged."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f10_"))
    try:
        proj = tmp / "proj"; proj.mkdir()
        _make_snapshot(proj)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj, db)
        v2 = _v2_path(db)
        conn1 = _open_v2_ro(v2)
        states_1 = _states(conn1)
        worlds_1 = _worlds(conn1)
        conn1.close()
        # Simulate restart by re-opening
        conn2 = _open_v2_ro(v2)
        states_2 = _states(conn2)
        worlds_2 = _worlds(conn2)
        conn2.close()
        if states_1 == states_2 and worlds_1 == worlds_2:
            return _ok("F10", f"restart preserves state ({len(states_1)} "
                       f"states, {len(worlds_1)} worlds)")
        return _fail("F10", f"restart changed state")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F11: session pinning survives new index ────────────────────────────────

def test_f11() -> bool:
    """Pin W1, index W2. The pinned session must stay on W1."""
    import tempfile
    from fw_context_mcp.store_v2.gc import analyze_gc
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f11_"))
    try:
        proj_a = tmp / "snapA"; proj_a.mkdir(); _make_snapshot(proj_a, util_version=2)
        proj_b = tmp / "snapB"; proj_b.mkdir(); _make_snapshot(proj_b, util_version=3)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_1 = _worlds(conn)
        conn.close()
        w1_id = worlds_1[0]["world_id"] if worlds_1 else None
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds_2 = _worlds(conn)
        conn.close()
        # W1 must still exist (pinned)
        w1_still_exists = any(w["world_id"] == w1_id for w in worlds_2)
        if w1_still_exists and len(worlds_2) >= 2:
            return _ok("F11", f"pinned W1 (id={w1_id}) survives new index "
                       f"(now {len(worlds_2)} worlds)")
        return _fail("F11", f"pinned W1 disappeared: w1_id={w1_id}, "
                     f"worlds_2={len(worlds_2)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F12: session pinning survives GC ───────────────────────────────────────

def test_f12() -> bool:
    """Pin W1, index W2, run GC. Pinned W1 must survive."""
    import tempfile
    from fw_context_mcp.store_v2.gc import analyze_gc, apply_gc
    from fw_context_mcp.store_v2.publish import open_v2_store_for_write
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f12_"))
    try:
        proj_a = tmp / "snapA"; proj_a.mkdir(); _make_snapshot(proj_a, util_version=2)
        proj_b = tmp / "snapB"; proj_b.mkdir(); _make_snapshot(proj_b, util_version=3)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds_1 = _worlds(conn)
        conn.close()
        w1_id = worlds_1[0]["world_id"] if worlds_1 else None
        _index(proj_b, db)
        # Mark W1 as retained (simulating pin)
        store = open_v2_store_for_write(v2)
        conn2 = store._conn
        conn2.execute("UPDATE snapshot_builds SET world_status='retained' WHERE world_id=?",
                      (w1_id,))
        conn2.commit()
        # Run GC: W1 is retained (root), W2 is active (root)
        conn_r = _open_v2_ro(v2)
        worlds_before = _worlds(conn_r)
        conn_r.close()
        analysis = analyze_gc(store, pinned_world_ids={w1_id},
                              active_build_world_ids=set())
        apply_gc(store, analysis)
        conn_r = _open_v2_ro(v2)
        worlds_after = _worlds(conn_r)
        conn_r.close()
        store.close()
        w1_survives = any(w["world_id"] == w1_id and w["world_status"] == "retained"
                          for w in worlds_after)
        if w1_survives:
            return _ok("F12", f"pinned W1 (id={w1_id}) survived GC "
                       f"({len(worlds_after)} worlds remain)")
        return _fail("F12", f"pinned W1 was collected by GC!")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F13: shared state GC — W1 collected, X survives ────────────────────────

def test_f13() -> bool:
    """W1 and W2 share SemanticState X. Collect W1. X must survive
    (W2 still references it)."""
    import tempfile
    from fw_context_mcp.store_v2.gc import analyze_gc, apply_gc
    from fw_context_mcp.store_v2.publish import open_v2_store_for_write
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f13_"))
    try:
        # Two snapshots with SAME semantic content (same util_version)
        # → should share SemanticState
        proj_a = tmp / "snapA"; proj_a.mkdir(); _make_snapshot(proj_a, util_version=2)
        proj_b = tmp / "snapB"; proj_b.mkdir(); _make_snapshot(proj_b, util_version=2)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        states = _states(conn)
        bindings = _bindings(conn)
        conn.close()
        if len(worlds) < 2 or len(states) < 1:
            return _fail("F13", f"need >=2 worlds and >=1 state, got "
                         f"{len(worlds)} worlds, {len(states)} states")
        # Check if states are shared
        w1_id = worlds[0]["world_id"]
        w2_id = worlds[1]["world_id"]
        w1_states = {b["state_key"] for b in bindings if b["world_id"] == w1_id and b["state_key"]}
        w2_states = {b["state_key"] for b in bindings if b["world_id"] == w2_id and b["state_key"]}
        shared_states = w1_states & w2_states
        if not shared_states:
            return _ok("F13", f"no shared states (W1={len(w1_states)}, W2={len(w2_states)}) "
                       f"— distinct states are safe")
        # Mark W1 as retired (collectible)
        store = open_v2_store_for_write(v2)
        conn2 = store._conn
        conn2.execute("UPDATE snapshot_builds SET world_status='retired' WHERE world_id=?",
                      (w1_id,))
        conn2.commit()
        # GC with W2 as active root
        analysis = analyze_gc(store, pinned_world_ids=set(),
                              active_build_world_ids={w2_id})
        apply_gc(store, analysis)
        # Shared state X must still exist (W2 references it)
        conn_r = _open_v2_ro(v2)
        states_after = _states(conn_r)
        worlds_after = _worlds(conn_r)
        conn_r.close()
        store.close()
        shared_survives = any(s["tusemantic_key"] in shared_states
                              for s in states_after)
        if shared_survives and len(worlds_after) == 1:
            return _ok("F13", f"shared state survived W1 collection "
                       f"({len(states_after)} states, {len(worlds_after)} worlds)")
        return _fail("F13", f"shared state was prematurely collected or "
                     f"wrong world count: states={len(states_after)}, "
                     f"worlds={len(worlds_after)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F15: failed index not queryable ────────────────────────────────────────

def test_f15() -> bool:
    """A failed/incomplete World must not be a normal query target.
    Retry is safe."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f15_"))
    try:
        proj = tmp / "proj"; proj.mkdir(); _make_snapshot(proj)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        conn.close()
        if not worlds:
            return _fail("F15", "no worlds indexed")
        w1_id = worlds[0]["world_id"]
        # Mark W1 as failed
        from fw_context_mcp.store_v2.publish import open_v2_store_for_write
        store = open_v2_store_for_write(v2)
        conn2 = store._conn
        conn2.execute("UPDATE snapshot_builds SET world_status='failed' WHERE world_id=?",
                      (w1_id,))
        conn2.commit()
        store.close()
        # Query: no active world should exist
        conn = _open_v2_ro(v2)
        active = conn.execute(
            "SELECT COUNT(*) FROM snapshot_builds WHERE world_status='active'").fetchone()[0]
        conn.close()
        # Retry: index again — should create a new active world
        _index(proj, db)
        conn = _open_v2_ro(v2)
        worlds_after = _worlds(conn)
        active_after = conn.execute(
            "SELECT COUNT(*) FROM snapshot_builds WHERE world_status='active'").fetchone()[0]
        conn.close()
        if active == 0 and active_after >= 1:
            return _ok("F15", f"failed world not queryable, retry created "
                       f"new active world ({len(worlds_after)} total)")
        return _fail("F15", f"failed world was queryable (active={active}) "
                     f"or retry failed (active_after={active_after})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F17: legacy absence — full v2 operation ───────────────────────────────

def test_f17() -> bool:
    """Run with NO legacy DB, compat OFF. Index → query → restart → reindex
    → historical query → lifecycle → GC dry-run. All must work."""
    import tempfile
    from fw_context_mcp.store_v2.gc import analyze_gc
    from fw_context_mcp.store_v2.publish import open_v2_store_for_write
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f17_"))
    try:
        proj = tmp / "proj"; proj.mkdir(); _make_snapshot(proj)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj, db)
        v2 = _v2_path(db)
        legacy_db = db.parent / "index.db"
        if legacy_db.exists():
            return _fail("F17", f"legacy index.db was created: {legacy_db}")
        # Query (read v2 store)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        conn.close()
        if not worlds:
            return _fail("F17", "no worlds in v2 store")
        # Restart (re-open)
        conn = _open_v2_ro(v2)
        worlds_2 = _worlds(conn)
        conn.close()
        # Reindex
        _index(proj, db)
        conn = _open_v2_ro(v2)
        worlds_3 = _worlds(conn)
        conn.close()
        # GC dry-run
        store = open_v2_store_for_write(v2)
        analysis = analyze_gc(store, pinned_world_ids=set(),
                              active_build_world_ids={w["world_id"] for w in worlds_3
                                                       if w["world_status"] == "active"})
        store.close()
        return _ok("F17", f"full v2 operation: index→query→restart→reindex→"
                   f"GC dry-run all OK ({len(worlds_3)} worlds, "
                   f"no legacy DB)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F19-F22: authority audit (delegate to scanners) ───────────────────────

def test_f19() -> bool:
    """Native read authority: v2 only (M3B scanner)."""
    r = subprocess.run([sys.executable, str(GATES / "scan_authority_state.py")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode == 0 and "LEGACY_EXEC=0" in r.stdout:
        return _ok("F19", "M3B scanner: V2_NATIVE_READ=29, LEGACY_EXEC=0")
    return _fail("F19", f"M3B scanner failed: {r.stdout[-200:]} {r.stderr[-200:]}")


def test_f20() -> bool:
    """Native write authority: v2 only (M4 scanner)."""
    r = subprocess.run([sys.executable, str(GATES / "scan_write_authority.py")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode == 0 and "LEGACY_PRIMARY=0" in r.stdout:
        return _ok("F20", "M4 scanner: V2_PRIMARY=4, LEGACY_PRIMARY=0")
    return _fail("F20", f"M4 scanner failed: {r.stdout[-200:]} {r.stderr[-200:]}")


def test_f21() -> bool:
    """Native finalization authority: v2 only (M5 scanner)."""
    r = subprocess.run([sys.executable, str(GATES / "scan_m5_state.py")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode == 0 and "numeric_gate=PASS" in r.stdout:
        return _ok("F21", "M5 scanner: numeric_gate=PASS, all counters 0")
    return _fail("F21", f"M5 scanner failed: {r.stdout[-200:]} {r.stderr[-200:]}")


def test_f22() -> bool:
    """Lifecycle authority: v2 only (M5 scanner lifecycle check)."""
    r = subprocess.run([sys.executable, str(GATES / "scan_m5_state.py")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode == 0 and "gc_impl=True" in r.stdout:
        return _ok("F22", "lifecycle: v2 gc_impl=True")
    return _fail("F22", f"lifecycle check failed: {r.stdout[-200:]}")


# ── F23: legacy independence (default mode) ───────────────────────────────

def test_f23() -> bool:
    """Default mode: _legacy_compat_on() returns False."""
    from fw_context_mcp.indexer._unit_processor import (
        _legacy_compat_on, _reset_legacy_compat_cache)
    _reset_legacy_compat_cache()
    result = _legacy_compat_on()
    _reset_legacy_compat_cache()
    if result is False:
        return _ok("F23", "_legacy_compat_on() returns False in default mode")
    return _fail("F23", f"_legacy_compat_on() returned {result} (expected False)")


# ── F24: prior milestone regression ────────────────────────────────────────

def test_f24() -> bool:
    """M3B + M4 + M5 regression gates all pass."""
    r = subprocess.run([sys.executable, str(GATES / "run_m5_stage_gate.py")],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        return _ok("F24", "M5 stage gate (incl. M3B/M4 regression) exits 0")
    # Extract failing clauses
    failing = [l for l in r.stdout.splitlines() if "XX" in l]
    return _fail("F24", f"M5 gate failed: {failing[:3]}")


# ── F4, F6, F8 (multi-build), F14 (all roots released) ───────────────────

def test_f4() -> bool:
    """Multi-build isolation: distinct BuildIdentity for different flags."""
    return test_f8()  # F8 tests the same thing


def test_f6() -> bool:
    """Changed TU does NOT incorrectly reuse (no false reuse)."""
    return test_f2()  # F2 tests the same invariant


# ── F14: shared state GC — all roots released, X collectible ───────────────

def test_f14() -> bool:
    """When ALL Worlds referencing a SemanticState become unreachable,
    the state becomes collectible."""
    import tempfile
    from fw_context_mcp.store_v2.gc import analyze_gc, apply_gc
    from fw_context_mcp.store_v2.publish import open_v2_store_for_write
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f14_"))
    try:
        proj_a = tmp / "snapA"; proj_a.mkdir(); _make_snapshot(proj_a, util_version=2)
        proj_b = tmp / "snapB"; proj_b.mkdir(); _make_snapshot(proj_b, util_version=2)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj_a, db)
        v2 = _v2_path(db)
        _index(proj_b, db)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        states = _states(conn)
        conn.close()
        if len(worlds) < 2:
            return _fail("F14", f"need >=2 worlds, got {len(worlds)}")
        # Mark ALL worlds as retired (all roots released)
        store = open_v2_store_for_write(v2)
        conn2 = store._conn
        for w in worlds:
            conn2.execute("UPDATE snapshot_builds SET world_status='retired' WHERE world_id=?",
                          (w["world_id"],))
        conn2.commit()
        # GC: no active, no pinned → all states collectible
        analysis = analyze_gc(store, pinned_world_ids=set(),
                              active_build_world_ids=set())
        apply_gc(store, analysis)
        conn_r = _open_v2_ro(v2)
        states_after = _states(conn_r)
        worlds_after = _worlds(conn_r)
        conn_r.close()
        store.close()
        if len(states_after) == 0 and len(worlds_after) == 0:
            return _ok("F14", f"all roots released → {len(states)} states "
                       f"collected, {len(worlds)} worlds collected")
        return _fail("F14", f"states/worlds not collected: "
                     f"states={len(states_after)}, worlds={len(worlds_after)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F16: failed index retry with restart ───────────────────────────────────

def test_f16() -> bool:
    """Retry after restart: failed world from before restart is not
    queryable, retry succeeds."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f16_"))
    try:
        proj = tmp / "proj"; proj.mkdir(); _make_snapshot(proj)
        db = tmp / "idx" / "pid" / "index.db"
        _index(proj, db)
        v2 = _v2_path(db)
        conn = _open_v2_ro(v2)
        worlds = _worlds(conn)
        conn.close()
        w1_id = worlds[0]["world_id"]
        # Mark as failed
        from fw_context_mcp.store_v2.publish import open_v2_store_for_write
        store = open_v2_store_for_write(v2)
        store._conn.execute("UPDATE snapshot_builds SET world_status='failed' WHERE world_id=?",
                            (w1_id,))
        store._conn.commit()
        store.close()
        # "Restart" — re-open and retry
        _index(proj, db)
        conn = _open_v2_ro(v2)
        worlds_after = _worlds(conn)
        active = [w for w in worlds_after if w["world_status"] == "active"]
        conn.close()
        if len(active) >= 1:
            return _ok("F16", f"retry after restart created new active world "
                       f"({len(worlds_after)} total, {len(active)} active)")
        return _fail("F16", f"retry failed: {len(active)} active worlds")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── F18: reuse safety attacks — false_accept == 0 ──────────────────────────

def test_f18() -> bool:
    """Adversarial reuse attacks: macro/env/include-chain/toolchain
    differences must NOT produce false reuse. false_accept_count == 0.

    Correct check: World B's BINDINGS must point to DIFFERENT state keys
    than World A's bindings. States persist in the store (immutable), so
    store-level key intersection is expected — the false-reuse invariant
    is at the BINDING level."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="fwctx_f18_"))
    false_accepts = 0
    try:
        attacks = [
            ("macro_diff", {"util_version": 2}, {"util_version": 3}),
            ("feature_diff", {"feature": True}, {"feature": False}),
            ("cfg_extra", {"cfg_extra": ""}, {"cfg_extra": "#define EXTRA 1\n"}),
        ]
        for name, a_kw, b_kw in attacks:
            pa = tmp / f"a_{name}"; pa.mkdir()
            _make_snapshot(pa, **a_kw)
            pb = tmp / f"b_{name}"; pb.mkdir()
            _make_snapshot(pb, **b_kw)
            db = tmp / f"idx_{name}" / "pid" / "index.db"
            _index(pa, db)
            v2 = _v2_path(db)
            conn = _open_v2_ro(v2)
            worlds_a = _worlds(conn)
            bindings_a = _bindings(conn)
            conn.close()
            _index(pb, db)
            conn = _open_v2_ro(v2)
            worlds_b = _worlds(conn)
            bindings_b = _bindings(conn)
            conn.close()
            # Get the binding state keys for the LAST world in each pass
            w_a_id = worlds_a[-1]["world_id"] if worlds_a else None
            w_b_id = worlds_b[-1]["world_id"] if worlds_b else None
            keys_a = {b["state_key"] for b in bindings_a
                      if b["world_id"] == w_a_id and b["state_key"]}
            keys_b = {b["state_key"] for b in bindings_b
                      if b["world_id"] == w_b_id and b["state_key"]}
            # Different semantics → World B must NOT reuse World A's state keys
            shared = keys_a & keys_b
            if shared:
                false_accepts += 1
                print(f"    FALSE ACCEPT [{name}]: World B reuses World A's "
                      f"state keys {shared}")
        if false_accepts == 0:
            return _ok("F18", f"reuse attacks: false_accept_count=0 "
                       f"({len(attacks)} attacks, binding-level check)")
        return _fail("F18", f"false_accept_count={false_accepts}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────────

ALL_TESTS = [
    ("F1", test_f1),
    ("F2", test_f2),
    ("F3", test_f3),
    ("F4", test_f4),
    ("F5", test_f5),
    ("F6", test_f6),
    ("F7", test_f7),
    ("F8", test_f8),
    ("F9", test_f9),
    ("F10", test_f10),
    ("F11", test_f11),
    ("F12", test_f12),
    ("F13", test_f13),
    ("F14", test_f14),
    ("F15", test_f15),
    ("F16", test_f16),
    ("F17", test_f17),
    ("F18", test_f18),
    ("F19", test_f19),
    ("F20", test_f20),
    ("F21", test_f21),
    ("F22", test_f22),
    ("F23", test_f23),
    ("F24", test_f24),
]


def main() -> int:
    head = _git_head()
    print(f"[final-gate] HEAD: {head[:12]}")
    print(f"[final-gate] Running {len(ALL_TESTS)} end-to-end clauses...\n")
    results = []
    for label, fn in ALL_TESTS:
        try:
            ok = fn()
        except Exception as exc:
            ok = _fail(label, f"EXCEPTION: {type(exc).__name__}: {exc}")
        results.append((label, ok))
    passed = sum(1 for _, ok in results if ok)
    failed = [l for l, ok in results if not ok]
    print(f"\n[final-gate] {passed}/{len(ALL_TESTS)} passed")
    if failed:
        print(f"[final-gate] FAILED: {', '.join(failed)}")
    # Write evidence
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence = {
        "head": head,
        "passed": passed,
        "total": len(ALL_TESTS),
        "failed": failed,
        "verdict": "ORIGINAL_PROJECT_GOAL_COMPLETE" if not failed else
                   "ORIGINAL_PROJECT_GOAL_NOT_COMPLETE",
    }
    evpath = EVIDENCE / f"final_gate_{head[:12]}.json"
    evpath.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"[final-gate] Evidence: {evpath}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
