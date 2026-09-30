"""Real stdio smoke driver for fw-context MCP (validation-only artifact).

Spawns the server as a subprocess, speaks newline-delimited JSON-RPC over
real stdio pipes, waits for every response with a deadline, and records a
full transcript + machine summary. BOM-free by construction.
"""
import json
import os
import subprocess
import sys
import threading
import queue
import time
from pathlib import Path

ART = Path(__file__).resolve().parent
REPO = Path(r"D:\github\fw-context-mcp")
PLATFORM = sys.argv[1] if len(sys.argv) > 1 else "win"

if PLATFORM == "win":
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    cmd = [sys.executable, "-m", "fw_context_mcp.mcp.server"]
elif PLATFORM == "wsl":
    # Running INSIDE WSL: source tree reached via /mnt/d mount; deps from host venv.
    env = dict(os.environ)
    env["PYTHONPATH"] = "/mnt/d/github/fw-context-mcp/src"
    env.setdefault("HOME", "/root")
    cmd = [sys.executable, "-m", "fw_context_mcp.mcp.server"]
else:
    # wsl launch handled by caller wrapper script; kept for symmetry
    cmd = sys.argv[2:]

env.setdefault("PYTHONIOENCODING", "utf-8")

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    cwd=str(ART),
)

lines_q: "queue.Queue[str]" = queue.Queue()


def reader(stream, sink):
    for raw in iter(stream.readline, b""):
        sink.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
    sink.put("__EOF__")


threads = [
    threading.Thread(target=reader, args=(proc.stdout, lines_q), daemon=True),
]
transcript = []
errbuf = []


def err_reader(stream):
    for raw in iter(stream.readline, b""):
        errbuf.append(raw.decode("utf-8", "replace").rstrip("\r\n"))


threads.append(threading.Thread(target=err_reader, args=(proc.stderr,), daemon=True))
for t in threads:
    t.start()


def send(obj):
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    proc.stdin.write(data)
    proc.stdin.flush()
    transcript.append({"dir": "out", "obj": obj, "t": time.time()})


def recv_want(want_id, timeout):
    deadline = time.time() + timeout
    pending = []
    while time.time() < deadline:
        try:
            item = lines_q.get(timeout=0.25)
        except queue.Empty:
            continue
        if item == "__EOF__":
            return {"timeout": True, "pending_before_eof": pending}
        try:
            m = json.loads(item)
        except json.JSONDecodeError:
            pending.append({"unparseable": item[:200]})
            continue
        transcript.append({"dir": "in", "obj": m, "t": time.time()})
        if m.get("id") == want_id:
            return m
        pending.append({k: m.get(k) for k in ("id", "method", "error")})
    return {"timeout": True, "pending_before_eof": pending}


results = {}

# 1) initialize
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "fw-validation-controller", "version": "1.0"}}})
r1 = recv_want(1, 30)
results["initialize"] = (
    {"ok": True,
     "protocolVersion": r1["result"].get("protocolVersion"),
     "serverInfo": r1["result"].get("serverInfo")}
    if isinstance(r1, dict) and "result" in r1 else {"ok": False, "detail": r1}
)

# 2) initialized notification (no response expected)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 3) tools/list
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
r2 = recv_want(2, 60)
results["tools_list"] = (
    {"ok": True, "count": len(r2["result"]["tools"]),
     "names": [t["name"] for t in r2["result"]["tools"]]}
    if isinstance(r2, dict) and "result" in r2 else {"ok": False, "detail": r2}
)

# 4) tools/call list_projects (read-only)
send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "list_projects", "arguments": {}}})
r3 = recv_want(3, 90)
results["tools_call_list_projects"] = (
    {"ok": True, "isError": r3["result"].get("isError", False),
     "content_head": (r3["result"].get("content") or [{}])[0].get("text", "")[:500]}
    if isinstance(r3, dict) and "result" in r3 else {"ok": False, "detail": r3}
)

# close stdin, wait for exit
try:
    proc.stdin.close()
except Exception:
    pass
try:
    proc.wait(timeout=30)
    results["exit_code"] = proc.returncode
except subprocess.TimeoutExpired:
    proc.kill()
    results["exit_code"] = "killed_after_timeout"

results["stderr_tail"] = errbuf[-12:]

(ART / f"stdio_{PLATFORM}_summary.json").write_text(
    json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
(ART / f"stdio_{PLATFORM}_transcript.json").write_text(
    json.dumps(transcript, indent=2, ensure_ascii=False)[:8_000_000], encoding="utf-8")

print(json.dumps({k: v for k, v in results.items() if k != "stderr_tail"}, indent=2)[:4000])
