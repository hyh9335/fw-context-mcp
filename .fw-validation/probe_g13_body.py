import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import kill_daemon  # noqa: E402

idx = Path.home() / "fwctx-validation-work" / "V1" / "G13" / "n1" / "index_p"
work = Path.home() / "fwctx-validation-work" / "V1" / "G13" / "n1" / "work_p"
kill_daemon(idx)
client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix="g13probe2")
try:
    client.start()
    for tool, args in [("get_source", {"name": "shared_value"}),
                       ("search_bodies", {"query": '"return 7"'}),
                       ("search_content", {"query": "return 7"}),
                       ("search_bodies", {"query": "shared_value"}),
                       ("get_file_map", {"file_path": "shared.h"})]:
        r = client.call_tool(tool, args, timeout=60)
        t = (r.get("text") or "")[:700].replace("\n", " ")
        print(f"== {tool} {json.dumps(args)[:50]}\n   {t}")
finally:
    client.stop()
    kill_daemon(idx)
