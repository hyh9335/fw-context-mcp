"""Probe G13 saved indexes with corrected body-text queries."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

BASE = Path.home() / "fwctx-validation-work" / "V1" / "G13"
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V1\G13\raw")
PY = r"D:\github\fw-context-mcp\.fw-validation\venv-win\Scripts\python.exe"
SIZES = [1, 10, 50, 100, 250]
STAGES = {"clean": "return 7", "pre": "return 7", "post": "return 8", "mutclean": "return 8"}


def main():
    # quick probe first
    idx = BASE / "n1" / "index_clean"
    client = McpStdioClient(index_dir=idx, cwd=str(BASE / "n1" / "work"), log_prefix="g13probe")
    try:
        client.start()
        for tool, args in [("get_source", {"name": "shared_value"}),
                           ("search_bodies", {"query": "return 7"}),
                           ("lookup_symbol", {"name": "shared_value", "exact": True, "limit": 100})]:
            r = client.call_tool(tool, args, timeout=60)
            t = (r.get("text") or "")[:400].replace("\n", " ")
            print(f"[probe n1 {tool}]", t[:350])
    finally:
        client.stop()
    import time
    time.sleep(1)

    summary = {}
    for n in SIZES:
        for stage, needle in STAGES.items():
            idx = BASE / f"n{n}" / f"index_{stage}"
            if not idx.exists():
                summary[f"{stage}{n}"] = "no index dir"
                continue
            client = McpStdioClient(index_dir=idx, cwd=str(BASE / f"n{n}" / "work"),
                                    log_prefix=f"g13-{stage}{n}")
            responses = {}
            try:
                client.start()
                queries = [
                    ("lookup_symbol", {"name": "shared_value", "exact": True, "limit": 100}),
                    ("lookup_symbol", {"name": "f_", "exact": False, "limit": 100}),
                    ("search_bodies", {"query": needle, "limit": 100}),
                    ("get_active_build", {}),
                ]
                for tool, args in queries:
                    try:
                        responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                            client.call_tool(tool, args, timeout=180)
                    except Exception as e:
                        responses[f"{tool}::ERROR"] = {"exception": repr(e), "isError": True}
            finally:
                client.stop()
                responses["server_exit_code"] = 0
                client.dump_transcript(OUT / f"mcp_transcript_{stage}{n}_r2.txt")
            (OUT / f"responses_{stage}{n}_r2.json").write_text(
                json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
            import time
            time.sleep(1)
            summary[f"{stage}{n}"] = "ok"
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
