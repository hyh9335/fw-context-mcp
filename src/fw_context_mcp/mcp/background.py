"""Background services — daemon lifecycle, auto-reindex, pause coordination.

The background module solves a coordination problem: multiple MCP server
processes (one per AI assistant instance) share a single per-project index.
They must agree on who spawns the watcher daemon, who detects staleness,
and how to pause the background reindex during write operations.

Key design decisions:

- **Daemon spawning uses the platform lock adapter (not PID files) for mutual
  exclusion**: PID files have an inherent TOCTOU race — the PID may be reused
  between ``read_pid()`` and a kill.  The kernel lock (``fcntl.flock`` on
  POSIX, ``msvcrt.locking`` on win32) is atomic and survives process death
  (the kernel releases it automatically).  The spawning protocol in
  ``_ensure_daemon_running`` holds the lock, spawns the daemon, then
  releases — the daemon blocks on its own lock acquisition and picks up the
  lock as soon as the spawner releases.  No race window.

- **The check of the work that waits is in the daemon**: the daemon's
  ``_staleness_check`` runs one time at its start.  It finds the work of the
  time when no daemon watched: the structural reasons, files that changed,
  a checkout and new source files.  watchfiles finds only NEW changes.

- **Pause/resume protocol**: before an MCP tool writes to the index
  (reindex_file), it calls ``_request_bg_reindex_pause`` to prevent
  the daemon's background reindex from holding the write lock.  The pause
  marker is a PID file — if the requesting process dies, the daemon detects
  the stale marker and resumes automatically.  This is safe because the
  background reindex checks the marker BETWEEN translation units, not
  mid-operation.
"""

from __future__ import annotations

import logging
import sys
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from ..platform import fs
from ..platform import lock as _platform_lock
from ..platform import spawn_detach_kwargs, subprocess_env
from .shared.context import _db_path
from .shared.pid_file import PidFile

log = logging.getLogger(__name__)


def _pid_exists(pid: int) -> bool:
    """Check whether a process with the given PID is running.

    Delegates to :meth:`PidFile._pid_exists`.
    """
    return PidFile._pid_exists(pid)


def _record_spawn_failure(db_dir: Path, reason: str) -> None:
    """Persist a spawn failure so ``fw-context watch status`` can surface it.

    Best-effort: the record must never mask the original failure.  The
    file lives next to the daemon's other endpoint artifacts and is
    cleared on the next successful spawn.
    """
    try:
        (db_dir / "daemon.spawn_error").write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n",
            encoding="utf-8",
        )
    except OSError:
        log.debug("could not record daemon spawn failure for %s", db_dir, exc_info=True)


def _clear_spawn_failure(db_dir: Path) -> None:
    try:
        (db_dir / "daemon.spawn_error").unlink(missing_ok=True)
    except OSError:
        log.debug("could not clear daemon spawn record for %s", db_dir, exc_info=True)


def _spawn_daemon(root: Path) -> None:
    """Spawn the watcher daemon, logging output to a file.

    Must be called without holding ``watcher.lock`` so the daemon can
    acquire it on startup.

    The log path opens through the platform ``safe_open_log`` — POSIX
    ``O_NOFOLLOW``; win32 has no ``O_NOFOLLOW`` (the pre-W1
    ``AttributeError`` silently killed every spawn) and refuses a reparse
    point instead.  A refusal or any other failure is RECORDED to
    ``daemon.spawn_error`` and surfaced by ``fw-context watch status``;
    the CLI caller completes either way.
    """
    db_path = _db_path(root)
    db_dir = db_path.parent
    log_file = db_dir / "daemon.log"
    try:
        fd = fs.safe_open_log(log_file, append=True)
        if fd is None:
            # Refusal (symlink/reparse at the log path) aborts the spawn and
            # is recorded — a daemon writing to devnull would be invisible.
            raise OSError(
                f"refused to open daemon log {log_file} (symlink/reparse protection)"
            )
        log_fh = os.fdopen(fd, "a", encoding="utf-8", errors="replace", closefd=True)
        try:
            subprocess.Popen(
                [sys.executable, "-u", "-m", "fw_context_mcp.mcp.daemon", str(root)],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=subprocess_env(),
                **spawn_detach_kwargs(),
            )
        finally:
            log_fh.close()
        # Daemon process runs independently (detached kwargs per platform)
        _clear_spawn_failure(db_dir)
    except (OSError, ValueError, TypeError, RuntimeError, AttributeError) as exc:
        log.exception("Failed to spawn watcher daemon for %s", root)
        _record_spawn_failure(db_dir, f"{type(exc).__name__}: {exc}")

# ── _is_bg_reindex_running (was at server.py:367) ──
def _is_bg_reindex_running(root: Path) -> bool:
    """Check whether any index process is running for *root*.

    Checks two signals:

    1. ``<db_dir>/<project_id>/reindex.pid`` — written by the daemon's
       index subprocess or a standalone ``fw-context index`` at startup,
       removed on exit.  A stale file (PID not alive) is cleaned up.
    2. ``<db_dir>/write.lock`` — held during write operations
       (``reindex_file``, ``reset_index``, index write phases).

    If either signal is active, another index process is active — skip
    launching a duplicate.

    Note: ``watcher.lock`` is NOT checked here — the persistent daemon
    holds it for its entire lifetime, even when idle (just watching for
    file changes).  Checking it was a false positive that made every
    ``get_active_build`` call report "reindexing" when the daemon was
    merely running.
    """
    db_path = _db_path(root)
    if not db_path.exists():
        return False

    def _lock_held(lock_file: Path) -> bool:
        """Probe a lock file without keeping a handle — boolean contract.

        The platform adapter normalizes contention (fcntl BlockingIOError,
        msvcrt OSError) to False; the probe never raises for "held".
        """
        try:
            handle = _platform_lock.open(lock_file)
        except OSError:
            return False
        try:
            if handle.try_lock():
                return False
            return True
        finally:
            handle.release()

    # 1. Index PID file — daemon subprocess or standalone fw-context index
    reindex_pid_file = db_path.parent / "reindex.pid"
    if PidFile.is_active(reindex_pid_file):
        return True

    # 2. General write lock — held during reindex_file, reset_index, etc.
    if _lock_held(db_path.parent / "write.lock"):
        return True

    return False


# ── read_reindex_progress ────────────────────────────────────────────────────
# How a fw-context line starts.  Three shapes, and nothing else writes them:
#
# * ``HH:MM:SS LEVEL `` — the logging format of a background index run,
#   ``cli/_index.py`` (``datefmt="%H:%M:%S"``).  ``--verbose`` picks the
#   framed VerboseFormatter instead, but the daemon never passes ``-v``.
# * ``fw-context: error: `` — a CLI diagnostic.
# * ``Project: `` — the header, printed before the first log record.
_REINDEX_RECORD_START = re.compile(
    r"^(?:\d{2}:\d{2}:\d{2} \w+ |fw-context: \w+: |Project: )"
)

# How much of the tail to read.  A record start must be inside it, and a
# failed build can quote up to 500 characters of another tool's stderr
# ahead of one — see the docstring below.  64 KiB holds hundreds of lines
# and keeps a multi-megabyte log off the query path.
_REINDEX_TAIL_BYTES = 65536


def read_reindex_progress(db_dir: Path) -> str | None:
    """Return the newest line the index run itself wrote to ``reindex.log``.

    Not the last line of the file.  ``daemon._run_index_async`` sends the
    stdout AND the stderr of ``fw-context index --background`` to that
    file, and a build runs inside that process, so the file also holds the
    output of west, cmake, ninja, and the shell that started them.  The
    last line is often one of theirs: a run in the Zephyr project ended on a
    bare ``lean-ctx:``, and both readers of this log — ``get_active_build``
    and ``fw-context watch status`` — reported that text as the progress.

    The error text of fw-context is not safe to take either.  When a build
    command fails, ``utils.run_build`` puts 500 characters of the tool's
    stderr into the message, so the record ends on a line that fw-context
    printed but did not write.

    So this reads backwards for the newest line that STARTS a fw-context
    record and gives that line.  After a failed build it is the
    ``fw-context: error:`` line, which names the failure, and not the
    quoted tool output below it.

    *db_dir* is the directory that holds ``index.db``.  Returns None when
    the file is absent, is empty, or holds no record start in its tail.
    """
    log_file = db_dir / "reindex.log"
    start = 0
    try:
        # Binary, and NOT text with a seek.  A text-mode seek to
        # ``size - N`` can land inside a UTF-8 sequence, and the read then
        # raises UnicodeDecodeError — which this function did not catch.
        # The log carries "→" and "—" in ordinary progress lines, so the
        # boundary is reachable.
        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return None
            start = max(0, size - _REINDEX_TAIL_BYTES)
            fh.seek(start)
            tail = fh.read()
    except OSError:
        return None

    lines = tail.decode("utf-8", errors="replace").splitlines()
    # A tail that begins mid-file begins mid-line.  Drop that fragment: a
    # cut line cannot be reported as a whole one, and its start is gone.
    if start > 0 and lines:
        del lines[0]
    for line in reversed(lines):
        if _REINDEX_RECORD_START.match(line):
            return line.strip()
    return None


# ── _ensure_daemon_running ───────────────────────────────────────────────────
def _ensure_daemon_running(root: Path) -> None:
    """Make sure the persistent watcher daemon is running for *root*.

    Uses *watcher.lock* (fcntl) as both daemon-alive indicator and spawn
    guard.  The daemon acquires the lock at startup; this function checks
    the ping first (fast path), then falls back to the lock for spawn
    coordination.
    """
    from .daemon import ping_daemon

    db_path = _db_path(root)
    if not db_path.exists():
        return
    db_dir = db_path.parent

    # Fast path — daemon is already running
    if ping_daemon(root):
        return

    # Slow path — daemon may be dead or starting up
    lock_file = db_dir / "watcher.lock"
    try:
        lock_handle = _platform_lock.open(lock_file)
    except OSError:
        return
    try:
        if not lock_handle.try_lock():
            # Lock held — daemon is running or another MCP server is spawning it
            return

        # We hold the lock — daemon is definitely dead (or never started).
        # Double-check ping in case of a race (daemon started between ping and lock).
        if ping_daemon(root):
            return

        # ── Spawn-before-release protocol ──
        # The daemon's main() blocks on LOCK_EX acquisition of watcher.lock.
        # We spawn the daemon WHILE holding our lock.  The daemon starts,
        # reaches its lock acquisition call, and blocks (because WE
        # hold the lock).  Then we release — the daemon immediately acquires
        # the lock and enters its main loop.
        #
        # Without this protocol, a second MCP server arriving between our
        # release and the daemon's acquisition would see the lock free,
        # conclude "daemon dead," and spawn a SECOND daemon — leading to
        # two daemons racing to run background reindexes.
        log.info("Spawning watcher daemon for %s", root)
        _spawn_daemon(root)
    finally:
        # Release in ALL paths: the pre-W1 code leaked the fd on the
        # ping race and the refusal path.
        lock_handle.release()

# ── _request_bg_reindex_pause (was at server.py:414) ──
def _request_bg_reindex_pause(root: Path) -> None:
    """Signal the background reindex to release the write lock.

    Writes ``<pid>`` to ``<db_dir>/<project_id>/reindex.pause`` so the
    bg reindex can check whether the requesting process is still alive.
    The bg process checks this marker between translation units — if the
    requesting PID is dead, it ignores the stale marker and continues.
    """
    db_path = _db_path(root)
    PidFile(db_path.parent / "reindex.pause").write()

# ── _resume_bg_reindex (was at server.py:430) ──
def _resume_bg_reindex(root: Path) -> None:
    """Remove the pause marker — the bg reindex may now resume.

    Only removes the marker when it was written by this process.
    A concurrent caller (e.g. ``fw-context index --force``) may have
    overwritten the marker with its own PID — in that case the marker
    must stay so the bg reindex remains paused for that caller.
    """
    db_path = _db_path(root)
    PidFile(db_path.parent / "reindex.pause").unlink_if_ours()


# ── bg_reindex_pause — context manager for safe pause/resume ──
@contextmanager
def bg_reindex_pause(root: Path):
    """Context manager: pause background reindex, auto-resume on exit.

    Safe for early returns, exceptions — resume always runs in finally.
    Usage::

        with bg_reindex_pause(root):
            ...  # critical section — bg reindex is paused
    """
    _request_bg_reindex_pause(root)
    try:
        yield
    finally:
        _resume_bg_reindex(root)

# ── _check_bg_pause (was at server.py:440) ──
def _check_bg_pause(root: Path) -> bool:
    """Check whether a pause was requested and the requester is still alive.

    Returns True if the bg reindex should pause (pause marker exists AND
    the requesting PID is still running).  Returns False if there is no
    pause marker or the requesting process has died (stale marker —
    cleaned up automatically).
    """
    db_path = _db_path(root)
    return PidFile.is_active(db_path.parent / "reindex.pause")

PENDING_MARKER = "reindex.pending"  # wave: pending marker file name


PENDING_MARKER = "reindex.pending"


def _is_bg_reindex_running(root: Path) -> bool:
    """Check whether any index process is running for *root*.

    Checks two signals:

    1. ``<db_dir>/<project_id>/reindex.pid`` — each ``fw-context index``
       run writes it when it holds the index lock
       (``cli._index._claim_index``), the run that the daemon started
       included, and removes it when it releases the lock.  A stale file
       (PID not alive) is cleaned up.
    2. ``<db_dir>/write.lock`` — held during write operations
       (``reindex_file``, ``reset_index``, index write phases).

    If either signal is active, another index process is active — skip
    launching a duplicate.

    Note: ``watcher.lock`` is NOT checked here — the persistent daemon
    holds it for its entire lifetime, even when idle (just watching for
    file changes).  Checking it was a false positive that made every
    ``get_project_status`` call report "reindexing" when the daemon was
    merely running.
    """
    db_path = _db_path(root)
    if not db_path.exists():
        return False

    def _lock_held(lock_file: Path) -> bool:
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        except OSError:
            return False
        if fcntl is None:  # Windows: no flock; marker-based state only
            os.close(lock_fd)
            return False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return True
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        return False

    # 1. Index PID file — daemon subprocess or standalone fw-context index
    reindex_pid_file = db_path.parent / "reindex.pid"
    if PidFile.is_active(reindex_pid_file):
        return True

    # 2. General write lock — held during reindex_file, reset_index, etc.
    if _lock_held(db_path.parent / "write.lock"):
        return True

    return False


def read_reindex_state(root: Path) -> str | None:
    """Return ``"reindexing"`` while an index run is active, ``"pending"`` while the daemon has work that is not complete, else None.

    WHY the two: an LLM that waits for a reindex must know that one comes.
    The daemon keeps its work as a flag in its memory, thus before this
    marker no other process saw a change that waited for the debounce or
    for a manual operation, and the answers said "run fw-context index"
    while the daemon was about to run it.  A run that holds the index
    makes "reindexing", also when the marker is there.
    """
    if _is_bg_reindex_running(root):
        return "reindexing"
    db_path = _db_path(root)
    if PidFile.is_active(db_path.parent / PENDING_MARKER):
        return "pending"
    return None
