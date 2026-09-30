"""Z3 scenario: workq test app — async/dispatch semantics, no invented edges (WSL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v2-work" / "Z3"
IDX = WORK / "index"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z3")

client = McpStdioClient(index_dir=IDX, cwd=str(WORK), platform="wsl")
responses = {}
try:
    client.start()
    calls = [
        ("get_active_build", {}),
        ("lookup_symbol", {"name": "k_work_init", "exact": True}),
        ("lookup_symbol", {"name": "k_work_submit", "exact": True}),
        ("lookup_symbol", {"name": "work_queue_main", "exact": True}),
        ("find_callers", {"name": "work_queue_main"}),
        ("find_call_path", {"from_name": "k_work_submit", "to_name": "work_queue_main"}),
        ("find_callees_recursive", {"name": "k_work_submit", "max_depth": 3}),
    ]
    for tool, args in calls:
        try:
            responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                client.call_tool(tool, args, timeout=180)
        except Exception as e:
            responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                           "exception": repr(e), "isError": True}
finally:
    client.stop()
    responses["server_exit_code"] = 0
    client.dump_transcript(OUT / "mcp_transcript_z3.txt")
(OUT / "responses_z3.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("Z3-QUERIES-DONE")
