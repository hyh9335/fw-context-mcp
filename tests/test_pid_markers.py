"""JSON pid markers with start-time identity (D2 marker upgrade).

Markers upgrade to JSON payloads ``{pid, create_token, role}``:

- ``reindex.pid`` and ``watcher.pid`` are written as JSON with the
  writer's start-time token;
- readers accept BOTH formats — a legacy bare-int pid keeps working for
  coordination (alive checks) but carries no kill authorization;
- ``PidFile.read_pid``/``is_active``/``unlink_if_ours`` must be
  JSON-aware, otherwise a JSON marker is treated as garbage and
  unlinked while its process is still alive.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fw_context_mcp.mcp.shared.pid_file import PidFile


def _sleeper() -> subprocess.Popen:
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


class TestPidFileReadsBothFormats:
    def test_bare_int_still_reads(self, tmp_path: Path):
        path = tmp_path / "marker"
        path.write_text("12345\n", encoding="utf-8")
        assert PidFile.read_pid(path) == 12345

    def test_json_marker_pid_is_read(self, tmp_path: Path):
        """A JSON marker must NOT be treated as garbage (and unlinked)."""
        path = tmp_path / "reindex.pid"
        payload = {"pid": 4242, "create_token": 132345, "role": "background-index"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert PidFile.read_pid(path) == 4242
        assert path.exists(), "a valid JSON marker must not be cleaned up as corrupt"

    def test_is_active_with_json_marker_of_live_process(self, tmp_path: Path):
        path = tmp_path / "reindex.pid"
        proc = _sleeper()
        try:
            path.write_text(
                json.dumps({"pid": proc.pid, "create_token": None, "role": "x"}),
                encoding="utf-8",
            )
            assert PidFile.is_active(path) is True
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_unlink_if_ours_with_json_marker(self, tmp_path: Path):
        path = tmp_path / "reindex.pid"
        proc = _sleeper()
        try:
            path.write_text(
                json.dumps({"pid": proc.pid, "create_token": 1, "role": "x"}),
                encoding="utf-8",
            )
            PidFile(path, pid=proc.pid).unlink_if_ours()
            assert not path.exists(), (
                "unlink_if_ours must compare the pid FIELD of a JSON marker"
            )
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_unlink_if_ours_keeps_a_foreign_json_marker(self, tmp_path: Path):
        path = tmp_path / "reindex.pid"
        payload = {"pid": 999999999, "create_token": 1, "role": "x"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        PidFile(path, pid=1).unlink_if_ours()
        assert path.exists(), "a JSON marker naming another pid must survive"

    def test_garbage_is_still_cleaned_up(self, tmp_path: Path):
        path = tmp_path / "marker"
        path.write_text("not a pid at all", encoding="utf-8")
        assert PidFile.read_pid(path) is None
        assert not path.exists()
