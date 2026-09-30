import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

home = Path.home() / "fwctx-validation-work/V1/G02"
c = McpStdioClient(index_dir=home / "index", cwd=str(home / "work"))
try:
    c.start()
    for tool, args in [("explain_symbol", {"name": "MODE_VALUE"}),
                       ("explain_symbol", {"name": "mode_value"})]:
        try:
            r = c.call_tool(tool, args, timeout=90)
            print("==", tool, args, "isError", r["isError"])
            print(r["text"][:900])
        except Exception as e:
            print("==", tool, args, "EXC", repr(e))
finally:
    c.stop()
