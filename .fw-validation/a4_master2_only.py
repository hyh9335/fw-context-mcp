import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from v1_runner import VENV_PY, kill_daemon  # noqa: E402

W = Path(r"C:\Users\h00969597\fwctx-validation-work\V4\A4_source_delete_add\work")
IDX = Path.home() / "fwctx-validation-work" / "V4" / "A4_master2_only_index"
if IDX.exists():
    shutil.rmtree(IDX, ignore_errors=True)
IDX.mkdir(parents=True)
# CC with ONLY master2.c, exact same shape as master.c's entry
cc = json.load(open(W / "compile_commands.json", encoding="utf-8-sig"))
e0 = cc[0]
e = {"directory": e0["directory"], "file": str(W / "master2.c"),
     "arguments": [x for x in e0["arguments"]]}
e["arguments"][e["arguments"].index("-o") + 1] = "master2.o"
for i, x in enumerate(e["arguments"]):
    if str(x).replace("\\", "/").endswith("master.c"):
        e["arguments"][i] = str(W / "master2.c")
        break
(W / "cc_master2_only.json").write_text(json.dumps([e], indent=1), encoding="utf-8")
env = {"FW_CONTEXT_INDEX_DIR": str(IDX)}
r0 = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(W), "--quick"],
                    capture_output=True, text=True, cwd=str(W), env=env, timeout=120, encoding="utf-8", errors="replace")
r = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(W / "cc_master2_only.json"),
                    "--project", str(W), "--no-analyze", "--no-embeddings"],
                   capture_output=True, text=True, cwd=str(W), env=env, timeout=300, encoding="utf-8", errors="replace")
out = r.stdout + r.stderr
keep = [ln for ln in out.splitlines() if any(k in ln for k in ("TUs to index", "skip", "master2", "Indexed", "config_hash"))]
print("rc:", r.returncode)
print("\n".join(keep[:10]))
kill_daemon(IDX)
