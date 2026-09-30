"""Process identity + termination hardening (AUD-R6, W5).

The accepted design (accepted_design_W5.md) froze a process-identity
policy; W1 shipped most of it (ProcessIdentity, JSON markers, token-
authorized terminate, legacy bare-int = coordination-only).  W5 closes
the RESIDUAL: the shared platform-level stop helper
(read-marker → verify-identity → terminate → wait) that BOTH the
watcher-restart and the background-reindex kill paths must route
through, plus the test-spec items it makes enforceable:

- valid identity allows stop                      (real owned sleeper)
- stale marker / dead target rejects              (no signal of any kind)
- same pid, different creation token rejects      (pid-reuse guard)
- wrong role / wrong executable rejects           (kill authority stays narrow)
- self / parent target rejects                    (never signal up the tree)
- watcher and reindexer use shared verification   (one helper, both sites)

Every process used here is SACRIFICIAL and OWNED: spawned by the test,
asserted on, reaped in a finally block.  No arbitrary pid is ever
touched.  No psutil — the identity sources are the platform backends
(win32 GetProcessTimes FILETIME, Linux /proc/<pid>/stat field 22).
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fw_context_mcp.platform import process as _process
from fw_context_mcp.platform.base import ProcessIdentity, read_pid_marker, write_pid_marker


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


def _kill_quietly(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        _wait_dead(proc)


def _real_token(proc: subprocess.Popen) -> int:
    identity = _process.identify(proc.pid)
    assert identity is not None and identity.create_token is not None
    return identity.create_token


# ── Shared helper (W5 residual: platform-level stop sequence) ────────────────


class TestSharedStopHelper:
    """stop_marker_process: the ONE marker-named kill sequence (design req 5)."""

    def test_valid_identity_allows_stop(self, tmp_path: Path):
        """Spec: valid identity allows stop — real owned sleeper, both platforms."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        try:
            marker = tmp_path / "watcher.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc),
                role="watcher-daemon",
            ))
            outcome = stop_marker_process(
                marker, service=_process, grace_s=5.0,
                allowed_roles=("watcher-daemon",),
            )
            _wait_dead(proc)
            assert outcome is StopOutcome.STOPPED
        finally:
            _kill_quietly(proc)

    def test_same_pid_different_token_rejects(self, tmp_path: Path):
        """Spec: same PID / different creation token rejects (pid reuse)."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        try:
            marker = tmp_path / "watcher.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc) + 1,
                role="watcher-daemon",
            ))
            outcome = stop_marker_process(
                marker, service=_process, grace_s=1.0,
                allowed_roles=("watcher-daemon",),
            )
            assert outcome is StopOutcome.REFUSED
            assert _process.is_alive(proc.pid), (
                "a token mismatch must never send a signal"
            )
        finally:
            _kill_quietly(proc)

    def test_legacy_bare_marker_rejects(self, tmp_path: Path):
        """Legacy bare-int markers are coordination-only, never kill-authorizing."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        try:
            marker = tmp_path / "watcher.pid"
            marker.write_text(f"{proc.pid}\n", encoding="utf-8")
            outcome = stop_marker_process(marker, service=_process, grace_s=1.0)
            assert outcome is StopOutcome.REFUSED
            assert _process.is_alive(proc.pid)
        finally:
            _kill_quietly(proc)

    def test_wrong_role_rejects(self, tmp_path: Path):
        """Spec: wrong role rejects — a valid token with the wrong role is denied."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        try:
            marker = tmp_path / "watcher.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc),
                role="background-index",  # wrong role for watcher.pid
            ))
            outcome = stop_marker_process(
                marker, service=_process, grace_s=1.0,
                allowed_roles=("watcher-daemon",),
            )
            assert outcome is StopOutcome.REFUSED
            assert _process.is_alive(proc.pid)
        finally:
            _kill_quietly(proc)

    def test_self_target_rejects(self, tmp_path: Path):
        """Spec: self target rejects — the helper never signals THIS process."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        marker = tmp_path / "reindex.pid"
        write_pid_marker(marker, ProcessIdentity(
            pid=os.getpid(), create_token=12345, role="background-index",
        ))
        outcome = stop_marker_process(marker, service=_process, grace_s=0.1)
        assert outcome is StopOutcome.REFUSED

    def test_parent_target_rejects(self, tmp_path: Path):
        """Spec: parent target rejects — the helper never signals up the tree."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        marker = tmp_path / "reindex.pid"
        write_pid_marker(marker, ProcessIdentity(
            pid=os.getppid(), create_token=12345, role="background-index",
        ))
        outcome = stop_marker_process(marker, service=_process, grace_s=0.1)
        assert outcome is StopOutcome.REFUSED

    def test_missing_marker_is_gone(self, tmp_path: Path):
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        outcome = stop_marker_process(
            tmp_path / "absent.pid", service=_process, grace_s=0.1,
        )
        assert outcome is StopOutcome.GONE

    def test_dead_target_rejects(self, tmp_path: Path):
        """A marker naming a dead pid refuses (identity gone ⇒ no signal)."""
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        token = _real_token(proc)
        proc.kill()
        _wait_dead(proc)
        marker = tmp_path / "watcher.pid"
        write_pid_marker(marker, ProcessIdentity(
            pid=proc.pid, create_token=token, role="watcher-daemon",
        ))
        outcome = stop_marker_process(
            marker, service=_process, grace_s=1.0,
            allowed_roles=("watcher-daemon",),
        )
        assert outcome is StopOutcome.REFUSED


class TestSharedVerificationWiring:
    """Spec: watcher and reindexer use SHARED identity verification."""

    def test_both_kill_paths_route_through_the_shared_helper(self):
        """Both production kill paths must call the platform helper."""
        from fw_context_mcp.cli import _index, _watch

        assert "stop_marker_process(" in inspect.getsource(_index._kill_bg_reindex)
        assert "stop_marker_process(" in inspect.getsource(_watch.cmd_watch_restart)

    def test_win32_restart_kill_path_has_no_sigkill_attribute_error(self):
        """Design RED (c), post-W1 regression: the restart kill path must run
        on win32 without referencing signal.SIGKILL (no such attribute)."""
        if sys.platform != "win32":
            pytest.skip("win32-only: signal.SIGKILL does not exist on Windows")
        from fw_context_mcp.platform import stop_marker_process
        from fw_context_mcp.platform.base import StopOutcome

        proc = _stand_in()
        try:
            marker = tmp_path_marker(proc)
            outcome = stop_marker_process(
                marker, service=_process, grace_s=5.0,
                allowed_roles=("watcher-daemon",),
            )
            _wait_dead(proc)
            assert outcome is StopOutcome.STOPPED
        finally:
            _kill_quietly(proc)


def tmp_path_marker(proc: subprocess.Popen) -> Path:
    import tempfile

    marker = Path(tempfile.mkdtemp(prefix="fwctx_w5_")) / "watcher.pid"
    write_pid_marker(marker, ProcessIdentity(
        pid=proc.pid, create_token=_real_token(proc), role="watcher-daemon",
    ))
    return marker


# ── End-to-end kill-path authorization (O5 oracle, through _kill_bg_reindex) ─


class TestKillPathAuthorization:
    """The production reindex killer refuses everything but a verified
    background reindexer (design RED (a) / O5)."""

    def test_planted_mismatching_token_marker_is_refused(self, tmp_path: Path):
        """Design RED (a): unrelated owned sleeper + planted reindex.pid with
        a MISMATCHING create_token — the killer must refuse."""
        from fw_context_mcp.cli._index import _kill_bg_reindex

        proc = _stand_in()
        try:
            marker = tmp_path / "reindex.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc) + 1,
                role="background-index",
            ))
            _kill_bg_reindex(tmp_path / "index.db")
            assert _process.is_alive(proc.pid), (
                "a planted marker with a mismatching token must never kill"
            )
        finally:
            _kill_quietly(proc)

    def test_valid_token_but_wrong_executable_is_refused(self, tmp_path: Path):
        """Spec: wrong executable rejects — a VALID token for an unrelated
        process is still denied by the background-argv gate (kill authority
        stays narrow: only --background reindex runs are killable)."""
        from fw_context_mcp.cli._index import _kill_bg_reindex

        proc = _stand_in()  # argv carries no fw-context/index/--background
        try:
            marker = tmp_path / "reindex.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc),
                role="background-index",
            ))
            _kill_bg_reindex(tmp_path / "index.db")
            assert _process.is_alive(proc.pid)
        finally:
            _kill_quietly(proc)

    def test_recognized_background_run_with_valid_token_stops(self, tmp_path: Path, monkeypatch):
        """The allow path: argv-recognized background run + verified token ⇒
        terminated through the platform service (end-to-end STOPPED)."""
        import fw_context_mcp.cli._index as idx

        proc = _stand_in()
        try:
            marker = tmp_path / "reindex.pid"
            write_pid_marker(marker, ProcessIdentity(
                pid=proc.pid, create_token=_real_token(proc),
                role="background-index",
            ))
            monkeypatch.setattr(idx, "_pid_is_fw_context_reindexer", lambda pid: True)
            idx._kill_bg_reindex(tmp_path / "index.db")
            _wait_dead(proc)
            assert not _process.is_alive(proc.pid)
        finally:
            _kill_quietly(proc)

    def test_legacy_marker_names_live_process_and_is_not_killed(self, tmp_path: Path):
        """Rolling-upgrade compat: a pre-W1 bare-int marker naming a LIVE
        process is coordination-only — no kill (explicitly tested per design)."""
        from fw_context_mcp.cli._index import _kill_bg_reindex

        proc = _stand_in()
        try:
            marker = tmp_path / "reindex.pid"
            marker.write_text(f"{proc.pid}\n", encoding="utf-8")
            _kill_bg_reindex(tmp_path / "index.db")
            assert _process.is_alive(proc.pid)
        finally:
            _kill_quietly(proc)
