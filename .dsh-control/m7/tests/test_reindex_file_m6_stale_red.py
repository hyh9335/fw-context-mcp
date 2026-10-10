"""RED test (§6, §27 regression policy): reindex_file leaves M6 stale.

Proves the defect: reindex_file_impl does NOT call materialize_unit_into_m6,
so after a single-file reindex, the M6 store is missing the updated facts.

This is the RED state. After the fix, this test becomes GREEN: reindex_file
materializes into M6 directly.
"""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))
sys.path.insert(0, str(REPO / ".dsh-control" / "corpora" / "harness"))


def main():
    print("=" * 60)
    print("  REINDEX_FILE M6-STALE RED TEST")
    print("=" * 60)

    # Use a tiny project: one source file.
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    src_dir = proj_root / "src"
    src_dir.mkdir()
    src = src_dir / "main.c"
    src.write_text("int alpha(int x) { return x; }\nint beta(int x) { return alpha(x); }\n",
                   encoding="utf-8")

    # compile_commands.json (use arguments array for reliable parsing)
    CLANG = r"C:\Program Files\LLVM\bin\clang.exe"
    RESOURCE = r"C:\Program Files\LLVM\lib\clang\22\include"
    cc = proj_root / "compile_commands.json"
    cc.write_text(json.dumps([{
        "directory": str(src_dir),
        "arguments": [CLANG, "--target=x86_64-pc-windows-msvc", "-xc", "-std=c11",
                      f"-isystem{RESOURCE}", str(src)],
        "file": str(src),
    }]), encoding="utf-8")

    # .fw-context/config.toml (required by runner.run)
    fwctx = proj_root / ".fw-context"
    fwctx.mkdir()
    (fwctx / "config.toml").write_text(
        '[project]\nid = "reindex-red-test"\n', encoding="utf-8")

    db_path = proj_root / ".fw-context" / "index.db"

    # Phase 1: full index via runner.run() — should populate M6.
    os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = "1"
    os.environ["FW_CONTEXT_M6_LEGACY_CONVERSION"] = "0"
    os.environ["PYTHONPATH"] = f"{REPO / 'src'};{REPO / '.dsh-control' / 'm6_research'}"

    from fw_context_mcp.indexer.runner import run as index_run
    from fw_context_mcp.store_v2.m6_query import open_m6_store, lookup_symbol

    print("\n[Phase 1] Full index via runner.run() ...")
    index_run(
        compile_commands=cc, db_path=db_path,
        project_root=proj_root, project_name="reindex_red",
        index_refs=True, force=True,
    )
    print("  Full index done.")

    # Check M6 has alpha and beta.
    m6_store = open_m6_store(proj_root)
    results_alpha = lookup_symbol(m6_store, "default", "alpha", exact=True)
    results_beta = lookup_symbol(m6_store, "default", "beta", exact=True)
    print(f"  M6 alpha: {len(results_alpha)} results")
    print(f"  M6 beta: {len(results_beta)} results")
    assert len(results_alpha) > 0, "M6 must have alpha after full index"
    assert len(results_beta) > 0, "M6 must have beta after full index"

    # Phase 2: modify the source — add a new function gamma.
    src.write_text(
        "int alpha(int x) { return x; }\n"
        "int beta(int x) { return alpha(x); }\n"
        "int gamma(int x) { return beta(x) + 1; }\n",
        encoding="utf-8")

    print("\n[Phase 2] Modified source: added gamma()")

    # Phase 3: reindex_file on main.c.
    print("[Phase 3] Calling reindex_file_impl(main.c) ...")
    from fw_context_mcp.mcp.handlers.maintenance import reindex_file_impl
    result = reindex_file_impl(
        file_path=str(src),
        project_root=str(proj_root),
        with_analysis=False,
    )
    print(f"  reindex_file result: {result.get('symbols_updated', result)}")

    # Phase 4: check if M6 now has gamma.
    # Open a fresh M6 store handle (to avoid cache).
    m6_store2 = open_m6_store(proj_root)
    results_gamma = lookup_symbol(m6_store2, "default", "gamma", exact=True)
    print(f"\n[Phase 4] M6 gamma after reindex_file: {len(results_gamma)} results")

    # The RED test: gamma is MISSING from M6 (reindex_file didn't materialize it).
    if len(results_gamma) == 0:
        print("\n  RED CONFIRMED: reindex_file_impl does NOT materialize into M6.")
        print("  gamma() is in the source and was re-parsed, but M6 has no gamma fact.")
        verdict = "RED"
    else:
        print("\n  GREEN: reindex_file_impl DOES materialize into M6 (fix is working).")
        verdict = "GREEN"

    out_path = REPO / ".dsh-control/evidence/final_economic_addendum/reindex_file_red_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "test": "reindex_file_m6_stale_red",
        "verdict": verdict,
        "m6_has_gamma_after_reindex": len(results_gamma) > 0,
        "m6_gamma_count": len(results_gamma),
        "reindex_file_result": result,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> {out_path}")

    shutil.rmtree(tmpdir, ignore_errors=True)

    # Exit 0 in both cases — this is a diagnostic test, not a gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
