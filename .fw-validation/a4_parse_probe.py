import json
import subprocess
from pathlib import Path

W = r"C:\Users\h00969597\fwctx-validation-work\V4\A4_source_delete_add\work"
cc = json.load(open(W + r"\compile_commands.json", encoding="utf-8-sig"))
e = cc[1]
print("entry2 args:", e["arguments"])
r = subprocess.run(e["arguments"], capture_output=True, text=True, cwd=W, encoding="utf-8", errors="replace")
print("rc:", r.returncode)
print("stdout:", r.stdout[:300])
print("stderr:", r.stderr[:300])
print("--- entry1 for comparison:", cc[0]["arguments"])
