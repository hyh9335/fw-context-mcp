"""Probe G02: is the command-line macro MODE_VALUE's per-context value visible anywhere?"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import McpStdioClient  # noqa: E402

REPO = Path(r"D:\github\fw-context-mcp")
WORK_ROOT = Path.home() / "fwctx-validation-work" / "V1"
case = "G02"
work = WORK_ROOT / case / "work"
index_dir = WORK_ROOT / case / "index"

client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix="probe2")
out = {}
try:
    client.start()
    for tool, args in [
        ("lookup_symbol", {"name": "MODE_VALUE", "kind": "macro"}),
        ("search_code", {"query": "MODE_VALUE"}),
        ("search_content", {"query": "MODE_VALUE"}),
        ("get_active_build", {}),
        ("list_conditional_regions", {"file_path": "same.c"}),
        ("compare_variants", {"a": "*", "b": "*"}),
    ]:
        key = f"{tool} {json.dumps(args)}"
        try:
            r = client.call_tool(tool, args, timeout=60)
            out[key] = {"isError": r["isError"], "text": r["text"][:1800]}
        except Exception as e:
            out[key] = {"exception": repr(e)}
finally:
    client.stop()

dest = Path(__file__).parent / "probe_g02_results.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"written {dest}")
for k, v in out.items():
    print("==", k, "== isError:", v.get("isError"))
    print((v.get("text") or v.get("exception", ""))[:900], "\n")
