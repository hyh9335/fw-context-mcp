"""POSIX backend — fcntl locks, /proc queries, AF_UNIX transport, signals.

Imported only by the facade on ``os.name == "posix"``.  Every adapter
mirrors the semantics the call sites had before W1, with the shared
contract from ``platform.base``.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import logging
import os
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path

from .base import (
    ControlListener,
    ProcessIdentity,
    PlatformError,
)

log = logging.getLogger(__name__)


# ── Lock (D3) ────────────────────────────────────────────────────────────────


class PosixLock:
    """``fcntl.flock``-based lock file.  Boolean try_lock contract.

    The lock file carries the holder pid as its content (the convention
    call sites already rely on for refusal messages).
    """

    def open(self, path: Path) -> "PosixLockHandle":
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        return PosixLockHandle(fd, path)


class PosixLockHandle:
    def __init__(self, fd: int, path: Path) -> None:
        self._fd = fd
        self._path = path
        self._locked = False
        self._closed = False

    def try_lock(self) -> bool:
        if self._closed:
            raise PlatformError(f"lock handle for {self._path} is closed")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            # BlockingIOError IS an OSError subclass for EAGAIN; the
            # remaining "contended" errnos exist on some kernels.
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, errno.EDEADLK):
                return False
            raise
        self._locked = True
        # Record the holder pid (message-only convention).  Best effort.
        try:
            os.ftruncate(self._fd, 0)
            os.write(self._fd, str(os.getpid()).encode("ascii"))
        except OSError:
            log.debug("could not record holder pid in %s", self._path, exc_info=True)
        return True

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._locked:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                log.debug("flock unlock failed on %s", self._path, exc_info=True)
            self._locked = False
        try:
            os.close(self._fd)
        except OSError:
            log.debug("lock fd close failed on %s", self._path, exc_info=True)

    def holder_pid(self) -> int | None:
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        try:
            return int(text)
        except ValueError:
            return None


# ── Process (D1/D2) ──────────────────────────────────────────────────────────


class PosixProcessService:
    """Liveness via ``os.kill(pid, 0)``; start-time token where /proc has one."""

    def is_alive(self, pid: int) -> bool:
        # POSIX three-outcome mapping (unchanged from pre-W1 pid_file.py):
        # ESRCH dead / EPERM alive / other OSError dead.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def identify(self, pid: int) -> ProcessIdentity | None:
        token = self._create_token(pid)
        if token is None and not self.is_alive(pid):
            return None
        return ProcessIdentity(pid=pid, create_token=token, role=None)

    def _create_token(self, pid: int) -> int | None:
        """Start-time token: Linux /proc/<pid>/stat field 22; else None.

        Field 22 is the starttime in clock ticks — monotonic per boot, so
        pid reuse changes it.  macOS has /proc/<pid> absent and no stable
        equivalent without private APIs: None (deny-kill direction).
        """
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return None
        # comm can contain spaces and parentheses: cut after the LAST ')'.
        try:
            tail = stat_text[stat_text.rindex(")") + 1:].split()
            return int(tail[19])  # field 22 overall = index 19 in the tail
        except (ValueError, IndexError):
            return None

    def terminate(
        self, pid: int, identity: ProcessIdentity, *, grace_s: float = 5.0
    ) -> bool:
        stored = identity.create_token
        if stored is None:
            log.warning(
                "refusing to terminate pid %d: marker carries no start-time token "
                "(legacy marker — coordination only, never kill-authorizing)",
                pid,
            )
            return False
        current = self.identify(pid)
        if current is None or current.create_token != stored:
            log.warning(
                "refusing to terminate pid %d: start-time token mismatch "
                "(stored %s, live %s) — likely pid reuse",
                pid, stored, current.create_token if current else "gone",
            )
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            log.warning("SIGTERM to pid %d failed: %s", pid, exc)
            return False
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not self.is_alive(pid):
                return True
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            log.warning("SIGKILL to pid %d failed: %s", pid, exc)
        return True

    def tree_kill(self, pid: int, *, grace_s: float = 2.0) -> bool:
        """Terminate *pid* and its descendants (AUD-R2 teardown service).

        The child was NOT started as its own process group (the caller's
        spawn is plain), and ``killpg`` is outside the sanctioned kill
        surface, so descendants are collected by reading the kernel
        parent-pid chain — on Linux from ``/proc/<pid>/stat`` field 4 —
        then SIGKILL is delivered per pid (root last).  This mirrors the
        win32 backend's in-process snapshot approach: no helper process
        is spawned.

        Semantics (mirrors ``platform/windows.py::tree_kill``):
        - True when the root was terminated, was already gone, or (being
          this process's own child) was reaped within *grace_s*;
        - False only when the kill call failed on a live root;
        - a SIGKILLed own child is reaped via ``waitpid(WNOHANG)`` — a
          zombie would otherwise count as alive to ``is_alive``;
        - macOS and other POSIX without /proc: direct child only
          (documented limitation; the caller's fallback stays correct).
        """
        if pid <= 0:
            return False
        for victim in self._descendants_of(pid):
            try:
                os.kill(victim, signal.SIGKILL)
            except OSError:
                pass  # gone between snapshot and delivery — fine
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True  # already dead — nothing to deliver
        except OSError as exc:
            log.warning("tree_kill: SIGKILL to pid %d failed: %s", pid, exc)
            return False
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            try:
                reaped, _status = os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                break  # not our child — liveness is the only judge
            if reaped == pid:
                return True  # reaped: definitively dead
            time.sleep(0.05)
        return not self.is_alive(pid)

    def _descendants_of(self, pid: int) -> list[int]:
        """Live pids whose /proc parent chain reaches *pid* (Linux only)."""
        try:
            stat_entries = os.listdir("/proc")
        except OSError:
            return []  # non-Linux POSIX: no descendant knowledge
        parent_of: dict[int, int] = {}
        for name in stat_entries:
            if not name.isdigit():
                continue
            try:
                stat_text = Path(f"/proc/{name}/stat").read_text(encoding="utf-8")
                tail = stat_text[stat_text.rindex(")") + 1:].split()
                parent_of[int(name)] = int(tail[1])  # field 4 = ppid
            except (OSError, ValueError, IndexError):
                continue
        children: dict[int, list[int]] = {}
        for child, parent in parent_of.items():
            children.setdefault(parent, []).append(child)
        found: list[int] = []
        frontier, seen = [pid], {pid}
        while frontier:
            current = frontier.pop()
            for child in children.get(current, ()):
                if child not in seen:      # cycle guard for stale entries
                    seen.add(child)
                    found.append(child)
                    frontier.append(child)
        return found

    def current_identity(self, role: str | None = None) -> ProcessIdentity:
        pid = os.getpid()
        return ProcessIdentity(
            pid=pid, create_token=self._create_token(pid), role=role,
            recorded_at=time.time(),
        )

    def command_line(self, pid: int) -> list[str] | None:
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        argv = [arg for arg in raw.decode("utf-8", errors="replace").split("\0") if arg]
        return argv or None


# ── Filesystem (D5) ──────────────────────────────────────────────────────────


class PosixFileSystem:
    def is_reparse_point(self, path: Path) -> bool:
        try:
            return stat.S_ISLNK(path.lstat().st_mode)
        except OSError:
            return False

    def safe_open_log(self, path: Path, *, append: bool = True) -> int | None:
        """Open with O_NOFOLLOW — a symlink at *path* fails with ELOOP.

        A refusal is VISIBLE (logged at warning level), never silent:
        the caller decides the fallback (devnull or recorded spawn failure).
        """
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
        try:
            return os.open(str(path), flags | os.O_NOFOLLOW, 0o644)
        except OSError as exc:
            log.warning(
                "refusing to open log path %s (O_NOFOLLOW): %s — "
                "falling back to the caller's catch-path",
                path, exc,
            )
            return None

    def safe_create_exclusive(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

    def symlinks_supported(self) -> bool:
        return True

    def native_separators(self, path: str) -> str:
        return path  # '/' is native on POSIX

    def fold_path_case(self, path: str) -> str:
        return path  # case-sensitive filesystems: no folding


# ── ControlTransport (D4) — AF_UNIX, unchanged behavior ─────────────────────


class PosixControlTransport:
    async def listen(
        self, db_dir: Path, on_ping
    ) -> ControlListener:
        sock_path = db_dir / "daemon.sock"
        sock_path.unlink(missing_ok=True)

        async def _handle_ping(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                if b"ping" in data:
                    on_ping()
            except (asyncio.TimeoutError, ConnectionError):
                pass
            finally:
                writer.close()

        try:
            server = await asyncio.start_unix_server(_handle_ping, path=str(sock_path))
        except OSError:
            log.exception(
                "Failed to bind control socket at %s", sock_path
            )
            raise
        try:
            os.chmod(sock_path, 0o600)
        except OSError:
            log.debug("chmod of socket %s failed", sock_path, exc_info=True)
        log.info("Daemon control socket listening at %s", sock_path)
        return ControlListener(
            endpoint=str(sock_path),
            _close=server.close,
            _wait_closed=server.wait_closed,
        )

    def ping(self, db_dir: Path, timeout: float = 2.0) -> bool:
        sock_path = db_dir / "daemon.sock"
        if not sock_path.exists():
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(str(sock_path))
            sock.sendall(b"ping\n")
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def cleanup(self, db_dir: Path) -> None:
        (db_dir / "daemon.sock").unlink(missing_ok=True)
        (db_dir / "daemon.port").unlink(missing_ok=True)

    def artifact_path(self, db_dir: Path) -> Path:
        return db_dir / "daemon.sock"
