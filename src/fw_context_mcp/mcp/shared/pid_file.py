"""PID marker files — write, read, liveness check, stale cleanup.

TOCTOU note: a bare-PID liveness check has an inherent race — the PID may be
reused between the check and the action.  Risk is low on Linux (PID wrap
at 4M), and the platform lock adapter is used where correctness matters
(``watcher.lock``).  The helpers in this module are for **coordination
markers** (pause, reindex-in-progress), not mutual exclusion.

Platform split: liveness queries go through ``fw_context_mcp.platform``
(``ProcessService``).  On POSIX it uses ``os.kill(pid, 0)`` (signal 0
delivers nothing, three-outcome mapping).  On Windows ``os.kill`` is never
touched — with signal 0 it routes to ``GenerateConsoleCtrlEvent`` and
DELIVERS CTRL_C to the target's console process group — the win32 backend
queries the process handle instead (``OpenProcess`` +
``GetExitCodeProcess``).

Marker formats (D2 marker upgrade, W1): a marker may hold

- a legacy bare ``"<pid>"`` line — coordination-only: alive checks work,
  but it carries no start-time identity, so nothing may kill by it;
- JSON ``{"pid": ..., "create_token": ..., "role": ..., "recorded_at": ...}``
  written via ``platform.write_pid_marker`` — the start-time token
  authorizes identity-checked termination.

Readers accept BOTH formats.  A JSON marker must never be treated as
garbage: its process is alive and ``read_pid``/``unlink_if_ours`` must
compare the pid FIELD.

WHY PID files instead of a database flag: the background reindex runs in
a separate OS process (``subprocess.Popen``).  It cannot share an in-memory
mutex or a Python ``threading.Lock`` with the MCP server process.  PID files
are the simplest cross-process coordination primitive — the filesystem is
the only IPC channel guaranteed to exist without additional infrastructure.

WHY ``unlink_if_ours`` checks the PID before unlinking: during a long
``fw-context index --force`` run, a second MCP server may write its own
pause marker, then finish and try to clean up.  Without PID ownership
checking, it would delete the first server's marker, prematurely resuming
the background reindex.  PID-based ownership prevents this.
"""

from __future__ import annotations

import os
from pathlib import Path

from ...platform import marker_pid as _marker_pid
from ...platform import process as _process


class PidFile:
    """Write a PID to a file and optionally clean it up.

    Use as a context manager for auto-cleanup::

        with PidFile(path) as pf:
            ...  # PID file exists while the block is active

    Or use the instance methods for manual lifecycle::

        pf = PidFile(path)
        pf.write()
        ...
        pf.unlink_if_ours()

    Static helpers (:meth:`is_active`, :meth:`read_pid`, :meth:`_pid_exists`)
    are available for callers that only need to inspect a PID file.
    """

    def __init__(self, path: Path, pid: int | None = None) -> None:
        self._path = path
        self._pid: int = pid if pid is not None else os.getpid()

    # ── Properties ──────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """The filesystem path of this PID file."""
        return self._path

    @property
    def pid(self) -> int:
        """The PID written (or to be written) to the file."""
        return self._pid

    # ── Context manager ─────────────────────────────────────────────

    def __enter__(self) -> PidFile:
        self.write()
        return self

    def __exit__(self, *args: object) -> None:
        self.unlink_if_ours()

    # ── Instance methods ────────────────────────────────────────────

    def write(self) -> None:
        """Write our PID to the file, overwriting any existing content."""
        self._path.write_text(str(self._pid), encoding="utf-8")

    def unlink_if_ours(self) -> None:
        """Remove the PID file, but **only** if it names our PID.

        A concurrent writer may have overwritten the file with its own
        identity (e.g. another ``fw-context index --force`` invocation) —
        in that case the marker must stay so the background reindex
        remains paused for that caller.  Both marker formats compare the
        pid FIELD, not the raw text.
        """
        try:
            if not self._path.exists():
                return
            if _marker_pid(self._path) == self._pid:
                self._path.unlink(missing_ok=True)
        except OSError:
            pass

    # ── Static helpers ──────────────────────────────────────────────

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        """Check whether a process with *pid* is running — a pure query on
        every platform: it must never change the target's state.

        POSIX: ``os.kill(pid, 0)`` — signal 0 is an existence check only.
        The three outcomes mean different things and must not be collapsed:

        - success — the process exists and we may signal it.
        - ``ProcessLookupError`` (ESRCH) — no such process.
        - ``PermissionError`` (EPERM) — the process EXISTS, we simply may not
          signal it.  It belongs to another user.

        Catching bare ``OSError`` reported that last case as dead, and
        :meth:`is_active` deletes the PID file when the process reads as dead.
        A pause marker written by another user's process would therefore be
        removed by a mere liveness check, resuming a background reindex that
        was deliberately held, or letting retention delete the build that
        process is still writing.

        Windows: ``os.kill`` is NEVER used — with signal 0 it routes to
        ``GenerateConsoleCtrlEvent`` and delivers CTRL_C to the pid's console
        process group (asynchronously, ~2-8 ms), and it does not raise for
        dead-but-console-registered pids.  The win32 backend instead queries
        the process handle: ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)``
        followed by ``GetExitCodeProcess``; a live process reports
        ``STILL_ACTIVE`` (259).  The error mapping is conservative — a
        live-but-unqueryable process must not read dead, because
        :meth:`is_active` unlinks the marker of a process it believes is
        gone:

        - ``OpenProcess`` fails with ``ERROR_ACCESS_DENIED`` (5) — alive
          (exists, cannot query; mirrors the POSIX EPERM mapping).
        - ``OpenProcess`` fails with ``ERROR_INVALID_PARAMETER`` (87) or any
          other error — no such pid / gone.
        - ``GetExitCodeProcess`` fails — alive (conservative).
        - exit code ``STILL_ACTIVE`` — alive; anything else — dead.

        Known limitations (accepted, same class as POSIX ``kill(pid, 0)``
        zombie semantics): a process that exits *with* code 259 reads alive;
        a terminated-but-unreaped process reads alive; pid reuse between the
        probe and the action can flip the answer false-alive.  These affect
        only marker-cleanup timing — the query delivers nothing under any
        circumstance.  The W1 start-time token (``ProcessIdentity``) is the
        answer where pid reuse actually matters: kill paths verify it.
        """
        return _process.is_alive(pid)

    @staticmethod
    def is_active(path: Path) -> bool:
        """Return ``True`` when *path* exists and the process is alive.

        Cleans up stale files (PID not alive or unparseable content)
        automatically — the caller never sees a stale PID file as active.
        Both marker formats are understood; a JSON marker for a LIVE
        process is valid, never garbage.
        """
        if not path.exists():
            return False
        pid = _marker_pid(path)
        if pid is None:
            path.unlink(missing_ok=True)
            return False
        if PidFile._pid_exists(pid):
            return True
        path.unlink(missing_ok=True)
        return False

    @staticmethod
    def is_active_other(path: Path) -> bool:
        """Return ``True`` when *path* is held by a live OTHER process.

        WHY this exists next to :meth:`is_active`: a pause marker written by
        THIS process must not block this process's own work.  ``fw-context
        index`` writes ``reindex.pause`` with its own PID for the whole run,
        so a guard built on :meth:`is_active` is always true inside that run
        and silently disables whatever it protects.

        Use :meth:`is_active` to answer "is anyone paused?" and this to
        answer "is someone ELSE paused?".  A guard that must not fire on our
        own marker needs the latter.
        """
        if not PidFile.is_active(path):
            return False
        pid = PidFile.read_pid(path)
        return pid is not None and pid != os.getpid()

    @staticmethod
    def read_pid(path: Path) -> int | None:
        """Read the PID from *path* — legacy bare-int or JSON marker.

        Returns ``None`` when the file does not exist or contains
        garbage.  Unparseable files are cleaned up automatically; a JSON
        marker is parsed, never deleted for its format.
        """
        if not path.exists():
            return None
        pid = _marker_pid(path)
        if pid is None:
            path.unlink(missing_ok=True)
        return pid
