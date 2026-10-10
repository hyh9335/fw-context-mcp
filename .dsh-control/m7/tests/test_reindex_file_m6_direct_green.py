"""GREEN regression test (§6, §27): reindex_file materializes into M6 directly.

After the M7-FINAL-ECONOMIC-ADDENDUM fix, reindex_file_impl now calls
materialize_unit_into_m6 when the legacy DB doesn't exist (pure M6 mode).
This test verifies:
1. Full index via runner.run() populates M6.
2. Source file modification + reindex_file updates M6 with new facts.
3. The new function (gamma) appears in M6 after reindex_file.

This is a PERMANENT regression test (§6: "Add a permanent regression test").
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))
sys.path.insert(0, str(REPO / ".dsh-control" / "corpora" / "harness"))


def main():
    print("=" * 60)
    print("  REINDEX_FILE M6-DIRECT GREEN TEST")
    print("=" * 60)

    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    src_dir = proj_root / "src"
    src_dir.mkdir()
    src = src_dir / "main.c"
    src.write_text("int alpha(int x) { return x; }\nint beta(int x) { return alpha(x); }\n",
                   encoding="utf-8")

    CLANG = r"C:\Program Files\LLVM\bin\clang.exe"
    RESOURCE = r"C:\Program Files\LLVM\lib\clang\22\include"
    cc = proj_root / "compile_commands.json"
    cc.write_text(json.dumps([{
        "directory": str(src_dir),
        "arguments": [CLANG, "--target=x86_64-pc-windows-msvc", "-xc", "-std=c11",
                      f"-isystem{RESOURCE}", str(src)],
        "file": str(src),
    }]), encoding="utf-8")

    fwctx = proj_root / ".fw-context"
    fwctx.mkdir()
    (fwctx / "config.toml").write_text(
        '[project]\nid = "reindex-green-test"\n', encoding="utf-8")

    db_path = proj_root / ".fw-context" / "index.db"

    os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = "1"
    os.environ["FW_CONTEXT_M6_LEGACY_CONVERSION"] = "0"
    os.environ["PYTHONPATH"] = f"{REPO / 'src'};{REPO / '.dsh-control' / 'm6_research'}"

    from fw_context_mcp.indexer.runner import run as index_run
    from fw_context_mcp.store_v2.m6_query import open_m6_store, lookup_symbol

    print("\n[Phase 1] Full index via runner.run() ...")
    index_run(compile_commands=cc, db_path=db_path,
              project_root=proj_root, project_name="reindex_green",
              index_refs=True, force=True)

    m6_store = open_m6_store(proj_root)
    results_alpha = lookup_symbol(m6_store, "default", "alpha", exact=True)
    results_beta = lookup_symbol(m6_store, "default", "beta", exact=True)
    assert len(results_alpha) > 0, "M6 must have alpha after full index"
    assert len(results_beta) > 0, "M6 must have beta after full index"
    print(f"  M6 alpha: {len(results_alpha)}, beta: {len(results_beta)}")

    # Modify source — add gamma
    src.write_text(
        "int alpha(int x) { return x; }\n"
        "int beta(int x) { return alpha(x); }\n"
        "int gamma(int x) { return beta(x) + 1; }\n",
        encoding="utf-8")
    print("[Phase 2] Added gamma() to source")

    print("[Phase 3] reindex_file_impl(main.c) ...")
    from fw_context_mcp.mcp.handlers.maintenance import reindex_file_impl
    result = reindex_file_impl(file_path=str(src), project_root=str(proj_root),
                                with_analysis=False)
    print(f"  result: {result}")

    m6_store2 = open_m6_store(proj_root)
    results_gamma = lookup_symbol(m6_store2, "default", "gamma", exact=True)
    print(f"  M6 gamma after reindex: {len(results_gamma)} results")

    assert len(results_gamma) > 0, "M6 must have gamma after reindex_file (M6-direct fix)"

    print("\n  GREEN: reindex_file materializes into M6 directly.")
    verdict = "PASS"

    out_path = REPO / ".dsh-control/evidence/final_economic_addendum/reindex_file_green_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "test": "reindex_file_m6_direct_green",
        "verdict": verdict,
        "m6_has_gamma_after_reindex": len(results_gamma) > 0,
        "m6_gamma_count": len(results_gamma),
    }, indent=2, default=str), encoding="utf-8")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"  -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
