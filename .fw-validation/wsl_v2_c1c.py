"""C1 final probe: exact __VECTOR_TABLE lookup + array body + SP slot references."""
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
    calls = [
        ("lookup_symbol", {"name": "__VECTOR_TABLE", "exact": True}),
        ("search_bodies", {"query": '"Initial Stack Pointer"'}),
        ("search_bodies", {"query": "Interrupt9_Handler"}),
        ("find_references", {"name": "__INITIAL_SP"}),
        ("get_vector_table", {}),
    ]
    for tool, args in calls:
        try:
            responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                client.call_tool(tool, args, timeout=120)
        except Exception as e:
            responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                           "exception": repr(e), "isError": True}
finally:
    client.stop()
    responses["server_exit_code"] = 0
    client.dump_transcript(OUT / "mcp_transcript_c1c.txt")
(OUT / "responses_c1c.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("C1C-DONE")
