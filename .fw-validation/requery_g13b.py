"""G13 evidence requerry v2 — daemon-disciplined.

Phase A: fresh pristine work trees (generate.py) + fresh index dirs; kill the
watcher daemon immediately after indexing; query with corrected probes.
Phase B: requery the EXISTING index_inc / index_mut dirs (they hold the
mutated-tree state; verify modified_files_count == 0 proves convergence).
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import kill_daemon  # noqa: E402

BASE = Path.home() / "fwctx-validation-work" / "V1" / "G13"
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V1\G13\raw")
PY = r"D:\github\fw-context-mcp\.fw-validation\venv-win\Scripts\python.exe"
GEN = r"D:\github\fw-context-mcp\fwctx_validation_benchmark_pack\golden\G13_fanout_scale\generate.py"
SIZES = [1, 10, 50, 100, 250]
ENV_KEY = "FW_CONTEXT_INDEX_DIR"


def run_cli(args, cwd, env):
    return subprocess.run([PY] + args, capture_output=True, text=True, timeout=1800,
                          cwd=str(cwd), env=env, encoding="utf-8", errors="replace")


def query(idx, work, tag, needle):
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=f"g13v2-{tag}")
    responses = {}
    try:
        client.start()
        for tool, args in [
                ("lookup_symbol", {"name": "shared_value", "exact": True, "limit": 100}),
                ("lookup_symbol", {"name": "f_", "exact": False, "limit": 100}),
                ("search_bodies", {"query": needle, "limit": 100}),
                ("get_active_build", {}),
        ]:
            try:
                responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                    client.call_tool(tool, args, timeout=180)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"exception": repr(e), "isError": True}
    finally:
        client.stop()
        responses["server_exit_code"] = 0
        client.dump_transcript(OUT / f"mcp_transcript_{tag}.txt")
    (OUT / f"responses_{tag}.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")


def phase_a(n):
    work = BASE / f"n{n}" / "work_p"
    idx = BASE / f"n{n}" / "index_p"
    for d in (work, idx):
        if d.exists():
            kill_daemon(idx)
            shutil.rmtree(d, ignore_errors=True)
    work.mkdir(parents=True)
    r = subprocess.run([PY, GEN, str(work), str(n)], capture_output=True, text=True,
                       timeout=180, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return f"generate failed: {r.stderr[-300:]}"
    import os
    env = {**dict(os.environ), ENV_KEY: str(idx)}
    r = run_cli(["-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"],
                work, env)
    if r.returncode != 0:
        return f"init failed: {r.stderr[-300:]}"
    r = run_cli(["-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
                 "--project", str(work), "--no-analyze", "--no-embeddings"], work, env)
    if r.returncode != 0:
        return f"index failed: {r.stderr[-300:]}"
    (OUT / f"index_p{n}.log").write_text(f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}",
                                         encoding="utf-8")
    kill_daemon(idx)          # daemon must not observe anything further
    query(idx, work, f"p{n}", "return 7")
    kill_daemon(idx)
    return "ok"


def phase_b(n, idx_name, tag):
    if (OUT / f"responses_{tag}.json").exists():
        return "ok(cached)"
    idx = BASE / f"n{n}" / idx_name
    work = BASE / f"n{n}" / "work"
    if not idx.exists():
        return "no index dir"
    kill_daemon(idx)
    query(idx, work, tag, "return 8")
    kill_daemon(idx)
    return "ok"


def main():
    results = {}
    for n in SIZES:
        results[f"p{n}"] = "ok(cached)" if (OUT / f"responses_p{n}.json").exists() else phase_a(n)
        print(f"p{n}:", results[f"p{n}"], flush=True)
    for n in SIZES:
        results[f"inc{n}"] = phase_b(n, "index_inc", f"inc{n}")
        results[f"mut{n}"] = phase_b(n, "index_mut", f"mut{n}")
        print(f"inc{n}/mut{n}: {results[f'inc{n}']}/{results[f'mut{n}']}", flush=True)
    (OUT / "requery_v2_summary.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
