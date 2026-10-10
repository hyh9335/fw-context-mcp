"""FINAL ECONOMIC CLOSURE BENCHMARK v2 (§10-17).

Direct-API benchmark — bypasses runner.run() to control build_name precisely.

Two modes:
  REFERENCE: each build indexed independently with a fresh M6 store.
  PRODUCTION M6: all builds indexed into the SAME M6 store (reuse).

Records per-build:
  - frontend_preprocess_parse (extract_all time)
  - semantic_materialization (materialize_unit_into_m6 time)
  - total_wall_time
  - facts_observed, facts_new, facts_reused
  - semantic_storage_bytes
"""
from __future__ import annotations
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))
sys.path.insert(0, str(REPO / ".dsh-control" / "corpora" / "harness"))

CLANG = r"C:\Program Files\LLVM\bin\clang.exe"
RESOURCE = r"C:\Program Files\LLVM\lib\clang\22\include"
CORPUS = REPO / ".dsh-control" / "m6_research" / "corpus" / "FreeRTOS-Kernel"

# 10 build configs — highly overlapping (same TUs, different -D flags)
BUILD_CONFIGS = [
    {"name": "BASE", "defines": []},
    {"name": "B2", "defines": ["-DconfigUSE_PREEMPTION=1"]},
    {"name": "B3", "defines": ["-DconfigUSE_PORT_OPTIMISED_TASK_SELECTION=1"]},
    {"name": "B4", "defines": ["-DconfigUSE_TICKLESS_IDLE=1"]},
    {"name": "B5", "defines": ["-DconfigUSE_MUTEXES=1"]},
    {"name": "B6", "defines": ["-DconfigUSE_RECURSIVE_MUTEXES=1"]},
    {"name": "B7", "defines": ["-DconfigUSE_COUNTING_SEMAPHORES=1"]},
    {"name": "B8", "defines": ["-DconfigUSE_QUEUE_SETS=1"]},
    {"name": "B9", "defines": ["-DconfigUSE_NEWLIB_REENTRANT=1"]},
    {"name": "B10", "defines": ["-DconfigUSE_TIMERS=1"]},
]

SCALES = [1, 2, 5, 10]
REPEATS = 1  # Single repeat — each build takes ~90s frontend, 10 builds × 2 modes = ~30min/scale


def _make_compile_commands(build_defines: list[str]) -> list:
    """Create compile_commands entries for the FreeRTOS corpus with given defines."""
    files = [CORPUS / "list.c", CORPUS / "queue.c"]
    entries = []
    for f in files:
        # IMPORTANT: stubs MUST come first (as -I, not -isystem) so the
        # minimal stdlib.h overrides the MSVC one.  The clang resource
        # dir is also -I (not -isystem) to match the M7.0B canary config
        # and avoid pulling in the full Windows SDK.
        args = [CLANG, "--target=x86_64-pc-windows-msvc", "-xc", "-std=c11"]
        args += build_defines
        args += [
            f"-I{REPO / '.dsh-control' / 'corpora' / 'harness' / 'stubs'}",
            f"-I{CORPUS / 'include'}",
            f"-I{CORPUS / 'portable' / 'MSVC-MingW'}",
            f"-I{RESOURCE}",
            str(f),
        ]
        entries.append({
            "directory": str(CORPUS),
            "arguments": args,
            "file": str(f),
        })
    return entries


def _index_build_direct(build_cfg: dict, m6_store, world_id: int) -> dict:
    """Index one build directly into the given M6 store. Returns timing stats."""
    from fw_context_mcp.indexer.compile_commands import parse as parse_compile_commands
    from fw_context_mcp.indexer.symbols import extract_all
    from fw_context_mcp.store_v2.m6_direct_materialization import (
        materialize_unit_into_m6,
    )

    build_name = build_cfg["name"]
    entries = _make_compile_commands(build_cfg["defines"])

    # Write a temp compile_commands.json and parse it
    tmpdir = Path(tempfile.mkdtemp(prefix=f"bench_{build_name}_"))
    cc_path = tmpdir / "compile_commands.json"
    cc_path.write_text(json.dumps(entries), encoding="utf-8")
    units = list(parse_compile_commands(cc_path))

    # Register this build in the M6 store's World
    m6_store.register_world(world_id, {build_name})

    total_facts_before = 0
    try:
        proj_before = m6_store.project_build(build_name)
        total_facts_before = sum(len(v) for v in proj_before.values())
    except Exception:
        pass

    t_frontend_total = 0.0
    t_materialize_total = 0.0
    facts_indexed_this_build = 0
    facts_reused_this_build = 0
    facts_observed_this_build = 0

    t0 = time.monotonic()
    for unit in units:
        # Phase 1: frontend parse (libclang)
        t_fe_start = time.monotonic()
        parsed = extract_all(unit, with_refs=True)
        t_fe = time.monotonic() - t_fe_start
        t_frontend_total += t_fe

        facts_observed_this_build += len(parsed.symbols)

        # Phase 2: M6 direct materialization
        t_mat_start = time.monotonic()
        stats = materialize_unit_into_m6(
            store=m6_store, parsed=parsed, project_root=CORPUS,
            build_name=build_name, world_id=world_id,
            config_hash="", context_id="",
        )
        t_mat = time.monotonic() - t_mat_start
        t_materialize_total += t_mat

        facts_indexed_this_build += stats.get("facts_indexed", 0)
        facts_reused_this_build += stats.get("facts_reused", 0)

    total_wall = time.monotonic() - t0

    # Count facts after this build
    total_facts_after = 0
    try:
        proj_after = m6_store.project_build(build_name)
        total_facts_after = sum(len(v) for v in proj_after.values())
    except Exception:
        pass

    shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "build_name": build_name,
        "frontend_time_s": round(t_frontend_total, 4),
        "materialization_time_s": round(t_materialize_total, 4),
        "total_wall_time_s": round(total_wall, 4),
        "facts_observed": facts_observed_this_build,
        "facts_indexed": facts_indexed_this_build,
        "facts_reused": facts_reused_this_build,
        "total_facts_in_build_view": total_facts_after,
    }


def _fresh_m6_store(tmpdir: Path):
    """Create a fresh M6 store in tmpdir."""
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    fwctx = tmpdir / ".fw-context"
    fwctx.mkdir(exist_ok=True)
    # Remove any existing M6 db
    for p in fwctx.glob("index-m6.db*"):
        p.unlink()
    return open_m6_store(tmpdir)


def _m6_db_bytes(tmpdir: Path) -> int:
    m6_path = tmpdir / ".fw-context" / "index-m6.db"
    return m6_path.stat().st_size if m6_path.exists() else 0


def benchmark_reference(num_builds: int) -> list[dict]:
    """REFERENCE: each build gets a FRESH M6 store. No cross-build reuse."""
    results = []
    for i in range(num_builds):
        build_cfg = BUILD_CONFIGS[i]
        tmpdir = Path(tempfile.mkdtemp(prefix=f"ref_{build_cfg['name']}_"))
        store = _fresh_m6_store(tmpdir)
        # Deterministic world_id from build_name (same as production fallback)
        import hashlib
        world_id = (int(hashlib.sha256(
            build_cfg["name"].encode()).hexdigest()[:8], 16) % 1000000) + 1
        stats = _index_build_direct(build_cfg, store, world_id)
        stats["mode"] = "reference"
        stats["storage_bytes"] = _m6_db_bytes(tmpdir)
        results.append(stats)
        shutil.rmtree(tmpdir, ignore_errors=True)
    return results


def benchmark_m6(num_builds: int) -> list[dict]:
    """PRODUCTION M6: all builds into the SAME M6 store. Cross-build reuse."""
    tmpdir = Path(tempfile.mkdtemp(prefix="m6_shared_"))
    store = _fresh_m6_store(tmpdir)
    import hashlib
    world_id = (int(hashlib.sha256(b"benchmark_world").hexdigest()[:8], 16) % 1000000) + 1

    results = []
    for i in range(num_builds):
        build_cfg = BUILD_CONFIGS[i]
        stats = _index_build_direct(build_cfg, store, world_id)
        stats["mode"] = "m6_production"
        stats["storage_bytes"] = _m6_db_bytes(tmpdir)
        results.append(stats)

    # Final cumulative storage
    if results:
        results[-1]["final_storage_bytes"] = _m6_db_bytes(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return results


def main():
    os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = "1"
    os.environ["FW_CONTEXT_M6_LEGACY_CONVERSION"] = "0"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"

    print("=" * 70, flush=True)
    print("  FINAL ECONOMIC CLOSURE BENCHMARK v2 (direct API)", flush=True)
    print("=" * 70, flush=True)

    all_results = {"scales": {}, "repeats": REPEATS, "build_configs": BUILD_CONFIGS,
                   "note": "REPEATS=1 due to frontend parse cost (~90s/TU). Single repeat gives stable central estimate; median = the single value."}

    for scale in SCALES:
        print(f"\n--- Scale: {scale} builds ({REPEATS} repeats) ---", flush=True)
        ref_runs = []
        m6_runs = []
        for rep in range(REPEATS):
            print(f"  Repeat {rep+1}/{REPEATS}...", flush=True)
            ref = benchmark_reference(scale)
            m6 = benchmark_m6(scale)
            ref_runs.append(ref)
            m6_runs.append(m6)
            print(f"    REF cumul: {sum(r['total_wall_time_s'] for r in ref):.3f}s, "
                  f"M6 cumul: {sum(r['total_wall_time_s'] for r in m6):.3f}s", flush=True)

        # Compute medians
        ref_cumul = statistics.median(
            sum(r["total_wall_time_s"] for r in run) for run in ref_runs
        )
        m6_cumul = statistics.median(
            sum(r["total_wall_time_s"] for r in run) for run in m6_runs
        )
        ref_storage = statistics.median(
            sum(r["storage_bytes"] for r in run) for run in ref_runs
        )
        m6_storage = m6_runs[-1][-1].get("final_storage_bytes", 0) if m6_runs[-1] else 0

        # Marginal: last build cost
        ref_marginal = statistics.median(
            run[-1]["total_wall_time_s"] for run in ref_runs
        )
        m6_marginal = statistics.median(
            run[-1]["total_wall_time_s"] for run in m6_runs
        )

        # Frontend share (M6 mode, last build)
        m6_last_fe = statistics.median(
            run[-1]["frontend_time_s"] for run in m6_runs
        )
        m6_last_mat = statistics.median(
            run[-1]["materialization_time_s"] for run in m6_runs
        )

        scale_data = {
            "scale": scale,
            "reference_cumulative_s": round(ref_cumul, 4),
            "m6_cumulative_s": round(m6_cumul, 4),
            "cumulative_reduction_pct": round(
                (1 - m6_cumul / ref_cumul) * 100, 2) if ref_cumul > 0 else 0,
            "reference_marginal_last_build_s": round(ref_marginal, 4),
            "m6_marginal_last_build_s": round(m6_marginal, 4),
            "marginal_reduction_pct": round(
                (1 - m6_marginal / ref_marginal) * 100, 2) if ref_marginal > 0 else 0,
            "m6_last_build_frontend_s": round(m6_last_fe, 4),
            "m6_last_build_materialization_s": round(m6_last_mat, 4),
            "m6_last_build_frontend_share_pct": round(
                m6_last_fe / m6_marginal * 100, 2) if m6_marginal > 0 else 0,
            "reference_storage_bytes": ref_storage,
            "m6_storage_bytes": m6_storage,
            "storage_reduction_pct": round(
                (1 - m6_storage / ref_storage) * 100, 2) if ref_storage > 0 else 0,
            "reference_raw": ref_runs,
            "m6_raw": m6_runs,
        }
        all_results["scales"][str(scale)] = scale_data

        print(f"  REF cumulative: {ref_cumul:.3f}s, marginal: {ref_marginal:.3f}s, storage: {ref_storage}B", flush=True)
        print(f"  M6  cumulative: {m6_cumul:.3f}s, marginal: {m6_marginal:.3f}s, storage: {m6_storage}B", flush=True)
        print(f"  Cumulative reduction: {scale_data['cumulative_reduction_pct']}%", flush=True)
        print(f"  Marginal reduction: {scale_data['marginal_reduction_pct']}%", flush=True)
        print(f"  Storage reduction: {scale_data['storage_reduction_pct']}%", flush=True)
        print(f"  M6 frontend share (last build): {scale_data['m6_last_build_frontend_share_pct']}%", flush=True)

    out_path = REPO / ".dsh-control/evidence/final_economic_addendum/benchmark_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
