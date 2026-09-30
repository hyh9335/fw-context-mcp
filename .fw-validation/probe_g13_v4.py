"""G13 value probes v4 — exact-phrase search_content."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import kill_daemon  # noqa: E402

BASE = Path.home() / "fwctx-validation-work" / "V1" / "G13"
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V1\G13\raw")
SIZES = [1, 10, 50, 100, 250]


def probe(idx, work, tag, queries):
    kill_daemon(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=f"g13v4-{tag}")
    responses = {}
    try:
        client.start()
        for tool, args in queries:
            try:
                responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                    client.call_tool(tool, args, timeout=120)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"exception": repr(e), "isError": True}
    finally:
        client.stop()
        responses["server_exit_code"] = 0
    (OUT / f"responses_{tag}.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")


for n in SIZES:
    probe(BASE / f"n{n}" / "index_p", BASE / f"n{n}" / "work_p", f"vp{n}",
          [("search_content", {"query": '"return 7"', "limit": 100}),
           ("search_content", {"query": '"return 8"', "limit": 100})])
    for stage, dirname in [("vinc", "index_inc"), ("vmut", "index_mut")]:
        probe(BASE / f"n{n}" / dirname, BASE / f"n{n}" / "work", f"{stage}{n}",
              [("search_content", {"query": '"return 8"', "limit": 100}),
               ("search_content", {"query": '"return 7"', "limit": 100})])
    print(f"v4 probes done for n={n}", flush=True)
print("ALL DONE")
