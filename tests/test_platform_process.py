"""ProcessService — liveness, start-time identity, terminate authorization.

The adapter owns every platform difference in process queries:

- ``is_alive``: POSIX three-outcome ``os.kill(pid, 0)``; win32
  ``OpenProcess`` + ``GetExitCodeProcess`` (never console events).
- ``identify``: start-time create_token — win32 ``GetProcessTimes``
  creation FILETIME, Linux ``/proc/<pid>/stat`` field 22, other POSIX
  ``None``.  A marker whose token does not match the live process is
  stale (pid reuse) and must never authorize a kill.
- ``terminate``: refuses ``create_token=None`` (legacy bare-int markers
  are coordination-only) and refuses a token mismatch; kills only on a
  verified match (win32 TerminateProcess, POSIX SIGTERM→SIGKILL).
- ``command_line``: cross-platform argv read (POSIX /proc cmdline, win32
  PEB read) so the background-run recognition works on both platforms.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from fw_context_mcp.platform import process
from fw_context_mcp.platform.base import ProcessIdentity

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _stand_in(*argv: str) -> subprocess.Popen:
    """A sleeping child carrying *argv*, returned only after exec finished."""
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c",
         "import time; print('READY', flush=True); time.sleep(30)", *argv],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    return proc


def _wait_dead(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    proc.wait(timeout=timeout)


class TestIsAlive:
    def test_a_live_child_is_alive(self):
        proc = _stand_in()
        try:
            assert process.is_alive(proc.pid) is True
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_a_dead_child_is_not_alive(self):
        proc = _stand_in()
        proc.kill()
        _wait_dead(proc)
        assert process.is_alive(proc.pid) is False

    def test_a_never_existing_pid_is_not_alive(self):
        assert process.is_alive(2147483646) is False


class TestIdentify:
    def test_token_is_stable_across_calls(self):
        proc = _stand_in()
        try:
            first = process.identify(proc.pid)
            second = process.identify(proc.pid)
            assert first is not None
            assert first.pid == proc.pid
            assert second is not None
            assert first.create_token == second.create_token, (
                "start-time token must be stable while the process lives"
            )
        finally:
            proc.kill()
            _wait_dead(proc)

    @pytest.mark.skipif(
        sys.platform not in ("win32", "linux"),
        reason="start-time token backend only implemented for win32 and Linux",
    )
    def test_token_is_available_on_supported_platforms(self):
        proc = _stand_in()
        try:
            identity = process.identify(proc.pid)
            assert identity is not None and identity.create_token is not None
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_dead_pid_identifies_to_none(self):
        assert process.identify(2147483646) is None


class TestCurrentIdentity:
    def test_carries_own_pid_and_role(self):
        identity = process.current_identity("test-role")
        assert identity.pid == __import__("os").getpid()
        assert identity.role == "test-role"


class TestTerminateAuthorization:
    """A kill is authorized ONLY by a verified start-time identity."""

    def test_terminates_when_the_token_matches(self):
        proc = _stand_in()
        identity = process.identify(proc.pid)
        assert identity is not None and identity.create_token is not None
        granted = process.terminate(proc.pid, identity, grace_s=5.0)
        _wait_dead(proc)
        assert granted is True, "matching identity must authorize the kill"

    def test_refuses_a_mismatched_token(self):
        """Pid reuse guard: a stale token must deny the kill."""
        proc = _stand_in()
        try:
            identity = process.identify(proc.pid)
            assert identity is not None
            stale = ProcessIdentity(
                pid=proc.pid,
                create_token=(identity.create_token or 0) + 1,
                role=identity.role,
                recorded_at=identity.recorded_at,
            )
            granted = process.terminate(proc.pid, stale, grace_s=1.0)
            assert granted is False
            assert process.is_alive(proc.pid) is True, (
                "a mismatched token must never kill the process"
            )
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_refuses_a_marker_without_a_token(self):
        """Legacy bare-int markers are coordination-only, never kill-authorizing."""
        proc = _stand_in()
        try:
            legacy = ProcessIdentity(pid=proc.pid, create_token=None, role="legacy")
            assert process.terminate(proc.pid, legacy, grace_s=1.0) is False
            assert process.is_alive(proc.pid) is True
        finally:
            proc.kill()
            _wait_dead(proc)


class TestCommandLine:
    def test_background_argv_is_visible(self):
        """The daemon's exact spawn shape must be recognizable cross-platform."""
        proc = _stand_in("-m", "fw_context_mcp.cli", "index", "--background")
        try:
            argv = process.command_line(proc.pid)
            assert argv is not None, "command_line must work on this platform"
            joined = " ".join(argv)
            assert "fw_context_mcp.cli" in joined
            assert "index" in argv or any(a == "index" for a in argv)
            assert "--background" in argv
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_foreground_argv_lacks_background(self):
        proc = _stand_in("-m", "fw_context_mcp.cli", "index")
        try:
            argv = process.command_line(proc.pid)
            assert argv is not None
            assert "--background" not in argv
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_dead_pid_gives_none(self):
        assert process.command_line(2147483646) is None


class TestPosixBackendSemantics:
    """The three-outcome ``os.kill(pid, 0)`` mapping stays in posix.py."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX backend")
    def test_esrch_reads_dead_eperm_reads_alive(self, monkeypatch: pytest.MonkeyPatch):
        from fw_context_mcp.platform import posix as posix_backend

        def _esrch(pid: int, sig: int) -> None:
            raise ProcessLookupError()

        def _eperm(pid: int, sig: int) -> None:
            raise PermissionError()

        monkeypatch.setattr("os.kill", _esrch)
        assert posix_backend.PosixProcessService().is_alive(1234) is False
        monkeypatch.setattr("os.kill", _eperm)
        assert posix_backend.PosixProcessService().is_alive(1234) is True
