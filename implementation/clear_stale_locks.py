import re
import subprocess
from pathlib import Path


def pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15).stdout
        return str(pid) in out
    except Exception:
        return True  # fail-safe: keep lock if we cannot tell


base = Path(r"D:\github\fw-context-mcp\implementation\fw_index_home")
removed = kept = 0
for lock in base.rglob("*.lock"):
    try:
        txt = lock.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(\d+)", txt)
        pid = int(m.group(1)) if m else None
    except Exception:
        pid = None
    if pid is None or not pid_alive(pid):
        lock.unlink(missing_ok=True)
        removed += 1
    else:
        kept += 1
print(f"removed: {removed} kept(live): {kept}")
