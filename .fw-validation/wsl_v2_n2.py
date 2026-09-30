"""N2 queries: smp variant vs N1 nsh — variant pair facts (WSL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N2")

responses = {}
for label, work, idx in (
    ("n1", Path.home() / "fwctx-v2-work" / "N1", Path.home() / "fwctx-v2-work" / "N1" / "index"),
    ("n2", Path.home() / "fwctx-v2-work" / "N2" / "nuttx-src", Path.home() / "fwctx-v2-work" / "N2" / "index"),
):
    client = McpStdioClient(index_dir=idx, cwd=str(work), platform="wsl")
    try:
        client.start()
        for tool, args in [("get_active_build", {}), ("find_hotspots", {"limit": 3})]:
            try:
                responses[f"{label}:{tool}"] = client.call_tool(tool, args, timeout=240)
            except Exception as e:
                responses[f"{label}:{tool}::ERROR"] = {"exception": repr(e), "isError": True}
    finally:
        client.stop()
        client.dump_transcript(OUT / f"mcp_transcript_n2_{label}.txt")

(OUT / "responses_n2.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("N2-QUERIES-DONE")
