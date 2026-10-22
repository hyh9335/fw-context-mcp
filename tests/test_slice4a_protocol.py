"""Slice-4A protocol tests (task §49 S4A.11) — T1–T24.

Every test drives the REAL pipeline: ``make_project`` + ``runner.run`` with
the v2 flag on, the production admission hook, and the production bind path.
No toy re-implementation of any gate.  Parse activity is observed by
instrumenting :func:`fw_context_mcp.indexer.symbols.extract_all` — the single
Tier-3 libclang parse entry point (imported per-call inside
``_check_and_parse_unit``, so a module-level patch intercepts every parse).

Naming: ``test_tNN_*`` mirrors the task's T-numbering.  §-references in
docstrings point at the Slice-4A task sections the test defends.
"""
from __future__ import annotations

import json
import os
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
from fw_context_mcp.store_v2.certify import CERT_VERSION      # noqa: E402

CERT_VERSION = CERT_VERSION  # re-export for readability


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
def parse_count(monkeypatch):
    """Counts Tier-3 libclang parses across the whole test."""
    calls = {"n": 0}
    real = _symbols.extract_all

    def counting(*a, **k):
        calls["n"] += 1
        if os.environ.get("S4A_TRACE_PARSE"):
            import traceback
            print(f"=== parse #{calls['n']} ===")
            traceback.print_stack()
        return real(*a, **k)

    monkeypatch.setattr(_symbols, "extract_all", counting)
    return calls


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


def _manifest_entry(proj: Path, cfg_hash: str, tu_rel: str) -> dict | None:
    p = proj / ".fw-context" / f"manifest.{cfg_hash}.json"
    if not p.exists():
        # manifest lives next to the db (db_dir), not in the project
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    units = data.get("units", data)
    for key in (tu_rel, f"./{tu_rel}"):
        if isinstance(units, dict) and key in units:
            return units[key]
    return None


def _manifest_path(db_path: Path, cfg_hash: str) -> Path:
    return db_path.parent / f"manifest.{cfg_hash}.json"


def _touch(proj_rel: Path) -> None:
    future = time.time() + 30.0
    os.utime(proj_rel, (future, future))


def _files_row(db_path: Path, rel: str) -> dict | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM files WHERE path LIKE ?", (f"%{rel}",))]
        return rows[-1] if rows else None
    finally:
        conn.close()


def _cert_rows(v2: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT cert_version, status FROM tu_certificates")]
    finally:
        conn.close()


def _cert_count(v2: Path) -> int:
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT count(*) FROM tu_certificates").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def proj_env(tmp_path):
    """make_project + project id + db path (run A not executed yet)."""
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    pid = generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    return {"proj": proj, "pid": pid, "db_path": db_path,
            "v2": _v2(db_path)}


# ── T1: flag-off inertness (§24: flag off = byte-identical legacy path) ──────

def test_t01_flag_off_inert(tmp_path, parse_count):
    os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    data = make_project(tmp_path)
    proj, pid = data["proj"], generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    try:
        _run(proj, pid, db_path)
        _touch(proj / "inc" / "util.h")
        _run(proj, pid, db_path)
        assert not _v2(db_path).exists()           # no v2 store created
        # legacy tiers: first run parses, second run takes Tier-1/2 (mtime
        # churn only forces Tier-3 only when the manifest flags staleness —
        # either way NOTHING fast-reuse related happened)
        assert parse_count["n"] >= 1
    finally:
        _UP._reset_v2_flag_cache()
        C.reset_flag_cache()


# ── T2: first flag-on run — no admission rows, store born via certify ────────

def test_t02_first_run_no_admissions(proj_env, parse_count, _v2_flags):
    cfg = _run(proj_env["proj"], proj_env["pid"], proj_env["db_path"])
    assert _v2(proj_env["db_path"]).exists()
    assert _adm(_v2(proj_env["db_path"])) == []    # store absent at hook time
    assert parse_count["n"] == 1                   # parsed through Tier 3
    rows = _cert_rows(_v2(proj_env["db_path"]))
    assert rows == [{"cert_version": CERT_VERSION, "status": "certified"}]


# ── T3/T18: hit run — parse skipped, bookkeeping ran, manifest untouched ─────

def test_t03_hit_run_skips_parse(proj_env, parse_count, _v2_flags):
    proj, db_path, v2 = proj_env["proj"], proj_env["db_path"], proj_env["v2"]
    cfg_a = _run(proj, proj_env["pid"], db_path)
    assert parse_count["n"] == 1
    parse_count["n"] = 0                           # count run B separately
    files_before = _files_row(db_path, "main.c")
    mpath = _manifest_path(db_path, cfg_a)
    assert mpath.exists(), "manifest must exist after run A"
    manifest_before = mpath.read_text(encoding="utf-8")
    main_time = (proj / "src" / "main.c").stat().st_mtime

    _touch(proj / "inc" / "util.h")                # mtime-only churn
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")

    cfg_b = _run(proj, proj_env["pid"], db_path)
    assert cfg_b == cfg_a                          # same config
    # §19/§20: zero parses, admission hit, bind replay
    if parse_count["n"] != 0:                      # diagnostics on failure
        for r in _adm(v2):
            print("ADM:", r["admitted"], r["fallback_reason"],
                  r["evidence_json"][:600])
    assert parse_count["n"] == 0
    rows = _adm(v2)
    assert len(rows) == 1
    r = rows[0]
    assert r["admitted"] == 1
    assert r["fallback_reason"] == ""
    assert r["legacy_gate"] == "pass"
    assert r["binding_status"] == "bound_existing"
    assert r["cert_id"] == 1
    # §19: the hit runs _handle_unchanged bookkeeping: file record refreshed.
    # content_hash MAY legitimately change vs run A: the manifest appeared
    # between the runs, so the Tier-2 tuple (source, flags, manifest-entry)
    # differs from run A's manifest-less tuple — identical to what the
    # legacy Tier-2 path itself would write.  The invariant that matters:
    # a valid current hash + refreshed mtime, and STABILITY across
    # consecutive hits.
    files_after = _files_row(db_path, "main.c")
    assert files_after is not None
    assert files_after["content_hash"]
    assert files_after["mtime"] >= files_before["mtime"]
    # §19: NO manifest-entry refresh on the hit path
    assert mpath.read_text(encoding="utf-8") == manifest_before
    # the source mtime was still recorded as of the bookkeeping write
    assert (proj / "src" / "main.c").stat().st_mtime >= main_time
    # consecutive-hit stability: same touches, same hash, still no parse
    _touch(proj / "inc" / "util.h")
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, proj_env["pid"], db_path)
    assert parse_count["n"] == 0
    files_c = _files_row(db_path, "main.c")
    assert files_c["content_hash"] == files_after["content_hash"]
    rows = _adm(v2)
    assert len(rows) == 2 and rows[1]["admitted"] == 1


# ── T4: source edit → A3 miss → exact original full path ─────────────────────

def test_t04_source_edit_miss(proj_env, parse_count, _v2_flags):
    proj, db_path, v2 = proj_env["proj"], proj_env["db_path"], proj_env["v2"]
    _run(proj, proj_env["pid"], db_path)
    src = proj / "src" / "main.c"
    src.write_text(src.read_text(encoding="utf-8").replace(
        "return", "return 0 +"), encoding="utf-8")
    _touch(src)
    _run(proj, proj_env["pid"], db_path)
    rows = _adm(v2)
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"].startswith("A3:no-candidate")
    assert parse_count["n"] == 2                   # run A parse + run B parse
    assert _cert_count(v2) == 2                    # re-certified after parse


# ── T5/T6/T7: §32 legacy-gate cannot be bypassed ─────────────────────────────

def test_t05_flags_change_gate_miss(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    cc = proj / "compile_commands.json"
    cc.write_text(cc.read_text(encoding="utf-8").replace(
        "-DFEATURE=1", "-DFEATURE=2"), encoding="utf-8")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "legacy_gate:no-current-config-row"
    assert parse_count["n"] == 2


def test_t06_rename_gate_miss(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    (proj / "src" / "main.c").rename(proj / "src" / "main2.c")
    cc = proj / "compile_commands.json"
    data = json.loads(cc.read_text(encoding="utf-8"))
    # entry["file"] is an ABSOLUTE backslash path — rewrite via json, not
    # text replace, so BOTH the file field and the arguments move.
    data[0]["file"] = str(proj / "src" / "main2.c")
    data[0]["arguments"] = ["clang", "-c", "src/main2.c", "-Iinc",
                            "--target=x86_64-pc-windows-msvc",
                            "-DFEATURE=1", "-include", "forced.h"]
    cc.write_text(json.dumps(data), encoding="utf-8")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = [r for r in _adm(v2) if r["main_file"].endswith("main2.c")]
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "legacy_gate:no-current-config-row"
    assert parse_count["n"] == 2


def test_t07_first_occurrence_gate_miss(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    (proj / "src" / "util.c").write_text(
        '#include "inc/util.h"\nint util_fn2(void){return 2;}\n',
        encoding="utf-8")
    cc = proj / "compile_commands.json"
    data = json.loads(cc.read_text(encoding="utf-8"))
    entry = dict(data[0])
    entry["arguments"] = ["clang", "-c", "src/util.c", "-Iinc",
                          "--target=x86_64-pc-windows-msvc"]
    entry["file"] = str(proj / "src" / "util.c")
    data.append(entry)
    cc.write_text(json.dumps(data), encoding="utf-8")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = [r for r in _adm(v2) if r["main_file"].endswith("util.c")]
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "legacy_gate:no-current-config-row"
    assert parse_count["n"] == 2                   # util.c parsed


# ── T8/T19: A4 content rehash catches header edits; miss = original path ─────

def test_t08_header_edit_a4_miss(proj_env, parse_count, _v2_flags):
    proj, db_path, v2 = proj_env["proj"], proj_env["db_path"], proj_env["v2"]
    cfg_a = _run(proj, proj_env["pid"], db_path)
    mpath = _manifest_path(db_path, cfg_a)
    manifest_before = mpath.read_text(encoding="utf-8")
    hdr = proj / "inc" / "util.h"
    hdr.write_text(hdr.read_text(encoding="utf-8") + "\n/* touched */\n",
                   encoding="utf-8")
    _touch(hdr)
    _run(proj, proj_env["pid"], db_path)
    rows = _adm(v2)
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"].startswith("A4:content-mismatch")
    assert parse_count["n"] == 2
    # T19: the miss ran the EXACT original path — manifest entry refreshed
    assert mpath.read_text(encoding="utf-8") != manifest_before


# ── T10: C7 env gate ──────────────────────────────────────────────────────────

def test_t10_env_include_gate(proj_env, parse_count, monkeypatch, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    monkeypatch.setenv("CPATH", str(proj / "elsewhere"))
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"] == "A2:env-include-vars-set:CPATH"
    # after the gate miss the legacy tiers still say unchanged (mtime-only)
    assert parse_count["n"] == 1


# ── T11: §31 WorkKey never authorises a skip (adversarial) ────────────────────

def test_t11_workkey_adversarial(proj_env, parse_count, _v2_flags):
    """WorkKey = (source_blob, L1).  Rewrite a closure header with NEW
    content: source blob unchanged, NCC unchanged (headers are not NCC
    legs) ⇒ WorkKey unchanged ⇒ the candidate is still selected — and MUST
    be rejected by A4's recorded-closure rehash.  No skip ever happens."""
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    hdr = proj / "inc" / "cfg.h"
    hdr.write_text(hdr.read_text(encoding="utf-8").replace(
        "1", "9"), encoding="utf-8")
    _touch(hdr)
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert len(rows) == 1
    assert rows[0]["admitted"] == 0
    assert rows[0]["fallback_reason"].startswith("A4:content-mismatch")
    assert parse_count["n"] == 2                   # full parse happened
    # provenance shows the WorkKey-matched candidate was actually tried
    ev = json.loads(rows[0]["evidence_json"])
    assert ev.get("candidates_tried", {}), "candidate must have been tried"
    assert _cert_count(_v2(proj_env["db_path"])) == 2   # re-certified


# ── T12/T13: A3 candidate filters (poisoned / cert_version ramp) ─────────────

def test_t12_poisoned_cert_excluded(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    _adm_write(v2, "UPDATE tu_certificates SET status='poisoned', "
                   "poison_reason='test:manual'")
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert rows[-1]["fallback_reason"] == "A3:no-candidate"
    # Wave decision: the poisoned certificate vetoes reuse only — the
    # content is unchanged, so no re-parse (v2 expected 2).
    assert parse_count["n"] == 1
    # restore → the next run hits again (the filter was the cause)
    _adm_write(v2, "UPDATE tu_certificates SET status='certified', "
                   "poison_reason=''")
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 1
    assert parse_count["n"] == 1                   # still no parse


def test_t13_cert_version_ramp_excluded(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    _adm_write(v2, "UPDATE tu_certificates SET cert_version='fwctx-cert/v1'")
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert rows[-1]["fallback_reason"] == "A3:no-candidate"


# ── T14: attestation loss → miss → re-certification back-fills ────────────────

def test_t14_attestation_loss_ramp(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    _adm_write(v2, "DELETE FROM cert_dir_attestations")
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    # A7-5 (the recorded source-dir pin) reads the same attestation rows and
    # precedes A5′ in the protocol order — it catches the deletion first.
    assert rows[-1]["fallback_reason"] == "A7-5:source-dir-not-attested"
    # Wave decision: the veto does not re-parse (content unchanged), so no
    # re-certification renews the attestations yet.
    assert parse_count["n"] == 1
    n = sqlite3.connect(f"file:{v2}?mode=ro", uri=True).execute(
        "SELECT count(*) FROM cert_dir_attestations").fetchone()[0]
    assert n == 0
    # and the FOLLOWING run is still vetoed — recovery needs a content
    # change, which is when the parse re-certifies and rewrites them.
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert parse_count["n"] == 1


# ── T15/T16: A5′ fresh attestation every admission ────────────────────────────

def test_t15_dir_listing_change(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    (proj / "inc" / "extra.h").write_text("#define EXTRA 1\n",
                                          encoding="utf-8")
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert "listing" in rows[-1]["fallback_reason"]
    # Wave decision: the veto is admission-side; the content hash is
    # unchanged, so no re-parse happens (v2 expected 2 = the fallback
    # re-parse artifact the wave removed).
    assert parse_count["n"] == 1
    # next run: still vetoed — recovery needs a content change under the
    # wave decision (the touch re-parses nothing, so nothing re-certifies)
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    assert _adm(v2)[-1]["admitted"] == 0


def test_t16_dir_pin_change(proj_env, parse_count, _v2_flags):
    import shutil
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    # replace the include dir with a NEW directory of the same content
    old = proj / "inc"
    backup = proj / "_inc_backup"
    shutil.copytree(old, backup)
    shutil.rmtree(old)
    shutil.copytree(backup, old)
    shutil.rmtree(backup)
    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert "dir-identity-changed" in rows[-1]["fallback_reason"]
    # Wave decision: the pin change vetoes reuse only — no re-parse.
    assert parse_count["n"] == 1


# ── T17: A6 identity change vetoes reuse ──────────────────────────────────────

def test_t17_a6_libclang_change(proj_env, parse_count, monkeypatch, _v2_flags):
    from fw_context_mcp.store_v2 import certify as C
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])

    # libclang_identity() returns a plain STRING — patch it wholesale
    monkeypatch.setattr(C, "libclang_identity",
                        lambda: "libclang:fake-other/9.9")

    _touch(proj / "src" / "main.c")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert rows[-1]["admitted"] == 0
    assert rows[-1]["fallback_reason"] == "A6:libclang-changed"
    # Wave decision: the identity change vetoes reuse only — no re-parse.
    assert parse_count["n"] == 1


# ── T20: idempotent replay — consecutive hits stay bound_existing ─────────────

def test_t20_idempotent_replay(proj_env, parse_count, _v2_flags):
    from slice2_helpers import binding_table, counts, metric_rows
    proj, db_path, v2 = proj_env["proj"], proj_env["db_path"], proj_env["v2"]
    _run(proj, proj_env["pid"], db_path)
    shape_a = counts(v2)
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, proj_env["pid"], db_path)
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, proj_env["pid"], db_path)
    shape_c = counts(v2)
    rows = _adm(v2)
    assert len(rows) == 2
    for r in rows:
        assert r["admitted"] == 1
        assert r["binding_status"] == "bound_existing"
    assert shape_c == shape_a                      # no new worlds/states/binds
    assert parse_count["n"] == 1                   # only run A parsed
    # §40 counters: reuse observed, nothing new.  run_id strings are not
    # ordered — assert over the SET of runs.  Pure-hit runs certify nothing
    # (certification_count==0) and bind one existing state; note that
    # existing_semantic_states_matched is certification-driven in Slice-2
    # and stays 0 on pure-hit runs — the reuse evidence lives in
    # reuse_admissions (asserted above).
    metrics = metric_rows(v2)
    hit_runs = [m for m in metrics.values()
                if int(m.get("new_semantic_states", 0)) == 0
                and int(m.get("certification_count", 0)) == 0
                and int(m.get("fast_hits", 0)) >= 1
                and int(m.get("parse_skipped", 0)) >= 1
                and int(m.get("parse_count", 0)) == 0]
    assert len(hit_runs) >= 1                       # at least one pure-hit run


# ── T21: mixed run — one hit + one miss in the same run ───────────────────────

def test_t21_mixed_run(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    # 2-TU project BEFORE run A
    (proj / "src" / "util.c").write_text(
        '#include "inc/util.h"\nint util_fn2(void){return 2;}\n',
        encoding="utf-8")
    cc = proj / "compile_commands.json"
    data = json.loads(cc.read_text(encoding="utf-8"))
    entry = dict(data[0])
    entry["arguments"] = ["clang", "-c", "src/util.c", "-Iinc",
                          "--target=x86_64-pc-windows-msvc"]
    entry["file"] = str(proj / "src" / "util.c")
    data.append(entry)
    cc.write_text(json.dumps(data), encoding="utf-8")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    assert parse_count["n"] == 2
    # run B: edit util.c only
    u = proj / "src" / "util.c"
    u.write_text(u.read_text(encoding="utf-8").replace("2", "3"),
                 encoding="utf-8")
    _touch(u)
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = {r["main_file"]: r for r in _adm(v2)}
    assert rows["src/main.c"]["admitted"] == 1
    assert rows["src/util.c"]["admitted"] == 0
    assert rows["src/util.c"]["fallback_reason"].startswith("A3:no-candidate")
    assert parse_count["n"] == 3                   # only util.c re-parsed


# ── T22: QueryView unaffected by fast-path binds ──────────────────────────────

def test_t22_query_view_after_hits(proj_env, parse_count, _v2_flags):
    from fw_context_mcp.store_v2.query import (diagnose_world,
                                               open_query_connection,
                                               resolve_query_view)
    from fw_context_mcp.store_v2.query import QueryView
    proj, db_path, v2 = proj_env["proj"], proj_env["db_path"], proj_env["v2"]
    _run(proj, proj_env["pid"], db_path)
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, proj_env["pid"], db_path)
    ns, snap, build = sqlite3.connect(f"file:{v2}?mode=ro", uri=True).execute(
        "SELECT b.namespace_id, sb.snapshot_id, b.build_identity_sha "
        "FROM builds b JOIN snapshot_builds sb "
        "ON sb.build_identity_sha = b.build_identity_sha LIMIT 1"
    ).fetchone()
    view = QueryView(repository_namespace=ns, snapshot_id=snap, build_id=build)
    conn = open_query_connection(v2)
    try:
        resolved = resolve_query_view(conn, view)
        assert resolved is not None
        diag = diagnose_world(conn, view)
        bound = sum(1 for b in diag.get("bindings", [])
                    if b.get("binding_status") == "bound") \
            if isinstance(diag, dict) and "bindings" in diag else None
        # at minimum the world must resolve and hold one bound binding
        n_bound = sqlite3.connect(f"file:{v2}?mode=ro", uri=True).execute(
            "SELECT count(*) FROM tu_bindings WHERE "
            "binding_status='bound'").fetchone()[0]
        assert n_bound >= 1
        assert bound is None or bound >= 1
    finally:
        conn.close()


# ── T23: BL-1 gate oracle (exact reason strings, unit level) ──────────────────

def test_t23_legacy_gate_oracle():
    from fw_context_mcp.store_v2.admission import _legacy_gate
    hit = {"src/main.c": type("FHR", (), {"content_hash": "abc"})()}
    assert _legacy_gate(hit, "src/main.c") == ""
    assert _legacy_gate({}, "src/main.c") == "legacy_gate:no-current-config-row"
    miss_hash = {"src/main.c": type("FHR", (), {"content_hash": ""})()}
    assert _legacy_gate(miss_hash, "src/main.c") == \
        "legacy_gate:empty-content-hash"


# ── T24: provenance completeness on every admission row ──────────────────────

def test_t24_provenance_complete(proj_env, parse_count, _v2_flags):
    proj, v2 = proj_env["proj"], proj_env["v2"]
    _run(proj, proj_env["pid"], proj_env["db_path"])
    # one miss + one hit
    hdr = proj / "inc" / "util.h"
    hdr.write_text(hdr.read_text(encoding="utf-8") + "\n/* touched */\n",
                   encoding="utf-8")
    _touch(hdr)
    _run(proj, proj_env["pid"], proj_env["db_path"])
    _touch(proj / "src" / "main.c")
    _touch(proj / "forced.h")
    _run(proj, proj_env["pid"], proj_env["db_path"])
    rows = _adm(v2)
    assert len(rows) == 2
    miss, hit = rows[0], rows[1]
    assert miss["admitted"] == 0 and hit["admitted"] == 1
    # miss carries candidates_tried + the total timing key (a warm-cache
    # content-rehash miss can round to 0.0 ms — sub-millisecond is honest;
    # the HIT row's total is dominated by the mandatory A-c5 clang probe)
    ev = json.loads(miss["evidence_json"])
    assert ev.get("candidates_tried")
    timing_m = json.loads(miss["timing_json"])
    assert "ms_total" in timing_m
    timing_h = json.loads(hit["timing_json"])
    # Slice-4B: A-c5 is skipped on the normal HIT path, so ms_total drops
    # from ~180ms (A-c5-dominated) to ~15ms (non-A-c5 gates only).  The
    # >50ms assertion was the 4A A-c5-dominated expectation; 4B's is >0.
    assert timing_h.get("ms_total", 0) > 0.0       # real admission work done
    # hit carries the gate pass + cert/state provenance + timings
    assert hit["legacy_gate"] == "pass"
    assert hit["cert_id"] >= 1
    assert hit["state_key"]
    # per-check ledger: every mandated check id is present
    checks = json.loads(hit["checks_json"])
    for cid in ("P", "A1", "A2", "A3", "A4", "A7-5", "A5'", "A6", "A7",
                "A-c5", "GATE-LEG", "GATE-LEG-2", "A8"):
        assert cid in checks, cid
