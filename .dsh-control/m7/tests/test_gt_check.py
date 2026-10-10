"""Quick check: which GT symbols are found in list.c after M6 materialization."""
import sys, tempfile, json, os, shutil
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))
sys.path.insert(0, str(REPO / ".dsh-control" / "corpora" / "harness"))

from fw_context_mcp.indexer.compile_commands import parse as parse_cc
from fw_context_mcp.indexer.symbols import extract_all
from fw_context_mcp.store_v2.m6_query import open_m6_store, lookup_symbol
from fw_context_mcp.store_v2.m6_direct_materialization import materialize_unit_into_m6

CORPUS = REPO / ".dsh-control" / "m6_research" / "corpus" / "FreeRTOS-Kernel"
CLANG = r"C:\Program Files\LLVM\bin\clang.exe"
RESOURCE = r"C:\Program Files\LLVM\lib\clang\22\include"
STUBS = str(REPO / ".dsh-control" / "corpora" / "harness" / "stubs")

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".fw-context").mkdir()
store = open_m6_store(tmpdir)
store.register_world(1, {"BASE"})

for src_file in ["list.c", "queue.c"]:
    f = CORPUS / src_file
    args = [CLANG, "--target=x86_64-pc-windows-msvc", "-xc", "-std=c11",
            f"-I{STUBS}", f"-I{CORPUS / 'include'}",
            f"-I{CORPUS / 'portable' / 'MSVC-MingW'}",
            f"-I{RESOURCE}", str(f)]
    cc = tmpdir / f"cc_{src_file}.json"
    cc.write_text(json.dumps([{"directory": str(CORPUS), "arguments": args, "file": str(f)}]))
    units = list(parse_cc(cc))
    for u in units:
        p = extract_all(u, with_refs=True)
        materialize_unit_into_m6(store=store, parsed=p, project_root=CORPUS,
                                  build_name="BASE", world_id=1,
                                  config_hash="", context_id="")

GT = ["vListInitialise", "vListInitialiseItem", "vListInsert",
      "vListInsertEnd", "uxListRemove",
      "xQueueGenericReset", "xQueueGenericCreate", "vQueueDelete",
      "xQueueGenericSend", "xQueueGenericReceive"]

for sym in GT:
    r = lookup_symbol(store, "BASE", sym, exact=True)
    print(f"{sym}: {len(r)} results")

shutil.rmtree(tmpdir, ignore_errors=True)
