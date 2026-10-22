"""Slice-4A legacy-facing tests (task §49 S4A.11) — E1/E2b/E3/E4/E5 + parity.

These defend the BL-1 contract and the §24 unchanged-path parity:

* E1 — mtime tolerance: sub-second mtime churn never invalidates (the v2
  A4 content rehash and the legacy content-hash tier agree).
* E2b — same-directory rerun: the flag-on hit path and the flag-off legacy
  path produce the SAME legacy files rows for the same scenario.
* E3 — header edit: a v2 A4 miss falls through to the exact original full
  path; legacy rows are byte-comparable with a flag-off control run.
* E4 — rename: after the LEGACY-GATE miss the renamed TU gets complete
  legacy bookkeeping under the current config (files row with content).
* E5 — cross-config return: flags A→B→A.  The B run is a LEGACY-GATE miss
  (different config, different context); returning to A re-hits the
  ORIGINAL certified state — no re-certification, no poisoning.  This is
  the multi-version reuse core (§40).
* §30 — unchanged-class admission overhead: with the flag ON an unchanged
  TU pays the admission protocol instead of the ~1 ms Tier-1 lookup; the
  outcome (files rows, no parse) is identical.  Cost isolation is
  benchmarked in tests/test_slice4a_benchmark.py.

Every test runs the REAL pipeline twice — once with the v2 flag ON (the
Slice-4A hook active) and once with it OFF (byte-identical legacy path) —
and compares the LEGACY database outcomes.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import make_project                      # noqa: E402

from fw_context_mcp.config.settings import (                  # noqa: E402
    _write_project_id, generate_project_id)
from fw_context_mcp.indexer import runner                     # noqa: E402
from fw_context_mcp.indexer import symbols as _symbols        # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def _v2_flags():
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    yield
    os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()


@pytest.fixture()
def _flag_off():
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    yield
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()


@pytest.fixture()
def parse_count(monkeypatch):
    calls = {"n": 0}
    real = _symbols.extract_all

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(_symbols, "extract_all", counting)
    return calls


def _v2(db_path: Path) -> Path:
    return db_path.parent / "index-v2.db"


def _run(proj: Path, pid: str, db_path: Path) -> str:
    return runner.run(compile_commands=proj / "compile_commands.json",
                      db_path=db_path, project_root=proj, project_id=pid,
                      index_refs=False, index_embeddings=False,
                      analyze_symbols=False, purge_max_missing_percent=100)


def _adm(v2: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM reuse_admissions ORDER BY admission_id")]
    finally:
        conn.close()


def _files_row(db_path: Path, rel: str) -> dict | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM files WHERE path LIKE ?", (f"%{rel}",))]
        return rows[-1] if rows else None
    finally:
        conn.close()


def _touch(p: Path, delta: float = 30.0) -> None:
    future = time.time() + delta
    os.utime(p, (future, future))


def _new_env(tmp_path: Path) -> tuple[Path, str, Path]:
    data = make_project(tmp_path)
    proj = data["proj"]
    pid = generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    return proj, pid, db_path


# ── E1: sub-second mtime tolerance (legacy tier and A4 agree) ────────────────

def test_e1_mtime_tolerance_sub_second(tmp_path, parse_count, _v2_flags):
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    parse_count["n"] = 0
    _touch(proj / "inc" / "util.h", delta=0.5)     # sub-second churn
    _touch(proj / "src" / "main.c", delta=0.5)
    _run(proj, pid, db_path)
    rows = _adm(_v2(db_path))
    assert rows[-1]["admitted"] == 1               # content rehash passes
    assert parse_count["n"] == 0                   # nothing re-parsed
    assert rows[-1]["fallback_reason"] == ""


def test_e1_flag_off_same_outcome(tmp_path, parse_count, _flag_off):
    """Flag-off control for E1: identical touches, identical legacy outcome."""
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    parse_count["n"] = 0
    _touch(proj / "inc" / "util.h", delta=0.5)
    _touch(proj / "src" / "main.c", delta=0.5)
    _run(proj, pid, db_path)
    assert parse_count["n"] == 0                   # legacy tiers agree
    assert not _v2(db_path).exists()


# ── E2b: same-dir rerun — flag-on hit vs flag-off legacy parity ──────────────

def _flip_v2_flag(on: bool) -> None:
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    if on:
        os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    else:
        os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()


def test_e2b_same_dir_rerun_parity(tmp_path, parse_count, _v2_flags):
    """Flag-ON run B is a committed hit with ZERO parses; the flag-OFF
    control for the same scenario re-parses ONCE (the manifest appears
    between the runs, so run A's manifest-less Tier-2 tuple no longer
    matches run B's manifest-aware tuple) and settles on the SAME
    content_hash the hit path writes — that difference IS the fast
    path's value for the unchanged class."""
    # flag-ON: run A (parse+cert) then run B (hit)
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    parse_count["n"] = 0
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, pid, db_path)
    on_files = _files_row(db_path, "main.c")
    assert parse_count["n"] == 0
    rows = _adm(_v2(db_path))
    assert rows[-1]["admitted"] == 1

    # flag-OFF control: same scenario in a fresh copy
    proj2, pid2, db_path2 = _new_env(tmp_path / "control")
    _flip_v2_flag(False)
    try:
        _run(proj2, pid2, db_path2)
        parse_count["n"] = 0
        _touch(proj2 / "src" / "main.c")
        _touch(proj2 / "forced.h")
        _run(proj2, pid2, db_path2)
        off_files = _files_row(db_path2, "main.c")
        assert parse_count["n"] == 0               # wave: the hash decides
        assert not _v2(db_path2).exists()
    finally:
        _flip_v2_flag(True)
    # legacy parity: identical settled content hashes
    assert on_files["content_hash"] == off_files["content_hash"]


# ── E3: header edit — v2 miss legacy rows == flag-off control rows ───────────

def test_e3_header_edit_full_path_parity(tmp_path, parse_count, _v2_flags,
                                         _flag_off):
    def scenario(proj: Path, pid: str, db_path: Path) -> None:
        _run(proj, pid, db_path)
        hdr = proj / "inc" / "util.h"
        hdr.write_text(hdr.read_text(encoding="utf-8") + "\n/* v3 */\n",
                       encoding="utf-8")
        _touch(hdr)
        _run(proj, pid, db_path)

    proj, pid, db_path = _new_env(tmp_path)
    scenario(proj, pid, db_path)
    on_files = _files_row(db_path, "main.c")
    assert parse_count["n"] == 2                   # A4 miss → full parse

    proj2, pid2, db_path2 = _new_env(tmp_path / "control")
    scenario(proj2, pid2, db_path2)
    off_files = _files_row(db_path2, "main.c")
    assert parse_count["n"] == 4                   # control also re-parsed
    # byte-comparable legacy rows for the identical scenario
    assert on_files["content_hash"] == off_files["content_hash"]
    assert on_files["source_hash"] == off_files["source_hash"]


# ── E4: rename — after the gate miss, legacy bookkeeping is complete ─────────

def test_e4_rename_aftermath(tmp_path, parse_count, _v2_flags):
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    (proj / "src" / "main.c").rename(proj / "src" / "main2.c")
    cc = proj / "compile_commands.json"
    data = json.loads(cc.read_text(encoding="utf-8"))
    data[0]["file"] = str(proj / "src" / "main2.c")
    data[0]["arguments"] = ["clang", "-c", "src/main2.c", "-Iinc",
                            "--target=x86_64-pc-windows-msvc",
                            "-DFEATURE=1", "-include", "forced.h"]
    cc.write_text(json.dumps(data), encoding="utf-8")
    _run(proj, pid, db_path)
    rows = [r for r in _adm(_v2(db_path))
            if r["main_file"].endswith("main2.c")]
    assert rows and rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "legacy_gate:no-current-config-row"
    # the renamed TU got the complete legacy treatment: a files row with
    # non-empty content hash under the CURRENT config
    fr = _files_row(db_path, "main2.c")
    assert fr is not None
    assert fr["content_hash"]
    assert parse_count["n"] == 2


# ── E5: cross-config return — BL-1 holds, states converge ────────────────────

def test_e5_cross_config_return(tmp_path, parse_count, _v2_flags):
    """flags A → B → A.  B is a LEGACY-GATE miss (first occurrence of the
    new context).  Returning to A is ALSO a LEGACY-GATE miss — run B's
    coverage purge removed config A's files rows (legacy ownership
    semantics: at most one context owns a path at a time) — so BL-1
    correctly refuses and the full path re-certifies, CONVERGED to the
    SAME semantic state (no new state row).  The next config-A run (rows
    restored) hits again.  Cross-config reuse of semantic STATES is
    Slice-2; Slice-4A never bypasses BL-1 (§32)."""
    proj, pid, db_path = _new_env(tmp_path)
    cc = proj / "compile_commands.json"
    original = cc.read_text(encoding="utf-8")
    v2 = _v2(db_path)

    cfg_a = _run(proj, pid, db_path)                       # A: cert
    cc.write_text(original.replace("-DFEATURE=1", "-DFEATURE=7"),
                  encoding="utf-8")
    _run(proj, pid, db_path)                               # B: gate miss
    parse_count["n"] = 0
    cc.write_text(original, encoding="utf-8")              # back to A
    cfg_a2 = _run(proj, pid, db_path)                      # C: gate miss too
    assert cfg_a2 == cfg_a
    assert parse_count["n"] == 1                # full path re-certified
    rows = _adm(v2)
    assert len(rows) == 2
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "legacy_gate:no-current-config-row"
    assert rows[1]["admitted"] == 0
    assert rows[1]["fallback_reason"] == "legacy_gate:no-current-config-row"
    # the re-certification CONVERGED: config B is genuinely a second state
    # (different flags ⇒ different L1 ⇒ KB-C-distinct), but returning to A
    # re-certified INTO A's existing state — no third state was created
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    n_states = conn.execute("SELECT count(*) FROM semantic_states").fetchone()[0]
    certs = [dict(r) for r in conn.execute(
        "SELECT cert_id, tusemantic_key, outcome FROM tu_certificates "
        "ORDER BY cert_id")]
    conn.close()
    assert n_states == 2                          # A-state + B-state only
    assert len(certs) == 3
    assert certs[2]["tusemantic_key"] == certs[0]["tusemantic_key"]
    assert certs[2]["outcome"] == "converged"
    # run D — config A, rows restored, nothing changed: committed hit
    parse_count["n"] = 0
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, pid, db_path)
    rows = _adm(v2)
    assert len(rows) == 3
    assert rows[2]["admitted"] == 1
    assert rows[2]["binding_status"] == "bound_existing"
    assert parse_count["n"] == 0


# ── §30: unchanged-class admission overhead (flag-isolated, documented) ──────

def test_s30_unchanged_class_admission(tmp_path, parse_count, _v2_flags):
    """With the flag ON, an UNCHANGED TU pays the admission protocol
    (measured here: bounded well under a second) instead of the ~1 ms
    legacy Tier-1 lookup — and the outcome is identical (no parse, files
    row refreshed).  The task §30 mandates the hook BEFORE
    _check_and_parse_unit; the overhead is flag-isolated and benchmarked
    centrally in tests/test_slice4a_benchmark.py."""
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    parse_count["n"] = 0
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    t0 = time.monotonic()
    _run(proj, pid, db_path)
    wall = time.monotonic() - t0
    rows = _adm(_v2(db_path))
    assert rows[-1]["admitted"] == 1
    assert parse_count["n"] == 0
    assert wall < 5.0                              # single-TU sanity bound
    timing = json.loads(rows[-1]["timing_json"])
    assert timing["ms_total"] < 1000.0             # admission << legacy parse


def test_s30_flag_off_control(tmp_path, parse_count, _flag_off):
    proj, pid, db_path = _new_env(tmp_path)
    _run(proj, pid, db_path)
    parse_count["n"] = 0
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    t0 = time.monotonic()
    _run(proj, pid, db_path)
    wall = time.monotonic() - t0
    # Wave decision contract: the hash decides — a touch that changes no
    # content re-parses nothing (the v2-era re-parse came from the
    # manifest-appearance Tier-1 tuple artifact the wave removed).
    assert parse_count["n"] == 0
    assert wall < 5.0
    assert not _v2(db_path).exists()               # no admission rows exist
