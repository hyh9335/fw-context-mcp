"""F1/F2 queries against the indexed FreeRTOS kernel (WSL)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v2-work" / "F1"
IDX = WORK / "index"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2")
PY = str(Path.home() / "fwctx-venv311" / "bin" / "python")


def query(tag, queries, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    client = McpStdioClient(index_dir=IDX, cwd=str(WORK), platform="wsl")
    responses = {}
    try:
        client.start()
        for tool, args in queries:
            try:
                responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                    client.call_tool(tool, args, timeout=180)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                               "exception": repr(e), "isError": True}
    finally:
        client.stop()
        responses["server_exit_code"] = 0
        client.dump_transcript(out_dir / f"mcp_transcript_{tag}.txt")
    (out_dir / f"responses_{tag}.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{tag}: {len(responses)} responses")


F1_QUERIES = [
    ("get_active_build", {}),
    ("find_hotspots", {"limit": 10}),
    ("find_call_path", {"from_name": "xTimerStart", "to_name": "xQueueGenericSend"}),
    ("find_callees_recursive", {"name": "xQueueGenericSend", "max_depth": 2}),
    ("lookup_symbol", {"name": "xTaskCreate", "exact": True}),
    ("find_call_path", {"from_name": "xQueueGenericSend", "to_name": "vListInsert"}),
]

F2_QUERIES = [
    ("find_indirect_call_sites", {"name": "pxCallbackFunction"}),
    ("find_indirect_targets", {"name": "pxCallbackFunction"}),
    ("find_indirect_call_sites", {"name": "pxTaskCode"}),
    ("find_indirect_targets", {"name": "pxTaskCode"}),
    ("find_call_path", {"from_name": "xTimerCreate", "to_name": "prvInitialiseNewTimer"}),
    ("find_references", {"name": "xTimerCreate"}),
]

query("f1", F1_QUERIES, OUT / "F1")
query("f2", F2_QUERIES, OUT / "F2")
print("F1F2-QUERIES-DONE")
