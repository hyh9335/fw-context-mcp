import subprocess
import sys

sys.path.insert(0, "/mnt/d/github/fw-context-mcp/.fw-validation")
import v1_runner as R

print("VENV_PY:", R.VENV_PY)
r = subprocess.run([R.VENV_PY, "--version"], capture_output=True, text=True)
print("venv python rc:", r.returncode, r.stdout.strip(), r.stderr.strip())
print("PACK exists:", R.PACK.exists(), R.PACK)
print("materialize exists:", (R.PACK / "materialize_compile_commands.py").exists())
