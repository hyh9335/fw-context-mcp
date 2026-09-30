import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from v1_runner import VENV_PY, kill_daemon  # noqa: E402

W = Path(r"C:\Users\h00969597\fwctx-validation-work\V4\A4_source_delete_add\work")
IDX = Path.home() / "fwctx-validation-work" / "V4" / "A4_both_fresh_index"
if IDX.exists():
    shutil.rmtree(IDX, ignore_errors=True)
IDX.mkdir(parents=True)
cc = json.load(open(W / "compile_commands.json", encoding="utf-8-sig"))
(W / "cc_both.json").write_text(json.dumps(cc, indent=1), encoding="utf-8")
env = {**os.environ, "FW_CONTEXT_INDEX_DIR": str(IDX)}
subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(W), "--quick"],
               capture_output=True, text=True, cwd=str(W), env=env, timeout=120, encoding="utf-8", errors="replace")
r = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(W / "cc_both.json"),
                    "--project", str(W), "--no-analyze", "--no-embeddings"],
                   capture_output=True, text=True, cwd=str(W), env=env, timeout=300, encoding="utf-8", errors="replace")
keep = [ln for ln in (r.stdout + r.stderr).splitlines()
        if any(k in ln for k in ("TUs to index", "skip", "master", "Indexed", "updated,"))]
print("rc:", r.returncode)
print("\n".join(keep[:12]))
kill_daemon(IDX)
