"""R2 queries: product-surface verification of the variant reindex (WSL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v2-work" / "R1" / "riot-src"
IDX = Path.home() / "fwctx-v2-work" / "R1" / "index"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/R2")

client = McpStdioClient(index_dir=IDX, cwd=str(WORK), platform="wsl")
responses = {}
try:
    client.start()
    calls = [
        ("get_active_build", {}),
        ("lookup_symbol", {"name": "saul_reg", "exact": False, "limit": 5}),
        ("lookup_symbol", {"name": "msg_send", "exact": True}),
        ("find_hotspots", {"limit": 3}),
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
    client.dump_transcript(OUT / "mcp_transcript_r2.txt")
(OUT / "responses_r2.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("R2-QUERIES-DONE")
