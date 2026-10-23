"""Slice-4A central benchmark (task §36) — S4A.12.

Generates a 100-TU project, runs the REAL pipeline twice:

* Run A — full parse + certification of all 100 TUs (parses == 100).
* Churn — touch every source and header (mtime-only, +30 s) so the legacy
  staleness tiers would re-parse everything, then EDIT 10 sources (real
  content change ⇒ A1 new blob ⇒ A3 finds no candidate).

Run B — the admission protocol must skip the parse for exactly the 90
unchanged TUs (committed hits) and fall through for exactly the 10 edited
ones.  Reports the §36 table: totals / hits / misses / parse count /
parse skipped / new states / reuse / wall time.

Wall times are machine-dependent: asserted only as loose sanity bounds and
printed for the performance report; the machine-neutral facts (counts) are
hard-asserted.  Set ``S4A_BENCH_OUT=<path>`` to also dump the run's JSON
artifact (used to produce SLICE4A_CENTRAL_BENCHMARK.md / metrics).
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

from fw_context_mcp.config.settings import (                  # noqa: E402
    _write_project_id, generate_project_id)
from fw_context_mcp.indexer import runner                     # noqa: E402
from fw_context_mcp.indexer import symbols as _symbols        # noqa: E402

N_TUS = 100
N_MISSES = 10


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


def _make_bench_project(tmp_path: Path) -> tuple[Path, str, Path]:
    proj = tmp_path / "bench"
    (proj / "src").mkdir(parents=True)
    (proj / "inc").mkdir(parents=True)
    entries = []
    for i in range(N_TUS):
        (proj / "inc" / f"h{i:03d}.h").write_text(
            f"#define H{i:03d}_MARK {i}\n", encoding="utf-8")
        (proj / "src" / f"tu{i:03d}.c").write_text(
            f'#include "h{i:03d}.h"\n'
            f"int f{i:03d}(void){{return {i};}}\n", encoding="utf-8")
        entries.append({
            "arguments": ["clang", "-c", f"src/tu{i:03d}.c", "-Iinc",
                          "--target=x86_64-pc-windows-msvc"],
            "directory": str(proj),
            "file": str(proj / "src" / f"tu{i:03d}.c"),
        })
    (proj / "compile_commands.json").write_text(json.dumps(entries),
                                                encoding="utf-8")
    from fw_context_mcp.config.settings import _write_project_id, \
        generate_project_id
    pid = generate_project_id()
    _write_project_id(proj, pid)
    db_path = tmp_path / "idx" / pid / "index.db"
    return proj, pid, db_path


def _run(proj: Path, pid: str, db_path: Path) -> str:
    return runner.run(compile_commands=proj / "compile_commands.json",
                      db_path=db_path, project_root=proj, project_id=pid,
                      index_refs=False, index_embeddings=False,
                      analyze_symbols=False, purge_max_missing_percent=100)


def _touch_tree(proj: Path) -> None:
    future = time.time() + 30.0
    for p in list((proj / "src").glob("*.c")) + \
            list((proj / "inc").glob("*.h")):
        os.utime(p, (future, future))


def _metric_row(v2: Path, run_id: str) -> dict:
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    try:
        return {m: v for m, v in conn.execute(
            "SELECT metric, value FROM slice2_run_metrics WHERE run_id=?",
            (run_id,))}
    finally:
        conn.close()


def test_central_benchmark_100tu(tmp_path, monkeypatch, _v2_flags):
    from slice2_helpers import set_v2_authority_island
    set_v2_authority_island(monkeypatch)   # fast_hits metrics need the finalize block
    calls = {"n": 0}
    real = _symbols.extract_all

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(_symbols, "extract_all", counting)

    proj, pid, db_path = _make_bench_project(tmp_path)

    # ── Run A: full parse + certification ────────────────────────────────
    t_a0 = time.monotonic()
    cfg_a = _run(proj, pid, db_path)
    wall_a = time.monotonic() - t_a0
    assert calls["n"] == N_TUS                    # every TU parsed once

    # ── Churn: mtime-only everywhere + content edit on 10 sources ────────
    _touch_tree(proj)
    for i in range(N_MISSES):
        p = proj / "src" / f"tu{i:03d}.c"
        p.write_text(p.read_text(encoding="utf-8") + f"/* edited {i} */\n",
                     encoding="utf-8")

    # ── Run B: 90 committed hits + 10 full-path misses ───────────────────
    calls["n"] = 0
    t_b0 = time.monotonic()
    cfg_b = _run(proj, pid, db_path)
    wall_b = time.monotonic() - t_b0
    assert cfg_b == cfg_a

    v2 = db_path.parent / "index-v2.db"
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        adm = [dict(r) for r in conn.execute(
            "SELECT * FROM reuse_admissions ORDER BY admission_id")]
        n_certs = conn.execute(
            "SELECT count(*) FROM tu_certificates").fetchone()[0]
    finally:
        conn.close()

    # Run B's rows share its world run_id; run A also wrote hook rows for
    # TUs 2..N (the store is BORN mid-run at TU 1's certification, so the
    # later TUs see a store whose files rows do not exist yet → cheap
    # cold-start gate misses).  Scope the §36 accounting to run B.
    hits_all = [r for r in adm if r["admitted"] == 1]
    assert hits_all, "run B must produce committed hits"
    run_b_id = hits_all[0]["run_id"]
    run_b_rows = [r for r in adm if r["run_id"] == run_b_id]
    run_a_rows = [r for r in adm if r["run_id"] != run_b_id]
    hits = [r for r in run_b_rows if r["admitted"] == 1]
    misses = [r for r in run_b_rows if r["admitted"] == 0]
    metrics = _metric_row(v2, run_b_id)
    hit_ms = [json.loads(r["timing_json"]).get("ms_total", 0.0)
              for r in hits]
    mean_hit_ms = round(sum(hit_ms) / max(len(hit_ms), 1), 2)
    comp_keys = ("ms_p", "ms_a1", "ms_gate_leg", "ms_a2", "ms_a3", "ms_a4",
                 "ms_a5", "ms_a6", "ms_a7", "ms_a_c5", "ms_gate_leg2",
                 "ms_txn1")
    comp_means = {}
    for k in comp_keys:
        vals = [json.loads(r["timing_json"]).get(k) for r in hits]
        vals = [v for v in vals if v is not None]
        if vals:
            comp_means[k] = round(sum(vals) / len(vals), 2)

    report = {
        "tu_total": N_TUS,
        "run_a_parses": N_TUS,
        "run_a_hook_rows_cold_start": len(run_a_rows),
        "run_b_admissions": len(run_b_rows),
        "run_b_hits": len(hits),
        "run_b_misses": len(misses),
        "run_b_parses": calls["n"],
        "run_b_parse_skipped": int(metrics.get("parse_skipped", -1)),
        "run_b_fast_hits": int(metrics.get("fast_hits", -1)),
        "run_b_new_states": int(metrics.get("new_semantic_states", -1)),
        "run_b_parse_count_metric": int(metrics.get("parse_count", -1)),
        "run_b_certifications": int(metrics.get("certification_count", -1)),
        "certs_after_run_b": n_certs,
        "wall_run_a_s": round(wall_a, 2),
        "wall_run_b_s": round(wall_b, 2),
        "mean_hit_ms_total": mean_hit_ms,
        "mean_hit_ms_components": comp_means,
        "hit_fallback_reasons": sorted({r["fallback_reason"]
                                        for r in hits}),
        "miss_fallback_reasons": sorted({r["fallback_reason"]
                                         for r in misses}),
        "hit_binding_status": sorted({r["binding_status"] for r in hits}),
        "hit_legacy_gate": sorted({r["legacy_gate"] for r in hits}),
    }
    print("\n=== §36 CENTRAL BENCHMARK ===")
    print(json.dumps(report, indent=2))

    # ── machine-neutral §36 facts ─────────────────────────────────────────
    assert len(run_b_rows) == N_TUS
    assert len(hits) == N_TUS - N_MISSES
    assert len(misses) == N_MISSES
    assert calls["n"] == N_MISSES                 # only edited TUs parsed
    # run B is a NEW world (10 sources changed ⇒ new build content set), so
    # the 90 unchanged TUs bind their EXISTING states into that world for
    # the first time → bound_new.  (bound_existing occurs when the whole
    # build content is unchanged — see test_t03/test_t20 in the protocol
    # suite.)  Either outcome commits the parse-skip decision (§21).
    assert all(r["binding_status"] == "bound_new" for r in hits), \
        sorted({r["binding_status"] for r in hits})
    assert all(r["legacy_gate"] == "pass" for r in hits)
    assert all(r["fallback_reason"] == "" for r in hits), \
        sorted({r["fallback_reason"] for r in hits})
    assert all(r["fallback_reason"].startswith("A3:no-candidate")
               for r in misses), \
        sorted({r["fallback_reason"] for r in misses})
    assert int(metrics.get("fast_hits", 0)) == len(hits)
    assert int(metrics.get("parse_skipped", 0)) == len(hits)
    # the 10 edited TUs re-certify into NEW KB-C semantic states
    assert int(metrics.get("new_semantic_states", 0)) == N_MISSES
    assert int(metrics.get("parse_count", -1)) == N_MISSES
    assert int(metrics.get("certification_count", -1)) == N_MISSES
    assert n_certs == N_TUS + N_MISSES            # 10 re-certs, no more
    # state-level reuse: exactly the 100 original states + 10 new ones
    n_states = sqlite3.connect(f"file:{v2}?mode=ro", uri=True).execute(
        "SELECT count(*) FROM semantic_states").fetchone()[0]
    assert n_states == N_TUS + N_MISSES

    # ── loose wall-time sanity (machine-dependent; report carries exact) ──
    assert wall_a < 900.0
    assert wall_b < 300.0

    out = os.environ.get("S4A_BENCH_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
