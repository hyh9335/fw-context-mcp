"""AUD-R2 regression — the real-stdio tool path must answer within budget.

History (fw-context closeout, issue AUD-R2, Windows-only): over a REAL stdio
server process, the FIRST build-aware tool call (``get_active_build``) never
answered — 90 s and 300 s deadlines, cold start AND restart — while the same
handler in-process answered sub-second, and WSL answered the whole flow in
~0.5 s.  Forensics (py-spy + GIT_TRACE2, .fw-closeout/artifacts/W2/) found
three stacked defects, each now fixed and each guarded by this test:

1. FastMCP installed a rich ``RichHandler`` bound to the *inherited console*;
   on a console-less harness the legacy-console write path blocked the event
   loop inside ``logging`` so even ``initialize`` never returned.  Fixed in
   ``server.py``: plain-stderr root logging is pre-installed before FastMCP
   constructs, so its ``basicConfig`` no-ops.  Guarded: initialize must
   return (with a spec-correct clientInfo — the historical probe omitted
   ``clientInfo.version`` and got -32602, a red herring).

2. ``SyncQueryExecutor._open_connection`` imports ``sqlite_vec`` lazily on
   the first build-aware tool call — on a worker thread, while the event
   loop, ping thread and AnyIO workers are live.  numpy's import chain loads
   native DLLs via ``LoadLibraryExW`` and deadlocked on the Windows loader
   lock (import from a fresh interpreter: ~0.1 s).  Fixed in ``server.py``:
   the import is prewarmed on the main thread before the loop starts.

3. ``_git_tu_candidates`` spawned ``git`` even for non-repositories, and
   ``subprocess.run``'s Windows timeout path (kill the wrapper + a recovery
   ``communicate()`` that takes NO timeout) never returned when the child's
   image load stalled (measured 23-53 s before main(), while a control spawn
   from the parent took 0.03 s at the same moment).  Fixed in ``stale.py``:
   repository-presence probe before spawning + ``Popen`` tree-kill teardown.

The test drives the REAL wire — it spawns ``python -m fw_context_mcp.mcp.server``
as a subprocess, performs the full handshake, ``tools/list``, then ONE
``get_active_build`` call, COLD and again after a server restart.  The green
budget from the closeout plan: every ``get_active_build`` over real stdio
must answer within 10 s with a truthful ``status``.
"""

from __future__ import annotations

import json
import os
import queue as queue_mod
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import fw_context_mcp

# ── Worktree pinning (anti-drift, same pattern as slice-N) ───────────────────
_WT_SRC = Path(__file__).resolve().parents[1] / "src"
_PKG_FILE = Path(fw_context_mcp.__file__).resolve()
assert _PKG_FILE.is_relative_to(_WT_SRC), (
    "fw_context_mcp resolved OUTSIDE this worktree's src: "
    f"{_PKG_FILE} (expected under {_WT_SRC}). "
    "Run pytest with PYTHONPATH pointing at the worktree src."
)

SCRATCH = Path(os.environ.get("TEMP", str(Path.home()))) / "fw_impl_w2"
SCRATCH.mkdir(parents=True, exist_ok=True)

# The green budget from the closeout plan (AUD-R2): a build-aware tool call
# over REAL stdio must answer within 10 s — cold start AND after restart.
CALL_BUDGET_S = 10.0
# Generous bounds for the handshake steps: a cold start pays interpreter +
# module imports + FastMCP construction + the native prewarm.
HANDSHAKE_BUDGET_S = 30.0

UTIL_H = """#ifndef UTIL_H
#define UTIL_H
int util_add(int a, int b);
int util_mul(int a, int b);
#endif
"""

UTIL_C = """#include "util.h"
int util_add(int a, int b) { return a + b; }
int util_mul(int a, int b) { return a * b; }
"""

MAIN_C = """#include "util.h"
int main(void) {
    return util_add(1, util_mul(2, 3));
}
"""


def _run_cli(cmd: list[str], cwd: Path, log: Path, index_home: Path) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_WT_SRC)
    env["FW_CONTEXT_INDEX_DIR"] = str(index_home)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(cmd)}  (cwd={cwd})\n")
        fh.flush()
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=900,
        )
        fh.write(proc.stdout)
        fh.write("\n--- stderr ---\n")
        fh.write(proc.stderr)
        fh.write(f"\n[exit code: {proc.returncode}]\n")
    return proc.returncode


def _build_indexed_fixture() -> tuple[Path, Path]:
    """Mini C project (no repository — the AUD-R2 repro shape), indexed once.

    Kept repository-less ON PURPOSE: spawning ``git`` from a live stdio
    server was the third AUD-R2 defect; a fixture WITHOUT ``.git`` in itself
    or any parent exercises the skip path (``_git_tu_candidates`` must return
    None without spawning git at all).
    """
    fixture = SCRATCH / "fixture_w2_stdio"
    index_home = SCRATCH / "index_home_w2"
    log = SCRATCH / "fixture_w2_build.log"
    if (fixture / ".fw-context" / "index.done").exists():
        return fixture, index_home
    fixture.mkdir(parents=True, exist_ok=True)
    index_home.mkdir(parents=True, exist_ok=True)
    for fname, text in (("util.h", UTIL_H), ("util.c", UTIL_C), ("main.c", MAIN_C)):
        (fixture / fname).write_text(text, encoding="utf-8")
    cc_dir = fixture / ".fw-context" / "build"
    cc_dir.mkdir(parents=True, exist_ok=True)
    cc = [
        {"directory": str(fixture),
         "command": "clang -std=c11 -I. -c util.c -o util.o", "file": "util.c"},
        {"directory": str(fixture),
         "command": "clang -std=c11 -I. -c main.c -o main.o", "file": "main.c"},
    ]
    (cc_dir / "compile_commands.json").write_text(json.dumps(cc), encoding="utf-8")
    (fixture / ".fw-context" / "config.toml").write_text(
        '[llm]\nenabled = false\n\n[index]\n'
        f'db_dir = "{index_home.as_posix()}"\n',
        encoding="utf-8")
    py = sys.executable
    rc = _run_cli([py, "-m", "fw_context_mcp.cli", "init", "--non-interactive",
                   "--skip-doctor", "--skip-build"], fixture, log, index_home)
    assert rc == 0, f"fixture init failed — see {log}"
    rc = _run_cli([py, "-m", "fw_context_mcp.cli", "index",
                   "--no-analyze", "--no-embeddings"], fixture, log, index_home)
    assert rc == 0, f"fixture index failed — see {log}"
    (fixture / ".fw-context" / "index.done").write_text("ok", encoding="utf-8")
    return fixture, index_home


@pytest.fixture(scope="module")
def indexed_fixture() -> tuple[Path, Path]:
    return _build_indexed_fixture()


# ── Real-stdio protocol helpers ───────────────────────────────────────────────

def _readline_with_timeout(stream, timeout_s: float) -> str:
    q: queue_mod.Queue = queue_mod.Queue()

    def _pump():
        try:
            q.put(stream.readline())
        except Exception:  # pragma: no cover - pump failure
            q.put("")

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout_s)
    except queue_mod.Empty:
        return ""


def _spawn_server(fixture: Path, index_home: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_WT_SRC)
    env["FW_CONTEXT_INDEX_DIR"] = str(index_home)
    env["PYTHONUTF8"] = "1"
    env.pop("GIT_TRACE", None)
    env.pop("GIT_TRACE2_EVENT", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "fw_context_mcp.mcp.server"],
        cwd=str(fixture), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    # Drain stderr (daemon thread): the logging fix keeps the loop thread off
    # the console, but an undrained stderr pipe would eventually fill.
    def _drain():
        try:
            for _line in proc.stderr:  # type: ignore[union-attr]
                pass
        except Exception:  # pragma: no cover
            pass
    threading.Thread(target=_drain, daemon=True).start()
    return proc


def _read_response(proc: subprocess.Popen, want_id: int, timeout_s: float) -> dict:
    """Read wire lines until the response with *want_id* arrives.

    Notifications (no id) and requests may interleave; only responses carry
    an ``id``.  Bounded overall by *timeout_s*.
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        line = _readline_with_timeout(proc.stdout, max(0.1, deadline - time.perf_counter()))
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg
    return {}


def _one_round(proc: subprocess.Popen, fixture: Path) -> None:
    """Full handshake + one get_active_build, each step bounded; asserts."""
    assert proc.stdin is not None and proc.stdout is not None

    t0 = time.perf_counter()
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "w2-regression", "version": "1.0"}}}
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    resp = _read_response(proc, 1, HANDSHAKE_BUDGET_S)
    assert resp, "server produced NO initialize response (AUD-R2 defect 1: " \
        "console-bound logging froze the loop before the first answer)"
    assert "error" not in resp, f"initialize errored on the wire: {resp}"
    name = (resp.get("result") or {}).get("serverInfo", {}).get("name")
    assert name == "fw-context", f"unexpected serverInfo: {resp}"
    init_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    proc.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
    proc.stdin.flush()
    tools = _read_response(proc, 2, HANDSHAKE_BUDGET_S)
    assert tools, "no tools/list response"
    tool_names = [t["name"] for t in tools["result"]["tools"]]
    assert "get_active_build" in tool_names, (
        f"get_active_build missing from tools/list ({len(tool_names)} tools)")
    list_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    proc.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_active_build",
                    "arguments": {"project_root": str(fixture)}}}) + "\n")
    proc.stdin.flush()
    body = _read_response(proc, 3, CALL_BUDGET_S)
    call_s = time.perf_counter() - t0
    assert body, (
        f"get_active_build produced NO response within {CALL_BUDGET_S}s over "
        "real stdio (AUD-R2: loader-lock numpy import / unbounded git spawn)")
    assert "error" not in body, f"tools/call errored on the wire: {body}"
    payload = json.loads(body["result"]["content"][0]["text"])
    assert payload.get("status") == "ready", (
        f"get_active_build status must be truthful 'ready', got: "
        f"{payload.get('status')} (full: {json.dumps(payload)[:300]})")
    assert call_s <= CALL_BUDGET_S, (
        f"get_active_build answered in {call_s:.2f}s — over the "
        f"{CALL_BUDGET_S}s AUD-R2 budget (initialize={init_s:.2f}s, "
        f"tools/list={list_s:.2f}s)")


# ═════════════════════════════ the regression ═════════════════════════════════

def test_stdio_toolpath_within_budget_cold_and_restart(indexed_fixture):
    """AUD-R2: cold start AND restart — get_active_build over REAL stdio
    answers within 10 s with truthful status on both platforms."""
    fixture, index_home = indexed_fixture

    # cold start
    proc = _spawn_server(fixture, index_home)
    try:
        _one_round(proc, fixture)
    finally:
        proc.kill()
        proc.wait(timeout=15)

    # restart (the W0 baseline hung identically on BOTH rounds)
    proc = _spawn_server(fixture, index_home)
    try:
        _one_round(proc, fixture)
    finally:
        proc.kill()
        proc.wait(timeout=15)
