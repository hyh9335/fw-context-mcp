"""Kill paths must be start-time-identity-checked (D2 adoption, W1).

``cli/_index.py:_kill_bg_reindex`` reads ``reindex.pid`` and terminates the
named process.  Under the accepted design:

- the marker identity (pid + create_token) must match the LIVE process'
  start-time token, or the kill is refused (pid reuse guard);
- a legacy bare-int marker carries no token 鈥?coordination-only, never
  kill-authorizing;
- the background-argv recognition gate stays as a second condition;
- killing uses ProcessService.terminate (win32 TerminateProcess /
  POSIX SIGTERM鈫扴IGKILL), never raw signal branching.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from fw_context_mcp.cli._index import _kill_bg_reindex
from fw_context_mcp.platform import process


def _bg_stand_in() -> subprocess.Popen:
    """A sleeper carrying the daemon's exact background-index argv."""
    proc = subprocess.Popen(  # noqa: S603 鈥?fixed argv, no shell
        [sys.executable, "-c",
         "import time; print('READY', flush=True); time.sleep(60)",
         "-m", "fw_context_mcp.cli", "index", "--background"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    return proc


def _wait_dead(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    proc.wait(timeout=timeout)


def _write_marker(path: Path, proc: subprocess.Popen, token) -> None:
    path.write_text(
        json.dumps(
            {"pid": proc.pid, "create_token": token, "role": "background-index"}
        ),
        encoding="utf-8",
    )


class TestKillBgReindexIdentity:
    def test_verified_marker_terminates_the_background_run(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _bg_stand_in()
        try:
            identity = process.identify(proc.pid)
            assert identity is not None
            _write_marker(db_path.parent / "reindex.pid", proc, identity.create_token)
            _kill_bg_reindex(db_path)
            _wait_dead(proc, timeout=15.0)
            assert process.is_alive(proc.pid) is False, (
                "a verified identity must authorize the kill"
            )
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_stale_token_marker_is_never_killed(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _bg_stand_in()
        try:
            identity = process.identify(proc.pid)
            assert identity is not None
            _write_marker(
                db_path.parent / "reindex.pid", proc, (identity.create_token or 0) + 7
            )
            _kill_bg_reindex(db_path)
            time.sleep(0.5)
            assert process.is_alive(proc.pid) is True, (
                "a mismatched token must never kill the process"
            )
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_legacy_bare_int_marker_is_never_killed(self, tmp_path: Path):
        """A pre-W1 bare-int marker is coordination-only 鈥?no kill authorization."""
        db_path = tmp_path / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        proc = _bg_stand_in()
        try:
            (db_path.parent / "reindex.pid").write_text(str(proc.pid), encoding="utf-8")
            _kill_bg_reindex(db_path)
            time.sleep(0.5)
            assert process.is_alive(proc.pid) is True, (
                "a bare-int marker must not authorize a kill"
            )
        finally:
            proc.kill()
            _wait_dead(proc)

    def test_missing_marker_is_a_noop(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _kill_bg_reindex(db_path)  # must not raise
        assert not (db_path.parent / "reindex.pid").exists()
