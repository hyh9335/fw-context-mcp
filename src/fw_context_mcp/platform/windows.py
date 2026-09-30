"""Windows backend — msvcrt locks, win32 process APIs, TCP-loopback transport.

Imported only by the facade on ``os.name == "nt"``.  Semantics mirror the
POSIX backend through the shared contract in ``platform.base``.

Key differences handled here:

- locks: ``msvcrt.locking`` byte-range locks, ``try_lock`` returning
  boolean (contention arrives as plain ``OSError``);
- liveness: ``OpenProcess`` + ``GetExitCodeProcess`` — never
  ``os.kill``/console events (pid 0 semantics and CTRL_C delivery);
- start-time token: creation FILETIME from ``GetProcessTimes``;
- termination: ``TerminateProcess`` (this is the win32 SIGKILL; win32
  has no SIGKILL attribute, which is the latent bug W1 removes);
- argv: PEB read (x64) so background-run recognition works without /proc.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import logging
import os
import socket
import stat
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from .base import (
    ControlListener,
    ProcessIdentity,
    PlatformError,
)

log = logging.getLogger(__name__)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_shell32 = ctypes.WinDLL("shell32")

_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
)
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.ReadProcessMemory.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_ntdll.NtQueryInformationProcess.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
)
_ntdll.NtQueryInformationProcess.restype = wintypes.LONG
_shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
_shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
_kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
_kernel32.LocalFree.restype = ctypes.c_void_p

# Toolhelp32 snapshot — used ONLY by ProcessService.tree_kill (AUD-R2):
# the sanctioned tree teardown walks the kernel parent-pid chain in
# process, so no external killer binary needs to be spawned (spawning
# ANY child from a live server can stall tens of seconds at image load
# on hardened hosts — the stall that motivated this service).
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
_kernel32.Process32NextW.restype = wintypes.BOOL

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87   # Windows' answer for a pid that does not exist
_WAIT_OBJECT_0 = 0

# Contended-lock errnos from msvcrt.locking: EACCES (region locked),
# EDEADLK, EAGAIN/EWOULDBLOCK.  msvcrt raises plain OSError, NOT
# BlockingIOError — the adapter normalizes all of them to False.
_CONTENTION_ERRNOS = frozenset(
    {errno.EACCES, errno.EDEADLK, errno.EAGAIN, errno.EWOULDBLOCK}
)


def _open_process(pid: int, access: int) -> int:
    # argtypes handle the ctypes conversion; plain ints keep stubs in tests
    # byte-identical to the values this layer chose.
    return _kernel32.OpenProcess(access, False, pid) or 0


def _close_handle(handle: int) -> None:
    _kernel32.CloseHandle(handle)


def _creation_filetime(handle: int) -> int | None:
    ft_create, ft_exit, ft_kernel, ft_user = (
        wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(),
    )
    if not _kernel32.GetProcessTimes(
        handle,
        ctypes.byref(ft_create), ctypes.byref(ft_exit),
        ctypes.byref(ft_kernel), ctypes.byref(ft_user),
    ):
        return None
    return (ft_create.dwHighDateTime << 32) | ft_create.dwLowDateTime


# ── Lock (D3) ────────────────────────────────────────────────────────────────


class WindowsLock:
    """``msvcrt.locking`` byte-range lock file.  Boolean try_lock contract.

    Layout convention: byte 0 is the locked marker; the holder pid is
    written at offset 8 so other handles can READ it — byte-range locks
    block reads of the locked region from any other handle.
    """

    def open(self, path: Path) -> "WindowsLockHandle":
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o644
        )
        return WindowsLockHandle(fd, path)


class WindowsLockHandle:
    _PID_OFFSET = 8

    def __init__(self, fd: int, path: Path) -> None:
        self._fd = fd
        self._path = path
        self._locked = False
        self._closed = False

    def try_lock(self) -> bool:
        if self._closed:
            raise PlatformError(f"lock handle for {self._path} is closed")
        os.lseek(self._fd, 0, os.SEEK_SET)
        try:
            import msvcrt

            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in _CONTENTION_ERRNOS:
                return False
            raise
        self._locked = True
        # Record the holder pid past the locked byte (message-only).
        try:
            os.lseek(self._fd, self._PID_OFFSET, os.SEEK_SET)
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
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                log.debug("locking unlock failed on %s", self._path, exc_info=True)
            self._locked = False
        try:
            os.close(self._fd)
        except OSError:
            log.debug("lock fd close failed on %s", self._path, exc_info=True)

    def holder_pid(self) -> int | None:
        try:
            fd = os.open(str(self._path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            return None
        try:
            os.lseek(fd, self._PID_OFFSET, os.SEEK_SET)
            data = os.read(fd, 64)
        except OSError:
            return None
        finally:
            os.close(fd)
        try:
            return int(data.decode("ascii", errors="replace").strip())
        except ValueError:
            return None


# ── Process (D1/D2) ──────────────────────────────────────────────────────────


class WindowsProcessService:
    def is_alive(self, pid: int) -> bool:
        # NEVER os.kill on win32 — GenerateConsoleCtrlEvent would deliver
        # CTRL_C to the pid's console process group and never raises for
        # dead-but-registered pids.  Pure handle query.
        if pid <= 0:
            return False
        handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            err = ctypes.get_last_error()
            if err == _ERROR_ACCESS_DENIED:
                return True    # exists, cannot query — mirrors POSIX EPERM
            return False       # ERROR_INVALID_PARAMETER = no such pid; else gone
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True    # conservative: query failure must not read "dead"
            return code.value == _STILL_ACTIVE
        finally:
            _close_handle(handle)

    def identify(self, pid: int) -> ProcessIdentity | None:
        if pid <= 0:
            return None
        handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            return None
        try:
            token = _creation_filetime(handle)
            if token is None:
                return None
            return ProcessIdentity(pid=pid, create_token=token, role=None)
        finally:
            _close_handle(handle)

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
        handle = _open_process(pid, _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            log.warning("OpenProcess(PROCESS_TERMINATE) for pid %d failed", pid)
            return False
        try:
            if not _kernel32.TerminateProcess(handle, 1):
                log.warning(
                    "TerminateProcess for pid %d failed: %s",
                    pid, ctypes.WinError(ctypes.get_last_error()),
                )
                return False
            # Give the child its grace window so a killed run's lock really
            # drops before the caller proceeds (kernel frees on death).
            _kernel32.WaitForSingleObject(handle, int(grace_s * 1000))
            return True
        finally:
            _close_handle(handle)

    def tree_kill(self, pid: int, *, grace_s: float = 2.0) -> bool:
        """Terminate *pid* and every descendant (kernel parent-pid tree).

        AUD-R2 teardown service for processes this process spawned itself.
        Implemented entirely IN PROCESS: a Toolhelp32 snapshot walks the
        parent chain, then TerminateProcess (the sanctioned kill in this
        file) is applied to the root first and each collected descendant
        after.  Spawning an external killer binary was considered and
        rejected: on the AUD-R2 validation host, ANY child process
        spawned from a live stdio server could stall tens of seconds at
        image load — an external killer would reintroduce exactly the
        stall this service exists to bound.

        Semantics:
        - returns True when the root was terminated, or was already gone
          while the sweep still ran (nothing left to terminate);
        - returns False only when the root EXISTS but could not be opened
          for termination (access denied);
        - descendants are a best-effort snapshot (TOCTOU on freshly
          spawned grandchildren accepted — the caller's pid is a live
          ``Popen`` handle held for at most the probe timeout);
        - the bounded ``WaitForSingleObject`` on the root lets its pipe
          write ends close before the caller gives up on the pipes.
        """
        if pid <= 0:
            return False
        descendants = self._descendants_of(pid)
        root = _open_process(pid, _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION)
        root_gone = root is None and ctypes.get_last_error() != _ERROR_ACCESS_DENIED
        killed_root = False
        if root:
            killed_root = bool(_kernel32.TerminateProcess(root, 1))
            if not killed_root:
                log.warning(
                    "tree_kill: TerminateProcess for pid %d failed: %s",
                    pid, ctypes.WinError(ctypes.get_last_error()),
                )
        for child_pid in descendants:
            handle = _open_process(child_pid, _PROCESS_TERMINATE)
            if handle:
                _kernel32.TerminateProcess(handle, 1)
                _close_handle(handle)
        if root:
            try:
                if killed_root and grace_s > 0:
                    _kernel32.WaitForSingleObject(root, int(grace_s * 1000))
            finally:
                _close_handle(root)
        if root is None and not root_gone:
            log.warning(
                "tree_kill: OpenProcess(PROCESS_TERMINATE) for pid %d denied", pid
            )
            return False
        return killed_root or root_gone

    def _descendants_of(self, pid: int) -> list[int]:
        """Live pids whose kernel parent chain reaches *pid* (one snapshot)."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
            return []
        parent_of: dict[int, int] = {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                parent_of[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)
        children: dict[int, list[int]] = {}
        for child, parent in parent_of.items():
            children.setdefault(parent, []).append(child)
        found: list[int] = []
        frontier, seen = [pid], {pid}
        while frontier:
            current = frontier.pop()
            for child in children.get(current, ()):
                if child not in seen:      # cycle guard for corrupt chains
                    seen.add(child)
                    found.append(child)
                    frontier.append(child)
        return found

    def current_identity(self, role: str | None = None) -> ProcessIdentity:
        pseudo = _kernel32.GetCurrentProcess()
        token = _creation_filetime(pseudo)
        return ProcessIdentity(
            pid=os.getpid(), create_token=token, role=role, recorded_at=time.time(),
        )

    def command_line(self, pid: int) -> list[str] | None:
        """The process argv, via its PEB (x64).  None on any failure.

        Reading the target's PEB is read-only (PROCESS_VM_READ).  Any
        failure — bitness mismatch, protected process, gone — returns
        None, and None means "do not claim this is a reindexer".
        """
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            return None  # offsets below are x64-only; deny rather than guess
        if pid <= 0:
            return None
        handle = _open_process(pid, _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ)
        if not handle:
            return None

        class _PBI(ctypes.Structure):
            _fields_ = [
                ("ExitStatus", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p),
                ("BasePriority", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
            ]

        try:
            pbi = _PBI()
            ret = wintypes.ULONG()
            status = _ntdll.NtQueryInformationProcess(
                handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret)
            )
            if status != 0 or not pbi.PebBaseAddress:
                return None

            def _read(addr: int, size: int) -> bytes | None:
                buf = ctypes.create_string_buffer(size)
                got = ctypes.c_size_t()
                if not _kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)
                ):
                    return None
                return buf.raw[: got.value]

            # PEB x64: +0x20 = ProcessParameters (pointer)
            raw = _read(pbi.PebBaseAddress + 0x20, 8)
            if raw is None or len(raw) < 8:
                return None
            params = int.from_bytes(raw, "little")
            if not params:
                return None
            # RTL_USER_PROCESS_PARAMETERS x64: +0x70 = UNICODE_STRING CommandLine
            us_raw = _read(params + 0x70, 16)
            if us_raw is None or len(us_raw) < 16:
                return None
            length = int.from_bytes(us_raw[0:2], "little")
            buffer_ptr = int.from_bytes(us_raw[8:16], "little")
            if not length or not buffer_ptr:
                return None
            data = _read(buffer_ptr, length)
            if data is None:
                return None
            cmdline = data.decode("utf-16-le", errors="replace")
            argc = ctypes.c_int()
            argv_ptr = _shell32.CommandLineToArgvW(cmdline, ctypes.byref(argc))
            if not argv_ptr:
                return [cmdline] if cmdline else None
            try:
                return [argv_ptr[i] for i in range(argc.value)]
            finally:
                _kernel32.LocalFree(argv_ptr)
        except OSError:
            return None
        finally:
            _close_handle(handle)


# ── Filesystem (D5) ──────────────────────────────────────────────────────────


class WindowsFileSystem:
    def is_reparse_point(self, path: Path) -> bool:
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except OSError:
            return False

    def safe_open_log(self, path: Path, *, append: bool = True) -> int | None:
        """Refuse a reparse point at *path*; otherwise open for writing.

        win32 has no ``O_NOFOLLOW`` (AttributeError is the pre-W1 silent
        spawn killer); lstat-based refusal covers symlinks and any other
        reparse target.  The refusal is logged — never silent.
        """
        if self.is_reparse_point(path):
            log.warning(
                "refusing to open log path %s: it is a reparse point "
                "(symlink/junction) — falling back to the caller's catch-path",
                path,
            )
            return None
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
        try:
            return os.open(str(path), flags, 0o644)
        except OSError as exc:
            log.warning(
                "refusing to open log path %s: %s — falling back to the "
                "caller's catch-path",
                path, exc,
            )
            return None

    def safe_create_exclusive(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.is_reparse_point(path):
            raise OSError(
                errno.EPERM, f"refusing to create {path}: reparse point at path"
            )
        return os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

    def symlinks_supported(self) -> bool:
        return False  # symlink creation needs privileges; treat as unsupported

    def native_separators(self, path: str) -> str:
        return path.replace("/", "\\")

    def fold_path_case(self, path: str) -> str:
        return path.casefold()


# ── ControlTransport (D4) — TCP loopback + daemon.port file ────────────────


class WindowsControlTransport:
    async def listen(
        self, db_dir: Path, on_ping
    ) -> ControlListener:
        db_dir.mkdir(parents=True, exist_ok=True)

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
            server = await asyncio.start_server(
                _handle_ping, host="127.0.0.1", port=0
            )
        except OSError:
            log.exception("Failed to bind control TCP listener on 127.0.0.1:0")
            raise
        port = server.sockets[0].getsockname()[1]
        port_file = db_dir / "daemon.port"
        port_file.write_text(str(port), encoding="utf-8")
        log.info("Daemon control TCP listener on 127.0.0.1:%d (daemon.port)", port)
        return ControlListener(
            endpoint=f"tcp://127.0.0.1:{port}",
            _close=server.close,
            _wait_closed=server.wait_closed,
        )

    def ping(self, db_dir: Path, timeout: float = 2.0) -> bool:
        port_file = db_dir / "daemon.port"
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
                sock.sendall(b"ping\n")
            return True
        except OSError:
            return False

    def cleanup(self, db_dir: Path) -> None:
        (db_dir / "daemon.port").unlink(missing_ok=True)
        (db_dir / "daemon.sock").unlink(missing_ok=True)

    def artifact_path(self, db_dir: Path) -> Path:
        return db_dir / "daemon.port"
