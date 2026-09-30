"""Z2 scenario: compare_variants between Z1 (assert off) and Z2 (CONFIG_ASSERT=y) — WSL.

Runs after both indexes complete. Uses the Z1 work dir as the project (variant
labels come from the two index dirs via FW_CONTEXT_INDEX_DIR).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

Z1 = Path.home() / "fwctx-v2-work" / "Z1"
Z2 = Path.home() / "fwctx-v2-work" / "Z2"
OUT = Path("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z2")

responses = {}
for label, work, idx in (("z1", Z1, Z1 / "index"), ("z2", Z2, Z2 / "index")):
    client = McpStdioClient(index_dir=idx, cwd=str(work), platform="wsl")
    try:
        client.start()
        calls = [
            ("get_active_build", {}),
            ("lookup_symbol", {"name": "main", "exact": True}),
            ("lookup_symbol", {"name": "z_impl_printk", "exact": True}),
        ]
        for tool, args in calls:
            try:
                responses[f"{label}:{tool}::{json.dumps(args)[:80]}"] = \
                    client.call_tool(tool, args, timeout=180)
            except Exception as e:
                responses[f"{label}:{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                                       "exception": repr(e), "isError": True}
    finally:
        client.stop()
        client.dump_transcript(OUT / f"mcp_transcript_{label}.txt")

(OUT / "responses_z2_variants.json").write_text(
    json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
print("Z2-QUERIES-DONE")
