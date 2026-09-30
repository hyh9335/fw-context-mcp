"""Reusable MCP stdio client for validation runs.

Spawns the fw-context MCP server as a subprocess over real stdio pipes,
performs the initialize handshake, and issues tools/call requests.
All raw traffic is persisted so every case has command + exit code +
raw artifact evidence.

Windows: use .fw-validation/venv-win/Scripts/python.exe (metadata 0.31.0).
WSL:     run under ~/fwctx-venv311/bin/python with PYTHONPATH=/mnt/d/.../src.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"D:\github\fw-context-mcp")


class McpStdioClient:
    def __init__(self, index_dir, cwd, platform="win", log_prefix="", server_timeout=60.0):
        self.platform = platform
        self.index_dir = str(index_dir)
        self.cwd = str(cwd)
        self.timeout = server_timeout
        self.log_prefix = log_prefix
        if platform == "win":
            self.python = str(REPO / ".fw-validation" / "venv-win" / "Scripts" / "python.exe")
            env_pythonpath = str(REPO / "src")
        else:
            self.python = sys.executable  # resolved inside WSL by caller wrapper
            env_pythonpath = "/mnt/d/github/fw-context-mcp/src"
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = env_pythonpath
        self.env["FW_CONTEXT_INDEX_DIR"] = self.index_dir
        self.env.pop("FW_CONTEXT_DEBUG", None)
        self.proc = None
        self.request_id = 0
        self.transcript = []
        self.server_info = None

    def start(self):
        cmd = [self.python, "-m", "fw_context_mcp.mcp.server"]
        self.proc = subprocess.Popen(
            cmd, cwd=self.cwd, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        resp = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "fwctx-validation", "version": "1.0.0"},
        }, timeout=self.timeout)
        if resp is None or "error" in (resp or {}):
            raise RuntimeError(f"initialize failed: {resp}")
        self.server_info = resp.get("result", {}).get("serverInfo")
        self.notify("notifications/initialized", {})
        return self.server_info

    def _send(self, obj):
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self.transcript.append((">>", line))
        self.proc.stdin.write(line.encode("utf-8"))
        self.proc.stdin.flush()

    def _recv(self, want_id, deadline):
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            self.transcript.append(("<<", text))
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue  # non-JSON noise; keep waiting
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(f"no response for id={want_id} within deadline")

    def request(self, method, params, timeout=None):
        self.request_id += 1
        rid = self.request_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + (timeout or self.timeout)
        return self._recv(rid, deadline)

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call_tool(self, name, arguments, timeout=None):
        t0 = time.time()
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        elapsed = time.time() - t0
        out = {
            "tool": name,
            "arguments": arguments,
            "elapsed_s": round(elapsed, 3),
            "isError": resp.get("result", {}).get("isError", False),
            "raw_response": resp,
        }
        content = resp.get("result", {}).get("content") or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        out["text"] = "\n".join(texts)
        try:
            out["json"] = json.loads(out["text"])
        except (json.JSONDecodeError, ValueError):
            out["json"] = None
        return out

    def dump_transcript(self, path):
        Path(path).write_text(
            "\n".join(f"{d} {t}" for d, t in self.transcript), encoding="utf-8"
        )

    def stop(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            rc = self.proc.returncode
            self.proc = None
            return rc
        return None
