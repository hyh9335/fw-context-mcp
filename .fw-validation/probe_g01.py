"""Probe G01 product output shapes to design the recipe (evidence-gathering)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import McpStdioClient  # noqa: E402

REPO = Path(r"D:\github\fw-context-mcp")
WORK_ROOT = Path.home() / "fwctx-validation-work" / "V1"
case = "G01"
work = WORK_ROOT / case / "work"
index_dir = WORK_ROOT / case / "index"

client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix="probe")
out = {}
try:
    info = client.start()
    for tool, args in [
        ("group_equivalent_variants", {"symbol": "role_value"}),
        ("group_equivalent_variants", {"symbol": "feature_value"}),
        ("lookup_symbol", {"name": "role_value"}),
        ("lookup_symbol", {"name": "feature_value"}),
        ("get_source", {"name": "role_value"}),
        ("get_symbol_context", {"name": "master_entry"}),
        ("explain_condition", {"file_path": "common.h", "line": 3}),
        ("list_conditional_regions", {"file_path": "common.h"}),
        ("get_active_build", {}),
        ("search_bodies", {"query": "role_value"}),
    ]:
        key = f"{tool} {json.dumps(args)}"
        try:
            r = client.call_tool(tool, args, timeout=60)
            out[key] = {"isError": r["isError"], "text": r["text"][:2500]}
        except Exception as e:
            out[key] = {"exception": repr(e)}
finally:
    client.stop()
    client.dump_transcript(str(Path(__file__).parent / "probe_transcript.txt"))

dest = Path(__file__).parent / "probe_g01_results.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"written {dest}")
for k, v in out.items():
    print("==", k, "== isError:", v.get("isError"))
    print((v.get("text") or v.get("exception", ""))[:700], "\n")
