"""N1 queries: sim:nsh clean index scale + smoke (WSL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v2-work" / "N1"
IDX = Path.home() / "fwctx-v2-work" / "N1" / "index"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N1")

client = McpStdioClient(index_dir=IDX, cwd=str(WORK), platform="wsl")
responses = {}
try:
    client.start()
    calls = [
        ("get_active_build", {}),
        ("lookup_symbol", {"name": "nx_start", "exact": True}),
        ("find_hotspots", {"limit": 5}),
        ("search_code", {"query": "work queue", "limit": 8}),
    ]
    for tool, args in calls:
        try:
            responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                client.call_tool(tool, args, timeout=240)
        except Exception as e:
            responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                           "exception": repr(e), "isError": True}
finally:
    client.stop()
    responses["server_exit_code"] = 0
    client.dump_transcript(OUT / "mcp_transcript_n1.txt")
(OUT / "responses_n1.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("N1-QUERIES-DONE")
