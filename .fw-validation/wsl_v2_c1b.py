"""C1 follow-up: why no vector table? read_file active view + conditional regions."""
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
        ("read_file", {"file_path": "src/startup_ARMCM3.c", "line_numbers": True}),
        ("list_conditional_regions", {"file_path": "src/startup_ARMCM3.c"}),
        ("lookup_symbol", {"name": "NMI_Handler", "exact": True}),
        ("lookup_symbol", {"name": "HardFault_Handler", "exact": True}),
        ("lookup_symbol", {"name": "VECTOR_TABLE", "exact": False}),
        ("search_bodies", {"query": "Reset_Handler"}),
        ("search_content", {"query": "VECTOR_TABLE"}),
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
    client.dump_transcript(OUT / "mcp_transcript_c1b.txt")
(OUT / "responses_c1b.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("C1B-DONE")
