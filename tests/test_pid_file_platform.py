"""Platform-split liveness tests for ``PidFile._pid_exists`` (T00 preflight).

Covers the frozen unlock-list core (T00_ACCEPTED_DESIGN.md DECISION 2/5):

- U1a/U2 — win32 probe liveness cross-checked against an in-test
  ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``GetExitCodeProcess``
  reference for a live child, a freshly exited child, and a never-existing
  pid, with a never-signals witness: on win32 nothing in this module may
  reach ``os.kill`` (signal 0 routes to ``GenerateConsoleCtrlEvent`` and
  DELIVERS CTRL_C to the target's console process group).
- U3/F00-D — conservative win32 error mapping via a stubbed kernel32
  (``platform.windows._kernel32``): access-denied -> True, no-such-pid
  (err 87) -> False, other OpenProcess error -> False (accepted spec),
  ``GetExitCodeProcess`` failure -> True, handle closed exactly once.
- U4/U8 — POSIX parity: the POSIX backend keeps the exact three-outcome
  ``os.kill(pid, 0)`` semantics (success -> True, ESRCH -> False,
  EPERM -> True, other OSError -> False).  Runs only where POSIX is the
  REAL platform — W1 removed the os.name re-branching this file used to
  force, and faking os.name breaks pathlib dispatch under pytest.
- U2(i)/U7/F00-E — static gates: no signal-zero liveness call in
  ``cli/_watch.py`` (probe rerouted through ``PidFile._pid_exists``), no
  ``os.kill`` left in ``pid_file.py`` at all (liveness delegated to the
  platform facade), no console-event / tree-kill API outside
  ``platform/windows.py`` (where TerminateProcess sits behind the D2
  start-time identity check), no ctypes import in ``pid_file.py``.

SAFETY PROTOCOL (F00-A): an autouse witness monkeypatches ``os.kill`` for
this whole module on win32, so even against a regressed tree a probe can
only fail with an AssertionError — it can never deliver a console event.
Sacrificial children are spawned by the test process and terminated only
through the test's own ``Popen`` handles.
"""
from __future__ import annotations

import ast
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import fw_context_mcp.mcp.shared.pid_file as pid_file_module
from fw_context_mcp.mcp.shared.pid_file import PidFile

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "fw_context_mcp"

PQLI = 0x1000            # PROCESS_QUERY_LIMITED_INFORMATION
STILL_ACTIVE = 259
NEVER_PID = 99999998     # canonical never-existing pid (attempt2 evidence S9)
SYNTHETIC_PID = 4194303  # odd -> can never name a real Windows pid (pids are multiples of 4)


# ── Never-signals witness (U2 ii) ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_console_events(monkeypatch):
    """On win32, nothing in this module may reach ``os.kill``.

    Against the unfixed tree this converts the destructive
    ``os.kill(pid, 0)`` probe into a safe AssertionError instead of a real
    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, ...)`` delivery; against the
    patched tree it silently proves the win32 branch never signals.
    """
    if os.name == "nt":
        def _witness(pid, sig, _real=os.kill):
            raise AssertionError(
                f"os.kill({pid!r}, {sig!r}) reached from a liveness path on win32 — "
                "signal 0 routes to GenerateConsoleCtrlEvent(CTRL_C_EVENT, ...) and "
                "delivers CTRL_C to the target's console process group")

        monkeypatch.setattr(os, "kill", _witness)
    yield


# ── OpenProcess reference oracle (U1 cross-check) ────────────────────────────

def _reference_alive(pid: int) -> bool:
    """In-test ``OpenProcess(PQLI)``+``GetExitCodeProcess`` reference."""
    if os.name != "nt":
        raise RuntimeError("reference oracle is win32-only")
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    handle = k32.OpenProcess(PQLI, False, pid)
    if not handle:
        return False  # process object gone — as dead as it gets
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # unqueryable -> conservative alive (mirrors accepted mapping)
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _spawn_ready_child() -> subprocess.Popen:
    """Console-sharing sacrificial child (design U1: flags=0), owned handle."""
    return subprocess.Popen(
        [sys.executable, "-c", "print('READY', flush=True); import time; time.sleep(30)"],
        stdout=subprocess.PIPE, text=True,
    )


def _terminate_child(proc: subprocess.Popen) -> None:
    """Terminate a sacrificial child via the test's OWN handle — never a console event."""
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


# ── U1a/U2 — win32 liveness vs OpenProcess reference, never signals ─────────

@pytest.mark.skipif(os.name != "nt", reason="win32 liveness contract (U1/U2)")
class TestWin32Liveness:
    def test_live_child_reads_alive_and_survives_the_probe(self):
        """F00-A core oracle: the probe must not change the target's state."""
        proc = _spawn_ready_child()
        try:
            assert proc.stdout.readline().strip() == "READY"
            pid = proc.pid
            assert _reference_alive(pid) is True, "reference must agree the child is alive"
            assert PidFile._pid_exists(pid) is True
            assert proc.poll() is None, "liveness probe must not terminate the target"
        finally:
            if proc.stdout:
                proc.stdout.close()
            _terminate_child(proc)

    def test_freshly_exited_child_reads_dead(self):
        """U2/F00-C: a freshly exited pid must not read alive (win32 fix for S5)."""
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait(timeout=10)
        pid = proc.pid
        deadline = time.monotonic() + 10
        while _reference_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _reference_alive(pid) is False, "reference must confirm the child is gone"
        assert PidFile._pid_exists(pid) is False

    def test_never_existing_pid_reads_dead(self):
        assert _reference_alive(NEVER_PID) is False
        assert PidFile._pid_exists(NEVER_PID) is False

    def test_is_active_cleans_stale_marker_of_exited_child(self, tmp_path):
        """U5/F00-C through the public seam: stale marker unlinked, not kept."""
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait(timeout=10)
        marker = tmp_path / "reindex.pause"
        marker.write_text(str(proc.pid), encoding="utf-8")
        assert PidFile.is_active(marker) is False
        assert not marker.exists(), "stale marker must be unlinked once the pid reads dead"


# ── U3/F00-D — conservative win32 error mapping via stubbed _kernel32 ────────

def _install_kernel32_stub(monkeypatch, *, open_ret, last_error, exit_ok=None, exit_code=None):
    """Replace the backend's ``_kernel32`` and ``ctypes.get_last_error`` with a script."""
    from fw_context_mcp.platform import windows as windows_backend

    calls = {"open": [], "exit": [], "close": []}

    class _StubKernel32:
        @staticmethod
        def OpenProcess(access, inherit, pid):
            calls["open"].append((access, inherit, pid))
            return open_ret

        @staticmethod
        def GetExitCodeProcess(handle, code_ref):
            calls["exit"].append(handle)
            if exit_ok:
                code_ref._obj.value = exit_code
            return bool(exit_ok)

        @staticmethod
        def CloseHandle(handle):
            calls["close"].append(handle)
            return 1

    monkeypatch.setattr(windows_backend, "_kernel32", _StubKernel32(), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error)
    return calls


@pytest.mark.skipif(os.name != "nt", reason="win32 error mapping (U3/F00-D)")
class TestWin32ErrorMapping:
    def test_openprocess_null_err_87_reads_dead(self, monkeypatch):
        calls = _install_kernel32_stub(monkeypatch, open_ret=0, last_error=87)
        assert PidFile._pid_exists(SYNTHETIC_PID) is False
        assert calls["open"] == [(PQLI, False, SYNTHETIC_PID)]
        assert calls["close"] == [], "a NULL handle must not be closed"

    def test_openprocess_access_denied_reads_alive(self, monkeypatch):
        _install_kernel32_stub(monkeypatch, open_ret=0, last_error=5)
        assert PidFile._pid_exists(SYNTHETIC_PID) is True, \
            "access denied must map conservative-True (F00-D): false would permit unsafe cleanup"

    def test_openprocess_other_error_reads_dead(self, monkeypatch):
        _install_kernel32_stub(monkeypatch, open_ret=0, last_error=999)
        assert PidFile._pid_exists(SYNTHETIC_PID) is False

    def test_still_active_exit_code_reads_alive_and_handle_closes_once(self, monkeypatch):
        calls = _install_kernel32_stub(monkeypatch, open_ret=0xDEAD, last_error=0,
                                       exit_ok=True, exit_code=STILL_ACTIVE)
        assert PidFile._pid_exists(SYNTHETIC_PID) is True
        assert calls["close"] == [0xDEAD], "handle must be closed exactly once"

    def test_terminated_exit_code_reads_dead(self, monkeypatch):
        calls = _install_kernel32_stub(monkeypatch, open_ret=0xDEAD, last_error=0,
                                       exit_ok=True, exit_code=0)
        assert PidFile._pid_exists(SYNTHETIC_PID) is False
        assert calls["close"] == [0xDEAD], "handle must be closed exactly once"

    def test_exit_code_query_failure_reads_alive_conservatively(self, monkeypatch):
        calls = _install_kernel32_stub(monkeypatch, open_ret=0xDEAD, last_error=0,
                                       exit_ok=False)
        assert PidFile._pid_exists(SYNTHETIC_PID) is True, \
            "GetExitCodeProcess failure must not read dead"
        assert calls["close"] == [0xDEAD], "handle must be closed exactly once"


# ── U4/U8 — POSIX parity ─────────────────────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="real-POSIX three-outcome semantics")
class TestPosixParity:
    """The POSIX backend preserves the exact three-outcome kill(pid, 0) semantics.

    W1 moved the branch into ``platform/posix.py``; faking ``os.name`` on
    Windows cannot exercise it (pathlib dispatch breaks), so parity runs on
    a real POSIX host (WSL gate) only.
    """

    def test_success_reads_alive(self, monkeypatch):
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        from fw_context_mcp.platform import process as platform_process

        assert platform_process.is_alive(SYNTHETIC_PID) is True

    def test_esrch_reads_dead(self, monkeypatch):
        def _esrch(pid, sig):
            raise ProcessLookupError(3, "No such process")

        monkeypatch.setattr(os, "kill", _esrch)
        from fw_context_mcp.platform import process as platform_process

        assert platform_process.is_alive(SYNTHETIC_PID) is False

    def test_eperm_reads_alive(self, monkeypatch):
        def _eperm(pid, sig):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "kill", _eperm)
        from fw_context_mcp.platform import process as platform_process

        assert platform_process.is_alive(SYNTHETIC_PID) is True

    def test_other_oserror_reads_dead(self, monkeypatch):
        def _einval(pid, sig):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(os, "kill", _einval)
        from fw_context_mcp.platform import process as platform_process

        assert platform_process.is_alive(SYNTHETIC_PID) is False


# ── U2(i)/U7/F00-E — static kill-safety gates ────────────────────────────────

def _sig0_kill_lines(path: Path) -> list[int]:
    """Line numbers of ``<name>.kill(pid, 0)`` calls — the destructive liveness shape."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "kill"
                and isinstance(node.func.value, ast.Name)
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == 0):
            hits.append(node.lineno)
    return hits


def _is_os_name_guard(stmt: ast.stmt, *, equals: bool) -> bool:
    """True when *stmt* is ``if os.name == "nt":`` (equals) / ``!= "nt"`` (not equals)."""
    if not (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare)):
        return False
    test = stmt.test
    op = test.ops[0] if test.ops else None
    want = ast.Eq if equals else ast.NotEq
    return (isinstance(test.left, ast.Attribute) and test.left.attr == "name"
            and isinstance(test.left.value, ast.Name) and test.left.value.id == "os"
            and isinstance(op, want)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "nt")


def _banned_kill_api_uses(path: Path) -> list[str]:
    """Code (not prose) uses of console-event / tree-kill / TerminateProcess APIs.

    AST-based so the docstrings that document WHY these APIs are avoided do
    not trip the gate: only Call positions (attribute or plain name) and
    command-line string constants count.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned_calls = {"GenerateConsoleCtrlEvent", "TerminateProcess", "killpg"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in banned_calls:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} .{func.attr}()")
            elif isinstance(func, ast.Name) and func.id in banned_calls:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {func.id}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and "taskkill" in node.value.lower():
            hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} 'taskkill' string")
    return hits


class TestStaticKillSafety:
    def test_watch_py_has_no_signal_zero_liveness_kill(self):
        hits = _sig0_kill_lines(SRC / "cli" / "_watch.py")
        assert hits == [], f"signal-0 os.kill liveness probe remains in _watch.py at lines {hits} (F00-E)"

    def test_watch_py_liveness_probe_routed_through_pidfile(self):
        """W1 form of the D3 adjudication: the restart wait-loop no longer
        hand-rolls a liveness poll at all — it delegates the whole
        terminate-and-wait to ``ProcessService.terminate``.  What must
        NEVER come back is a manual ``os.kill``/``signal`` wait-loop."""
        tree = ast.parse(
            (SRC / "cli" / "_watch.py").read_text(encoding="utf-8")
        )
        kill_calls = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "kill"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "os"
        ]
        assert kill_calls == [], (
            "cli/_watch.py must not call os.kill "
            f"(ProcessService.terminate owns killing) — found at {kill_calls}"
        )
        signal_imports = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            and any(getattr(a, "name", "") == "signal" or getattr(n, "module", "") == "signal"
                    for a in getattr(n, "names", []))
        ]
        assert signal_imports == [], (
            "cli/_watch.py must not import signal (win32 has no SIGKILL) "
            f"— found at {signal_imports}"
        )

    def test_pid_file_delegates_liveness_to_platform_facade(self):
        """U2(i)/U7(ii), W1 form: pid_file.py holds NO os.kill and NO os.name
        branch — liveness lives behind the platform facade; the POSIX
        backend carries the three-outcome os.kill(pid, 0)."""
        pid_file_tree = ast.parse(
            (SRC / "mcp" / "shared" / "pid_file.py").read_text(encoding="utf-8")
        )
        kill_calls = [
            n for n in ast.walk(pid_file_tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "kill"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "os"
        ]
        assert kill_calls == [], (
            f"os.kill must live in the platform backend, found in pid_file.py at "
            f"{[n.lineno for n in kill_calls]}"
        )
        posix_tree = ast.parse(
            (SRC / "platform" / "posix.py").read_text(encoding="utf-8")
        )
        posix_sig0 = [
            n for n in ast.walk(posix_tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "kill"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "os"
            and len(n.args) >= 2
            and isinstance(n.args[1], ast.Constant)
            and n.args[1].value == 0
        ]
        assert posix_sig0, "POSIX backend must retain the os.kill(pid, 0) existence check"

    def test_no_console_event_or_tree_kill_api_in_src(self):
        """U7(i)/(iii): no console-event delivery, killpg, taskkill, or
        TerminateProcess CODE outside platform/windows.py — the win32
        backend's TerminateProcess is the D2 identity-checked kill."""
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            if path.parent.name == "platform" and path.name == "windows.py":
                continue  # sanctioned: identity-checked TerminateProcess (ADR D2)
            offenders.extend(_banned_kill_api_uses(path))
        assert offenders == []

    def test_win32_ctypes_bindings_platform_guarded(self):
        """U8, W1 form: pid_file.py imports NO ctypes at all — the win32
        bindings live in platform/windows.py."""
        tree = ast.parse(
            (SRC / "mcp" / "shared" / "pid_file.py").read_text(encoding="utf-8")
        )

        def imports_ctypes(stmt: ast.stmt) -> bool:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Import) and any(
                        a.name.split(".")[0] == "ctypes" for a in n.names):
                    return True
                if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "ctypes":
                    return True
            return False

        unguarded = [f"line {n.lineno}" for n in tree.body if imports_ctypes(n)]
        assert unguarded == [], (
            f"ctypes must stay in the platform backend, found in pid_file.py at {unguarded}"
        )


# ═══════════════════ AUD-R2 tree-kill service (NEW-W2-3) ══════════════════════
#
# The AUD-R2 fix needs a SANCTIONED tree teardown for timed-out helper
# children: ``platform.process.tree_kill(pid)``.  The service lives in the
# platform backends (win32: Toolhelp32 snapshot + TerminateProcess inside
# the exempt windows.py; POSIX: /proc descendant walk + SIGKILL — no
# ``killpg``, which stays outside the sanctioned surface).  These tests
# spawn only SACRIFICIAL OWNED processes (sleepers the test itself started)
# and always end them in a ``finally`` through the test's own handles.

_CHILD_SPAWNS_SLEEPER = """\
import subprocess, sys, time
proc = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
)
open(sys.argv[1], "w").write(str(proc.pid))
time.sleep(60)
"""


def _wait_until(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class TestTreeKill:
    """NEW-W2-1/3: the sanctioned tree-kill really kills the WHOLE tree."""

    def test_kills_child_and_grandchild(self, tmp_path):
        from fw_context_mcp import platform

        marker = tmp_path / "grandchild.pid"
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_SPAWNS_SLEEPER, str(marker)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        try:
            assert _wait_until(lambda: marker.exists(), 15.0), (
                "sacrificial child never reported its grandchild")
            grandchild_pid = int(marker.read_text().strip())
            assert platform.process.is_alive(child.pid)
            assert platform.process.is_alive(grandchild_pid)

            assert platform.process.tree_kill(child.pid) is True

            assert _wait_until(
                lambda: not platform.process.is_alive(child.pid), 10.0), (
                "the direct child survived tree_kill")
            if Path("/proc").exists():
                # Linux: the /proc descendant walk must reach the grandchild
                # (the AUD-R2 orphan scenario: a wrapper spawns the real
                # worker, killing only the direct child leaves it alive).
                # macOS has no /proc — direct-child-only is the documented
                # fallback in platform/posix.py::tree_kill.
                assert _wait_until(
                    lambda: not platform.process.is_alive(grandchild_pid), 10.0), (
                    "the grandchild survived tree_kill — orphan scenario AUD-R2 "
                    "guards against")
        finally:
            try:
                child.kill()
                child.wait(timeout=5)
            except Exception:  # pragma: no cover — safety net only
                pass

    def test_kills_an_already_dead_pid_cleanly(self):
        """A teardown raced by the child's own exit must not raise."""
        from fw_context_mcp import platform

        sleeper = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        try:
            sleeper.wait(timeout=15)
            # process object fully reaped by the test — pid may even be reused;
            # tree_kill must return without raising (True when gone/terminated).
            assert platform.process.tree_kill(sleeper.pid) in (True, False)
        finally:  # pragma: no cover — safety net only
            try:
                sleeper.kill()
            except Exception:
                pass

    def test_git_probe_timeout_routes_through_facade(self, tmp_path, monkeypatch):
        """The _git_tu_candidates TimeoutExpired branch tears the child tree
        down through the platform facade: a fake-stuck git under a tiny
        timeout ends dead, and the probe returns None (the walk answers)."""
        import fw_context_mcp.mcp.shared.stale as stale_module
        from fw_context_mcp import platform

        stuck = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        try:
            def stuck_communicate(timeout=None):
                time.sleep(0.3)
                raise subprocess.TimeoutExpired(cmd="git ls-files", timeout=timeout)

            monkeypatch.setattr(stuck, "communicate", stuck_communicate)
            # stale._git_tu_candidates imports subprocess inside the function,
            # so patching the module attribute redirects its spawn to `stuck`.
            monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: stuck)
            monkeypatch.setattr(stale_module, "_GIT_LS_TIMEOUT_S", 0.1)

            repo = tmp_path / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()  # repository-presence probe passes

            assert stale_module._git_tu_candidates(repo, frozenset({".c"})) is None

            assert _wait_until(
                lambda: not platform.process.is_alive(stuck.pid), 10.0), (
                "the fake-stuck git child survived the timeout teardown")
        finally:
            try:
                stuck.kill()
                stuck.wait(timeout=5)
            except Exception:  # pragma: no cover — safety net only
                pass
