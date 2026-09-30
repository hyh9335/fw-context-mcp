import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from v1_runner import VENV_PY, kill_daemon  # noqa: E402

W = Path(r"C:\Users\h00969597\fwctx-validation-work\V4\A4_source_delete_add\work")
cc = json.load(open(W / "compile_commands.json", encoding="utf-8-sig"))
e0 = cc[0]
src_tok = [x for x in e0["arguments"] if str(x).replace("\\", "/").endswith("master.c")][0]


def probe(name, tu2_args):
    idx = Path.home() / "fwctx-validation-work" / "V4" / f"A4_probe_{name}"
    if idx.exists():
        shutil.rmtree(idx, ignore_errors=True)
    idx.mkdir(parents=True)
    e = {"directory": e0["directory"], "file": str(W / "master2.c"),
         "arguments": [str(W / "master2.c") if x == src_tok else x for x in tu2_args]}
    (W / f"cc_{name}.json").write_text(json.dumps([e0, e], indent=1), encoding="utf-8")
    env = {**os.environ, "FW_CONTEXT_INDEX_DIR": str(idx)}
    subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(W), "--quick"],
                   capture_output=True, text=True, cwd=str(W), env=env, timeout=120, encoding="utf-8", errors="replace")
    r = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(W / f"cc_{name}.json"),
                        "--project", str(W), "--no-analyze", "--no-embeddings"],
                       capture_output=True, text=True, cwd=str(W), env=env, timeout=300, encoding="utf-8", errors="replace")
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if "master2" in ln or "updated," in ln]
    print(f"== {name}: rc={r.returncode}")
    print("   " + "\n   ".join(lines[:4]))
    kill_daemon(idx)


base = [x for x in e0["arguments"]]
# P1: TU2 identical flags to TU1 (the failing scenario)
probe("sameflags", base)
# P2: TU2 different -D flag (ROLE_SLAVE) — like the G01 slave pattern
probe("diffflags", [x for x in base if "ROLE_MASTER" not in str(x)] + ["-DROLE_SLAVE=1"])
