"""``fw-context watch`` — background watcher daemon management.

Manages the file-watching daemon that keeps the symbol index current by
reindexing modified source files automatically.  The daemon monitors the
project directory for changes and calls ``reindex_file_impl`` for each
changed file.

WHY a daemon: during development, the index quickly falls out of sync with
source files if the user must remember to reindex manually.  The daemon
uses inotify/FSEvents to detect changes and reindexes within seconds.

WHY debouncing: editors save frequently (auto-save, rapid edits).
Reindexing on every event would waste CPU.  A cooldown period collects
multiple rapid saves into a single reindex.
"""

from __future__ import annotations

import argparse
import os
import sqlite3

from ..platform import transport as _transport


def _unlink_when_released(path, *, wait_s: float) -> None:
    """Unlink *path*, retrying briefly while a dying process still holds it.

    win32 maps ``ERROR_SHARING_VIOLATION`` (32) to ``PermissionError``;
    a ``wait_s`` of 0 keeps the single-attempt semantics.
    """
    import time as _t

    deadline = _t.monotonic() + wait_s
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if _t.monotonic() >= deadline:
                raise
            _t.sleep(0.1)


def _read_spawn_error(db_dir) -> str | None:
    """The reason the last daemon spawn failed, when one was recorded.

    ``mcp.background._spawn_daemon`` writes ``daemon.spawn_error`` on any
    failure; this reads it back for ``watch status``.
    """
    from pathlib import Path

    path = Path(db_dir) / "daemon.spawn_error"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def _spawn_error_suffix(db_dir) -> str:
    """Status-line fragment naming the last spawn failure, or ``""``."""
    reason = _read_spawn_error(db_dir)
    return f" (last spawn failed: {reason})" if reason else ""


def cmd_watch(args: argparse.Namespace) -> int:
    """Dispatcher for ``fw-context watch`` sub-subcommands."""
    if getattr(args, "watch_command", None) is None:
        print("Usage: fw-context watch <status|restart>")
        return 1
    return args.func(args)


def cmd_watch_status(args: argparse.Namespace) -> int:
    """Print the watcher daemon status for a project."""

    from ..config import derive_project_id
    from ..config import load as load_config
    from ..mcp.background import _is_bg_reindex_running, read_reindex_progress
    from ..mcp.daemon import ping_daemon
    from ..utils import resolve_project_root

    root = resolve_project_root(args.project)
    cfg = load_config(project_root=root)
    project_id = derive_project_id(root)
    db_dir = cfg.index.db_dir / project_id

    pid_file = db_dir / "watcher.pid"

    print(f"Project:    {root.name}")
    print(f"Path:       {root}")
    print(f"DB dir:     {db_dir}")

    # Daemon status
    alive = ping_daemon(root)
    if alive:
        if pid_file.exists():
            try:
                from ..platform import marker_pid

                pid = marker_pid(pid_file)
                import time as _time

                uptime_s = _time.time() - pid_file.stat().st_mtime
                print(f"Daemon:     running (pid {pid}, uptime {int(uptime_s)}s)")
            except (OSError, ValueError):
                print("Daemon:     running")
        else:
            print("Daemon:     running")
    else:
        artifact = _transport.artifact_path(db_dir)
        if artifact.exists():
            print("Daemon:     not responding (endpoint exists but no response)")
        elif pid_file.exists():
            print("Daemon:     dead (pid file exists but no response)")
        else:
            print(f"Daemon:     not running{_spawn_error_suffix(db_dir)}")

    # Endpoint
    print(f"Endpoint:   {_transport.artifact_path(db_dir)}{' (active)' if alive else ''}")

    # Modified files count
    db_path = db_dir / "index.db"
    if db_path.exists():
        try:
            from ..indexer.db import get_active_config, open_db
            from ..mcp.shared.stale import _count_modified_files

            conn = open_db(db_path)
            try:
                cfg_data = get_active_config(conn, project_id)
                if cfg_data:
                    mod_count = _count_modified_files(conn, cfg_data["config_hash"], root, use_cache=False)
                    print(f"Modified:   {mod_count} file(s)")
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            pass

    # Index subprocess
    if _is_bg_reindex_running(root):
        print("Index:      running")
    else:
        print("Index:      idle")

    # Last line the index run itself wrote.  Not the last line of the
    # file: reindex.log also holds the output of the build tools, and the
    # helper says why taking that would be wrong.
    last = read_reindex_progress(db_dir)
    if last is not None:
        print(f"Last index: {last}")

    return 0


def cmd_watch_restart(args: argparse.Namespace) -> int:
    """Restart the watcher daemon for a project."""
    import time as _time

    from ..config import derive_project_id
    from ..config import load as load_config
    from ..mcp.background import _ensure_daemon_running
    from ..mcp.daemon import ping_daemon
    from ..platform import ROLE_WATCHER_DAEMON, StopOutcome, read_pid_marker
    from ..platform import process as _process
    from ..platform import stop_marker_process
    from ..utils import resolve_project_root

    root = resolve_project_root(args.project)
    cfg = load_config(project_root=root)
    project_id = derive_project_id(root)
    db_dir = cfg.index.db_dir / project_id

    pid_file = db_dir / "watcher.pid"

    # Kill existing daemon — through the platform's ONE marker-named kill
    # sequence (stop_marker_process: read-marker → verify-identity →
    # terminate → wait, W5), shared with the background-reindex killer so
    # both paths enforce the same identity policy.  The marker must carry
    # the start-time token of the LIVE pid (D2): a bare-int legacy marker
    # is coordination-only and refuses to authorize the kill; a token
    # mismatch (pid reuse) refuses inside the service with both
    # identities logged; a marker naming this process or its parent is
    # refused by the helper.  ``allowed_roles`` is safe ONLY here:
    # watcher.pid has exactly one writer (the daemon) and always carries
    # ``watcher-daemon``.  win32 has no SIGKILL — the pre-W1
    # ``signal.SIGKILL`` here was a latent AttributeError;
    # ProcessService.terminate is the per-platform path (win32
    # TerminateProcess, documented as non-graceful by nature — the
    # daemon's own graceful path is the ping-timeout loop).
    attempted_pid = None
    if pid_file.exists():
        marker = read_pid_marker(pid_file)
        if marker is not None and marker.pid != os.getpid():
            attempted_pid = marker.pid
            print(f"Stopping daemon (pid {marker.pid})...")
    outcome = stop_marker_process(
        pid_file,
        service=_process,
        grace_s=3.0,
        allowed_roles=(ROLE_WATCHER_DAEMON,),
    )
    if outcome is StopOutcome.STOPPED:
        print("Old daemon stopped.")
    elif outcome is StopOutcome.REFUSED and attempted_pid is not None:
        print(
            "Old daemon not terminated (no verified identity or refusal) — "
            "continuing with endpoint cleanup."
        )

    # Clean up leftover artifacts (daemon.sock on POSIX, daemon.port on win32)
    _transport.cleanup(db_dir)
    # win32 teardown race (surfaced by the W5 restart e2e): TerminateProcess
    # signals the process object BEFORE the kernel has finished tearing down
    # the dead daemon's handle table, so its still-open ``watcher.lock`` can
    # briefly reject deletion (WinError 32 sharing violation).  After a
    # verified stop, retry the unlinks for a bounded window; everywhere else
    # a single attempt keeps the pre-W5 semantics (a LIVE daemon that refused
    # to die still blocks the cleanup, honestly).
    post_stop_wait = 3.0 if outcome is StopOutcome.STOPPED else 0.0
    _unlink_when_released(pid_file, wait_s=post_stop_wait)
    _unlink_when_released(db_dir / "watcher.lock", wait_s=post_stop_wait)

    # Spawn new daemon
    print("Starting new daemon...")
    _ensure_daemon_running(root)

    # Wait and verify
    _time.sleep(0.5)
    if ping_daemon(root):
        print("Daemon restarted successfully.")
    else:
        print("Daemon may still be starting — check 'fw-context watch status' in a moment.")

    return 0
