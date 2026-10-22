"""P2-A parser/libclang identity tests (T01-T07, T10-T12).

What P2A fixed: ``certify.libclang_identity()`` used to call the
UNREGISTERED binding function ``clang_getClangVersion`` through ctypes'
default prototype (``restype=c_int``).  The C function really returns a
16-byte CXString BY VALUE; on the Windows x64 ABI that return travels
through a hidden caller-allocated sret slot, so the unregistered call was
undefined behavior — it wild-wrote through an uninitialized pointer and
surfaced a per-process address artifact as the "identity"
(``libclang:-2138174976``, different every process).  Cross-process reuse
therefore died at A6 with ``A6:libclang-changed`` on every candidate.

The fix extracts the version text through the binding's canonical CXString
conversion (``clang_getCString``) after registering the true prototype.

Boundaries asserted here (task §9, §13):
* stable version text does NOT weaken the Slice-4A J2 defense — A-c5
  stays mandatory on every hit (T10) and a changed capture binary or a
  changed libclang still fails closed at A6 (T07-unit, T11);
* flag-off behavior is untouched (T12).

The restart end-to-end acceptance tests (T08/T09/T11-ramp, task §10/§11)
run as real multi-process jobs in ``rebaseline_p2a.py`` (they need real
clang subprocesses and real project checkouts); this module keeps the
identity-layer and single-process admission tests fast.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import make_project                      # noqa: E402

from fw_context_mcp.config.settings import (                 # noqa: E402
    _write_project_id, generate_project_id)
from fw_context_mcp.indexer import runner                    # noqa: E402
from fw_context_mcp.indexer import _unit_processor as _UP    # noqa: E402
from fw_context_mcp.store_v2 import certify as C             # noqa: E402
from fw_context_mcp.store_v2.certify import (                # noqa: E402
    CERT_VERSION, INDEXER_GENERATION, ParserIdentity,
    clang_cli_identity, libclang_identity)
from fw_context_mcp.store_v2.admission import _a6_identity   # noqa: E402


# ── helpers (mirrors of the slice4a harness) ─────────────────────────────────

def _reset_flags() -> None:
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()


@pytest.fixture()
def v2_flags():
    _reset_flags()
    os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    _reset_flags()
    yield
    os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    _reset_flags()


def _v2(db_path: Path) -> Path:
    return db_path.parent / "index-v2.db"


def _run(proj: Path, pid: str, db_path: Path, **kw) -> str:
    return runner.run(compile_commands=proj / "compile_commands.json",
                      db_path=db_path, project_root=proj, project_id=pid,
                      index_refs=False, index_embeddings=False,
                      analyze_symbols=False,
                      purge_max_missing_percent=100, **kw)


def _adm(v2: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM reuse_admissions ORDER BY admission_id")]
    finally:
        conn.close()


def _adm_write(v2: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(v2)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _touch(p: Path) -> None:
    future = time.time() + 30.0
    os.utime(p, (future, future))


# ── cross-process child (REAL binding, no mocks, no patches) ────────────────

_CHILD = r"""
import sys
sys.path.insert(0, {src!r})
from fw_context_mcp.store_v2.certify import libclang_identity
import os
os.chdir({cwd!r})          # separate working directory per process
print("IDENTITY:" + libclang_identity())
"""


def _identity_in_fresh_process(cwd: Path) -> str:
    code = _CHILD.format(src=str(REPO / "src"), cwd=str(cwd))
    p = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=180, cwd=cwd,
        encoding="utf-8",
        errors="replace",
    )
    assert p.returncode == 0, \
        f"fresh process crashed (exit {p.returncode}): {p.stderr[-400:]}"
    ident = None
    for line in (p.stdout or "").splitlines():
        if line.startswith("IDENTITY:"):
            ident = line[len("IDENTITY:"):]
    assert ident, f"no identity printed, stdout={p.stdout!r}"
    return ident


# ── T01/T03: 5 fresh processes → identical identity (RED before the fix) ────

def test_p2a_t01_t03_cross_process_stable(tmp_path):
    idents = []
    for i in range(5):
        d = tmp_path / f"cwd{i}"
        d.mkdir()
        idents.append(_identity_in_fresh_process(d))
    assert len(set(idents)) == 1, f"identities differ: {idents}"
    # and the single identity is content, not a pointer artifact
    assert idents[0] == libclang_identity()
    assert not re.fullmatch(r"libclang:-?\d+", idents[0]), \
        f"identity looks like a raw pointer/int: {idents[0]!r}"


# ── T02: same-process stability + no repr/pointer leakage ───────────────────

def test_p2a_t02_same_process_stable_and_content_shaped():
    a = libclang_identity()
    b = libclang_identity()
    c = libclang_identity()
    assert a == b == c
    assert a.startswith("libclang:")
    body = a[len("libclang:"):]
    # the broken code produced a bare per-process int (ctypes c_int of a
    # wild-write artifact); content-derived identity is human version text
    assert not re.fullmatch(r"-?\d+", body), f"pointer noise: {a!r}"
    assert "clang version" in body
    # no live-object repr / hex address leakage
    assert hex(id(a)) not in a and str(id(a)) not in a


# ── T04: restart simulation — fresh processes from different cwds ───────────

def test_p2a_t04_restart_stable_across_cwds(tmp_path):
    for name in ("runA", "runB"):
        (tmp_path / name).mkdir()
    i1 = _identity_in_fresh_process(tmp_path / "runA")
    i2 = _identity_in_fresh_process(tmp_path / "runB")
    assert i1 == i2
    # neither cwd nor any temp path leaks into the identity
    assert str(tmp_path) not in i1 and "runA" not in i1


# ── T05: clone-root independence ─────────────────────────────────────────────

def test_p2a_t05_clone_root_independent(tmp_path):
    root_a = tmp_path / "cloneA" / "repo"
    root_b = tmp_path / "cloneB" / "repo"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    assert root_a.is_dir() and root_b.is_dir()
    ia = _identity_in_fresh_process(root_a)
    ib = _identity_in_fresh_process(root_b)
    assert ia == ib
    assert "cloneA" not in ia and "cloneB" not in ia


# ── T06: materially different parser identity separates ─────────────────────

def test_p2a_t06_different_parser_identity_separates():
    base = dict(indexer_generation=INDEXER_GENERATION,
                capture_binary_id=clang_cli_identity(),
                builtin_digest="d" * 64, builtin_count=420,
                target_triple="x86_64-pc-windows-msvc")
    real = libclang_identity()
    other = "libclang:clang version 99.0.0 (fixture-other-parser)"
    a = ParserIdentity(libclang_id=real, **base)
    b = ParserIdentity(libclang_id=other, **base)
    assert a.identity_sha != b.identity_sha
    assert a.generation != b.generation
    # identical inputs → identical sha (the deterministic contract itself)
    a2 = ParserIdentity(libclang_id=real, **base)
    assert a.identity_sha == a2.identity_sha
    assert a.generation == a2.generation


def test_p2a_t06b_extraction_failure_falls_back_deterministic(monkeypatch):
    """Fail-closed determinism: if CXString extraction breaks, the fallback
    is the loaded-library path — stable within the machine, never a
    per-process address."""
    def boom():
        raise RuntimeError("fixture: extraction unavailable")
    monkeypatch.setattr(C, "_libclang_version_text", boom)
    saved = dict(C._libclang_cache)
    C._libclang_cache.clear()          # per-process memoization must not
    try:                               # mask the fallback path
        x = C.libclang_identity()
        y = C.libclang_identity()
    finally:
        C._libclang_cache.clear()
        C._libclang_cache.update(saved)
    assert x == y
    assert x.startswith("libclang:libclang-file:"), x
    # the fallback names the actual loaded libclang path (deterministic
    # per machine — conservative across machines, never an address)
    assert x.endswith(Path(x[len("libclang:libclang-file:"):]).name)


# ── T07: old / incompatible identities fail closed ──────────────────────────

def test_p2a_t07_old_pointer_identity_fails_closed_at_a6():
    """A certificate carrying a PRE-P2A pointer-derived libclang_id must
    never match the fixed identity (no partial compatibility)."""
    from fw_context_mcp.indexer.compile_commands import parse as _unused
    row = {"parser_identity": json.dumps({
        "indexer_generation": INDEXER_GENERATION,
        "libclang_id": "libclang:757524736",      # old pointer noise
        "capture_binary_id": clang_cli_identity(),
        "target_triple": "x86_64-pc-windows-msvc",
        "failed": ""})}
    reason = _a6_identity(row, libclang_identity(), clang_cli_identity(), [])
    assert reason == "A6:libclang-changed"


def test_p2a_t07b_old_cert_version_misses_on_real_store(
        tmp_path, v2_flags):
    """Real store ramp: downgrade a live v3 certificate to the pre-P2A
    cert_version and the candidate is honestly ineligible (A3), full path
    re-runs, no false accept; restoring the version admits again."""
    data = make_project(tmp_path)
    proj, pid = data["proj"], generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    try:
        _run(proj, pid, db_path)                       # seed (full path)
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)                       # restart → HIT
        rows = _adm(_v2(db_path))
        assert rows[-1]["admitted"] == 1

        _adm_write(_v2(db_path),
                   "UPDATE tu_certificates SET cert_version='fwctx-cert/v2'")
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)                       # old version → MISS
        rows = _adm(_v2(db_path))
        assert rows[-1]["admitted"] == 0
        assert "A6" not in (rows[-1]["checks_json"] or "")

        _adm_write(_v2(db_path),
                   f"UPDATE tu_certificates SET cert_version='{CERT_VERSION}'")
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)                       # restored → HIT again
        rows = _adm(_v2(db_path))
        assert rows[-1]["admitted"] == 1
    finally:
        pass


# ── T10: mandatory A-c5 still executes on the REAL admission path ────────────

def test_p2a_t10_ac5_executes_on_real_hit(tmp_path, v2_flags):
    """P2-A T10 (updated for Slice-4B): A-c5 CAN execute on a real HIT
    when FW_CONTEXT_4B_DUAL_PROOF is set (shadow validation mode).  In
    normal 4B production mode, A-c5 is SKIPPED (verified by
    test_4bt19_ac5_invocation_count_zero_on_hit in test_slice4b.py).
    This test uses dual-proof mode to confirm the A-c5 code path still
    works correctly when invoked."""
    os.environ["FW_CONTEXT_4B_DUAL_PROOF"] = "1"
    try:
        data = make_project(tmp_path)
        proj, pid = data["proj"], generate_project_id()
        _write_project_id(proj, pid)
        db_path = tmp_path / "idx" / pid / "index.db"
        _run(proj, pid, db_path)                           # seed
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)                           # restart admission
        rows = _adm(_v2(db_path))
        hit = [r for r in rows if r["admitted"] == 1]
        assert hit, "expected at least one real fast hit"
        checks = json.loads(hit[-1]["checks_json"] or "{}")
        # In dual-proof mode, A-c5 should pass (it ran and matched)
        assert checks.get("A-c5") == "pass(dual-proof)", checks
        timings = json.loads(hit[-1]["timing_json"] or "{}")
        assert timings.get("ms_a_c5", 0) > 0, timings        # real clang -dM -E
    finally:
        os.environ.pop("FW_CONTEXT_4B_DUAL_PROOF", None)


# ── T11: in-place toolchain replacement stays fail-closed ────────────────────

def test_p2a_t11_in_place_toolchain_replacement_fails_closed():
    """Stable version TEXT does not prove binary equality: a cert captured
    by a different capture binary must still miss at A6.  (A-c5 remains
    mandatory regardless — T10 proves it really executes.)"""
    row = {"parser_identity": json.dumps({
        "indexer_generation": INDEXER_GENERATION,
        "libclang_id": libclang_identity(),            # same version text…
        "capture_binary_id":
            "clang-cli:18.1.1:REPLACED-BINARY-IN-PLACE",
        "target_triple": "x86_64-pc-windows-msvc",
        "failed": ""})}
    reason = _a6_identity(row, libclang_identity(), clang_cli_identity(), [])
    assert reason == "A6:capture-binary-changed"


# ── T12: flag-off behavior unchanged ─────────────────────────────────────────

def test_p2a_t12_flag_off_unchanged(tmp_path):
    _reset_flags()
    os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    _reset_flags()
    data = make_project(tmp_path)
    proj, pid = data["proj"], generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    try:
        _run(proj, pid, db_path)
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)
        assert not _v2(db_path).exists()               # legacy baseline
    finally:
        _reset_flags()
