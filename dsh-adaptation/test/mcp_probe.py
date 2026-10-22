#!/usr/bin/env python3
"""Server-side MCP probe for fw-context-mcp (independent of dsh).

Spawns `fw-context-mcp`, performs the JSON-RPC 2.0 handshake, lists tools,
prints the count and names, then exits. Does NOT call a tool, so it never
touches an index (safe for read-only smoke testing).

Usage:
    python dsh-adaptation/test/mcp_probe.py
"""
import json
import os
import subprocess
import sys
import time

def main() -> int:
    command = os.environ.get("FW_CONTEXT_CMD", "fw-context-mcp")
    proc = subprocess.Popen(
        [command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "dsh-compat-probe", "version": "0"},
            },
        })
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        tools = None
        deadline = time.time() + 12
        while time.time() < deadline and tools is None:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == 2:
                tools = msg["result"]["tools"]
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    print("TOOL_COUNT", len(tools) if tools else 0)
    if tools:
        for tool in sorted(t["name"] for t in tools):
            print("  -", tool)

    return 0 if tools else 1

if __name__ == "__main__":
    sys.exit(main())