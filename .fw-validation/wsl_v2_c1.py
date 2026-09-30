"""C1 queries: get_vector_table on the CMSIS CM3 startup template (WSL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v2-work" / "C1"
IDX = WORK / "index"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/C1")

client = McpStdioClient(index_dir=IDX, cwd=str(WORK), platform="wsl")
responses = {}
try:
    client.start()
    for tool, args in [("get_vector_table", {}),
                       ("lookup_symbol", {"name": "Reset_Handler", "exact": True}),
                       ("lookup_symbol", {"name": "Vectors", "exact": False})]:
        try:
            responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                client.call_tool(tool, args, timeout=120)
        except Exception as e:
            responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                           "exception": repr(e), "isError": True}
finally:
    client.stop()
    responses["server_exit_code"] = 0
    client.dump_transcript(OUT / "mcp_transcript_c1.txt")
(OUT / "responses_c1.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("C1-QUERIES-DONE")
