"""Daemon spawn must not die on missing os.O_NOFOLLOW (RED #4, D4/D5).

On win32 ``os.O_NOFOLLOW`` does not exist, so ``background._spawn_daemon``
raises AttributeError at ``os.open`` — caught and logged, meaning the
daemon silently never spawns on Windows and the CLI cannot say why.

Accepted design:

- log opening goes through ``FileSystemCapabilities.safe_open_log``
  (POSIX O_NOFOLLOW strict; win32 lstat reparse refusal);
- a spawn failure is OBSERVABLE: the reason is recorded to
  ``<db_dir>/daemon.spawn_error`` and ``fw-context watch status`` surfaces
  it;
- a CLI index run completes even when the daemon cannot spawn.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from fw_context_mcp.mcp import background

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _fake_project(tmp_path: Path) -> Path:
    """A db layout _db_path can return without loading a real config."""
    db_path = tmp_path / "index" / "proj-id" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()  # _ensure_daemon_running bails when the index is absent
    return db_path


def _patch_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr(background, "_db_path", lambda root: db_path)


class TestSpawnReachesPopen:
    def test_spawn_reaches_popen_on_this_platform(self, monkeypatch, tmp_path: Path):
        """The spawn path must reach Popen — no AttributeError before it.

        Pre-fix Windows: ``os.open(log, os.O_NOFOLLOW|...)`` raises
        AttributeError (os.O_NOFOLLOW missing) and Popen is never called.
        """
        db_path = _fake_project(tmp_path)
        _patch_db_path(monkeypatch, db_path)
        called: list[object] = []

        def _fake_popen(args, **kwargs):
            called.append(args)
            return types.SimpleNamespace(pid=424242)

        monkeypatch.setattr(background.subprocess, "Popen", _fake_popen)
        background._spawn_daemon(tmp_path)
        assert called, (
            "the daemon spawn never reached Popen — pre-fix win32 dies at "
            "os.O_NOFOLLOW (AttributeError) and swallows it"
        )
        argv = called[0]
        assert "fw_context_mcp.mcp.daemon" in argv

    def test_spawn_injects_utf8_env(self, monkeypatch, tmp_path: Path):
        """D6.2: internal daemon spawn carries PYTHONUTF8=1."""
        db_path = _fake_project(tmp_path)
        _patch_db_path(monkeypatch, db_path)
        seen: dict[str, object] = {}

        def _fake_popen(args, **kwargs):
            seen["env"] = kwargs.get("env")
            return types.SimpleNamespace(pid=424242)

        monkeypatch.setattr(background.subprocess, "Popen", _fake_popen)
        background._spawn_daemon(tmp_path)
        env = seen.get("env") or {}
        assert env.get("PYTHONUTF8") == "1"


class TestSpawnFailureIsObservable:
    def test_poisoned_log_path_does_not_raise_and_is_recorded(
        self, monkeypatch, tmp_path: Path
    ):
        """daemon.log made a DIRECTORY: open fails, spawn aborts, reason recorded."""
        db_path = _fake_project(tmp_path)
        (db_path.parent / "daemon.log").mkdir()
        _patch_db_path(monkeypatch, db_path)

        background._spawn_daemon(tmp_path)  # must not raise

        spawn_error = db_path.parent / "daemon.spawn_error"
        assert spawn_error.exists(), (
            "a failed spawn must record its reason in daemon.spawn_error"
        )
        assert spawn_error.read_text(encoding="utf-8").strip()

    def test_successful_spawn_clears_the_stale_record(self, monkeypatch, tmp_path: Path):
        db_path = _fake_project(tmp_path)
        (db_path.parent / "daemon.spawn_error").write_text(
            "old failure", encoding="utf-8"
        )
        _patch_db_path(monkeypatch, db_path)
        monkeypatch.setattr(
            background.subprocess, "Popen",
            lambda args, **kw: types.SimpleNamespace(pid=424242),
        )
        background._spawn_daemon(tmp_path)
        assert not (db_path.parent / "daemon.spawn_error").exists()


class TestWatchStatusSurfacesSpawnFailure:
    def test_read_spawn_error_returns_the_reason(self, tmp_path: Path):
        from fw_context_mcp.cli._watch import _read_spawn_error

        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        assert _read_spawn_error(db_dir) is None
        (db_dir / "daemon.spawn_error").write_text(
            "refused: symlink at log path", encoding="utf-8"
        )
        assert "symlink" in (_read_spawn_error(db_dir) or "")

    def test_status_line_names_the_spawn_failure(self, tmp_path: Path):
        from fw_context_mcp.cli._watch import _spawn_error_suffix

        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        assert _spawn_error_suffix(db_dir) == ""
        (db_dir / "daemon.spawn_error").write_text("disk full", encoding="utf-8")
        suffix = _spawn_error_suffix(db_dir)
        assert "disk full" in suffix and "spawn" in suffix


class TestSpawnDoesNotCrashRealFlow:
    def test_ensure_daemon_running_completes_without_a_daemon(
        self, monkeypatch, tmp_path: Path
    ):
        """The CLI index flow calls this; it must return, not raise."""
        from fw_context_mcp.mcp import background as bg

        db_path = _fake_project(tmp_path)
        (db_path.parent / "daemon.log").mkdir()  # poisoned spawn
        _patch_db_path(monkeypatch, db_path)
        monkeypatch.setattr(
            "fw_context_mcp.mcp.daemon.ping_daemon", lambda root: False
        )
        # The real spawn will fail (poisoned log) — that must stay internal.
        bg._ensure_daemon_running(tmp_path)
        assert (db_path.parent / "daemon.spawn_error").exists()


class TestSubprocessUtf8Isolation:
    def test_spawn_env_in_subprocess_is_utf8(self):
        """Sanity for D6.2 in a fresh interpreter: subprocess_env carries the flags."""
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {_SRC!r})\n"
            "from fw_context_mcp.platform.base import subprocess_env\n"
            "env = subprocess_env()\n"
            "assert env.get('PYTHONUTF8') == '1', env.get('PYTHONUTF8')\n"
            "assert env.get('PYTHONIOENCODING') == 'utf-8'\n"
            "print('OK')\n"
        )
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        
        encoding="utf-8",
        errors="replace",
    )
        assert result.returncode == 0, result.stdout + result.stderr
