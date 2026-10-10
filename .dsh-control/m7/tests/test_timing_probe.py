"""Quick timing probe: single build, direct API, to find the bottleneck."""
from __future__ import annotations
import json, os, sys, time, tempfile, shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))
sys.path.insert(0, str(REPO / ".dsh-control" / "corpora" / "harness"))

CLANG = r"C:\Program Files\LLVM\bin\clang.exe"
RESOURCE = r"C:\Program Files\LLVM\lib\clang\22\include"
CORPUS = REPO / ".dsh-control" / "m6_research" / "corpus" / "FreeRTOS-Kernel"

def main():
    os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = "1"
    os.environ["FW_CONTEXT_M6_LEGACY_CONVERSION"] = "0"

    from fw_context_mcp.indexer.compile_commands import parse as parse_compile_commands
    from fw_context_mcp.indexer.symbols import extract_all
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    from fw_context_mcp.store_v2.m6_direct_materialization import materialize_unit_into_m6

    files = [CORPUS / "list.c", CORPUS / "queue.c"]
    entries = []
    for f in files:
        args = [CLANG, "--target=x86_64-pc-windows-msvc", "-xc", "-std=c11",
                f"-I{REPO / '.dsh-control' / 'corpora' / 'harness' / 'stubs'}",
                f"-I{CORPUS / 'include'}",
                f"-I{CORPUS / 'portable' / 'MSVC-MingW'}",
                f"-I{RESOURCE}", str(f)]
        entries.append({"directory": str(CORPUS), "arguments": args, "file": str(f)})

    tmpdir = Path(tempfile.mkdtemp(prefix="timing_"))
    cc = tmpdir / "compile_commands.json"
    cc.write_text(json.dumps(entries), encoding="utf-8")
    units = list(parse_compile_commands(cc))
    print(f"Units: {len(units)}", flush=True)

    fwctx = tmpdir / ".fw-context"
    fwctx.mkdir()
    store = open_m6_store(tmpdir)
    store.register_world(1, {"BASE"})

    for i, unit in enumerate(units):
        print(f"\n[Unit {i+1}] {unit.file.name}", flush=True)
        t0 = time.monotonic()
        parsed = extract_all(unit, with_refs=True)
        t_fe = time.monotonic() - t0
        print(f"  extract_all: {t_fe:.3f}s, symbols={len(parsed.symbols)}, refs={len(parsed.references)}", flush=True)

        t0 = time.monotonic()
        stats = materialize_unit_into_m6(
            store=store, parsed=parsed, project_root=CORPUS,
            build_name="BASE", world_id=1, config_hash="", context_id="",
        )
        t_mat = time.monotonic() - t0
        print(f"  materialize: {t_mat:.3f}s, indexed={stats.get('facts_indexed')}, reused={stats.get('facts_reused')}", flush=True)

    # Second build (same defines, different name) — should be fast (reuse)
    print("\n[Build 2: B2 — same source, different name]", flush=True)
    store.register_world(1, {"B2"})
    for i, unit in enumerate(units):
        t0 = time.monotonic()
        parsed = extract_all(unit, with_refs=True)
        t_fe = time.monotonic() - t0
        t0 = time.monotonic()
        stats = materialize_unit_into_m6(
            store=store, parsed=parsed, project_root=CORPUS,
            build_name="B2", world_id=1, config_hash="", context_id="",
        )
        t_mat = time.monotonic() - t0
        print(f"  Unit {i+1}: fe={t_fe:.3f}s, mat={t_mat:.3f}s, indexed={stats.get('facts_indexed')}, reused={stats.get('facts_reused')}", flush=True)

    m6_path = tmpdir / ".fw-context" / "index-m6.db"
    print(f"\nM6 DB size: {m6_path.stat().st_size} bytes", flush=True)
    shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()
