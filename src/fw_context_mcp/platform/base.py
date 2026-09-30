"""Platform abstraction — base protocols, dataclasses, errors, shared helpers.

The package exists so business code never asks the operating system what
it is running on.  ``platform/__init__.py`` resolves the backend ONCE at
import and re-exports singletons; every consumer imports from the facade
and only ever sees this surface (D1 of PLATFORM_ADR).

This module is import-safe on every platform: it holds no ``fcntl``,
``msvcrt`` or ``ctypes`` imports.  Heavy third-party imports (httpx) are
lazy so a CLI invocation that never touches the network does not pay for
them (the CLI defers heavy imports until subcommand selection).
"""

from __future__ import annotations

import enum
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "ControlListener",
    "ControlTransport",
    "FileSystemCapabilities",
    "InterProcessLock",
    "LockHandle",
    "ProcessIdentity",
    "ProcessService",
    "PlatformError",
    "ROLE_BACKGROUND_INDEX",
    "ROLE_FOREGROUND_INDEX",
    "ROLE_WATCHER_DAEMON",
    "StopOutcome",
    "configure_stdio",
    "http_client",
    "http_get",
    "http_post",
    "read_pid_marker",
    "spawn_detach_kwargs",
    "stop_marker_process",
    "subprocess_env",
    "write_pid_marker",
]

log = logging.getLogger(__name__)


# ── Errors ───────────────────────────────────────────────────────────────────


class PlatformError(RuntimeError):
    """A platform adapter could not carry out an operation."""


# ── Process identity (D2) ────────────────────────────────────────────────────

#: Marker roles.  ``background-index`` and ``watcher-daemon`` are the two
#: kill-authorizing roles; ``foreground-index`` belongs to a person and is
#: never killed to make way for another run.
ROLE_WATCHER_DAEMON = "watcher-daemon"
ROLE_BACKGROUND_INDEX = "background-index"
ROLE_FOREGROUND_INDEX = "foreground-index"


@dataclass(frozen=True)
class ProcessIdentity:
    """Who a (recorded or live) process is, for kill authorization.

    ``create_token`` is the process START-TIME token: win32 creation
    FILETIME (``GetProcessTimes``), Linux ``/proc/<pid>/stat`` field 22,
    ``None`` where the platform cannot provide one.  A marker whose token
    does not match the live process identifies a REUSED pid and must
    never authorize a kill.  ``create_token is None`` (legacy bare-int
    markers) is coordination-only: alive checks yes, kills never.
    """

    pid: int
    create_token: int | None = None
    role: str | None = None
    recorded_at: float | None = None

    def with_pid(self, pid: int) -> "ProcessIdentity":
        return replace(self, pid=pid)


# ── JSON pid markers (D2 marker upgrade) ─────────────────────────────────────


def write_pid_marker(path: Path, identity: ProcessIdentity) -> None:
    """Write a JSON pid marker ``{pid, create_token, role, recorded_at}``.

    The caller is responsible for any staleness handling around it (e.g.
    unlink-and-retry on ``FileExistsError``).
    """
    payload = {
        "pid": identity.pid,
        "create_token": identity.create_token,
        "role": identity.role,
        "recorded_at": identity.recorded_at,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def read_pid_marker(path: Path) -> ProcessIdentity | None:
    """Read a pid marker in EITHER format.

    Returns ``None`` for a missing file.  Legacy bare-int content reads
    back as an identity with ``create_token=None`` — coordination-only
    (alive checks), never kill-authorizing.  A JSON payload that is not
    an object, or that lacks an integer ``pid``, reads back as ``None``
    (garbage — the caller decides what cleanup applies).
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        pid = payload.get("pid")
        if not isinstance(pid, int):
            return None
        token = payload.get("create_token")
        role = payload.get("role")
        recorded_at = payload.get("recorded_at")
        return ProcessIdentity(
            pid=pid,
            create_token=token if isinstance(token, int) else None,
            role=role if isinstance(role, str) else None,
            recorded_at=recorded_at if isinstance(recorded_at, (int, float)) else None,
        )
    # Legacy bare-int content.
    try:
        return ProcessIdentity(pid=int(text))
    except ValueError:
        return None


def marker_pid(path: Path) -> int | None:
    """The pid of a marker in either format, or ``None`` when unreadable."""
    identity = read_pid_marker(path)
    return identity.pid if identity is not None else None


# ── Shared marker-stop sequence (D2, W5) ─────────────────────────────────────


class StopOutcome(enum.Enum):
    """Outcome of :func:`stop_marker_process`.

    ``GONE`` — no marker (or unreadable garbage): nothing to stop.
    ``REFUSED`` — a marker names a live or dead target, but authorization
    failed: NO signal of any kind was sent.  Refusal reasons: the target
    is this process or its parent, a legacy bare-int marker (no token),
    a marker role outside ``allowed_roles``, a call-site ``authorize``
    predicate, or a start-time token mismatch / dead target reported by
    ``ProcessService.terminate`` (which logs both identities).
    ``STOPPED`` — the terminate was carried out through the service.
    """

    GONE = "gone"
    REFUSED = "refused"
    STOPPED = "stopped"


def stop_marker_process(
    path: Path,
    *,
    service: ProcessService,
    grace_s: float = 5.0,
    allowed_roles: tuple[str, ...] | None = None,
    authorize: Callable[[ProcessIdentity], bool] | None = None,
    caller_pid: int | None = None,
    caller_ppid: int | None = None,
) -> StopOutcome:
    """The ONE marker-named kill sequence: read → verify → terminate → wait.

    Every kill of a process known only through a pid marker MUST run
    through this helper (D2/W5) — never inline a ``read_pid_marker`` +
    ``terminate`` pair again.  Authorization is the start-time identity:
    ``create_token`` ``None`` (legacy bare-int markers) and token
    mismatches (pid reuse) are refused inside the ``ProcessService``,
    which logs both identities on refusal.

    Extra refusals, each logged and each sending no signal:

    - *path* names THIS process (``caller_pid``, default ``os.getpid()``)
      or its parent (``caller_ppid``, default ``os.getppid()``) — a kill
      path never signals up its own process tree.  Pass an impossible
      pid (e.g. ``-1``) to disable a check.
    - the marker's ``role`` is not in *allowed_roles* (when given).  Only
      attach this where the marker file has exactly one legitimate
      writer (``watcher.pid`` → ``watcher-daemon``); ``reindex.pid`` is
      written by both background and foreground index runs and must stay
      role-agnostic — its narrowing policy is the *authorize* argv gate.
    - *authorize* (call-site policy, e.g. the background-argv
      recognition) rejects the marker's identity.

    ``service`` is explicit (the facade singleton) so tests can exercise
    the sequence against the real backend or a recording stub without
    re-resolving the platform.  ``grace_s`` forwards to ``terminate``.
    """
    marker = read_pid_marker(path)
    if marker is None:
        return StopOutcome.GONE
    if marker.pid <= 0:
        log.warning(
            "refusing to stop garbage marker %s (pid %r)", path, marker.pid,
        )
        return StopOutcome.REFUSED
    own_pid = os.getpid() if caller_pid is None else caller_pid
    if marker.pid == own_pid:
        log.warning(
            "refusing to stop pid %d from %s: it is THIS process",
            marker.pid, path,
        )
        return StopOutcome.REFUSED
    parent_pid = os.getppid() if caller_ppid is None else caller_ppid
    if marker.pid == parent_pid:
        log.warning(
            "refusing to stop pid %d from %s: it is THIS process's parent",
            marker.pid, path,
        )
        return StopOutcome.REFUSED
    if marker.create_token is None:
        log.warning(
            "refusing to stop pid %d from %s: legacy bare-pid marker — "
            "coordination only, never kill-authorizing",
            marker.pid, path,
        )
        return StopOutcome.REFUSED
    if allowed_roles is not None and marker.role not in allowed_roles:
        log.warning(
            "refusing to stop pid %d from %s: marker role %r is not one of "
            "%s — the marker's writer does not match this kill path",
            marker.pid, path, marker.role, sorted(allowed_roles),
        )
        return StopOutcome.REFUSED
    if authorize is not None and not authorize(marker):
        log.debug(
            "pid %d from %s rejected by call-site authorization policy — "
            "ignoring",
            marker.pid, path,
        )
        return StopOutcome.REFUSED
    if service.terminate(marker.pid, marker, grace_s=grace_s):
        return StopOutcome.STOPPED
    # terminate logged the reason (token mismatch with both identities,
    # dead target, or failed kill call).
    return StopOutcome.REFUSED


# ── ProcessService (D1/D2) ───────────────────────────────────────────────────


@runtime_checkable
class ProcessService(Protocol):
    """Process queries and identity-checked termination."""

    def is_alive(self, pid: int) -> bool: ...

    def identify(self, pid: int) -> ProcessIdentity | None: ...

    def terminate(
        self, pid: int, identity: ProcessIdentity, *, grace_s: float = 5.0
    ) -> bool: ...

    def tree_kill(self, pid: int, *, grace_s: float = 2.0) -> bool:
        """Terminate *pid* AND every process below it in the parent tree.

        Sanctioned kill service for teardown of processes this process
        SPAWNED ITSELF (its own ``Popen`` pids — e.g. a timed-out helper
        subprocess).  It must NEVER be aimed at marker/documented pids:
        unlike ``terminate`` there is no start-time token check, because
        the caller holds the child object and the kill happens within
        seconds of the spawn.  Descendant collection is a best-effort
        snapshot walk; the root's death is the contract's success
        criterion.  Backends implement this WITHOUT spawning helper
        processes (win32: Toolhelp32 snapshot + TerminateProcess in
        ``platform/windows.py``; POSIX: /proc descendant walk + SIGKILL),
        because spawning an external killer from a live server can stall
        at image load on hardened hosts (AUD-R2 evidence).
        """
        ...

    def current_identity(self, role: str | None = None) -> ProcessIdentity: ...

    def command_line(self, pid: int) -> list[str] | None: ...


# ── Interprocess lock (D3) ───────────────────────────────────────────────────


@runtime_checkable
class LockHandle(Protocol):
    """One open lock file.  ``try_lock`` is the WHOLE contract: boolean.

    msvcrt.locking signals contention with plain ``OSError``
    (EACCES/EDEADLK) while fcntl.flock raises ``BlockingIOError`` — a call
    site branching on the exception type works on exactly one platform.
    The adapter normalizes every "locked" indication to ``False``.
    """

    def try_lock(self) -> bool: ...

    def release(self) -> None:
        """Unlock when held, close the handle.  Idempotent."""

    def holder_pid(self) -> int | None:
        """The pid recorded in the lock file, message-only."""


@runtime_checkable
class InterProcessLock(Protocol):
    def open(self, path: Path) -> LockHandle: ...


# ── Filesystem capabilities (D5) ─────────────────────────────────────────────


@runtime_checkable
class FileSystemCapabilities(Protocol):
    def is_reparse_point(self, path: Path) -> bool: ...

    def safe_open_log(self, path: Path, *, append: bool = True) -> int | None:
        """Open a log file for writing without following symlinks.

        POSIX opens with ``O_NOFOLLOW`` (a symlink at *path* fails with
        ELOOP); win32 refuses when ``lstat`` reports a reparse point.
        Returns the fd, or ``None`` when the open was REFUSED — the
        caller falls back per its own policy (daemon log → devnull,
        spawn → recorded failure).
        """

    def safe_create_exclusive(self, path: Path) -> int:
        """Create *path* exclusively (``O_EXCL``) and return the fd.

        Raises ``FileExistsError`` when the file exists and ``OSError``
        on any other failure.  On win32 a symlink at *path* is refused.
        """

    def symlinks_supported(self) -> bool: ...

    def native_separators(self, path: str) -> str:
        """Convert forward slashes to the OS-native separator (win32)."""

    def fold_path_case(self, path: str) -> str:
        """Case-fold path keys on case-insensitive filesystems."""


# ── ControlTransport (D4) ────────────────────────────────────────────────────


@dataclass
class ControlListener:
    """A live control endpoint: close it, wait for it to drain."""

    endpoint: str
    _close: Callable[[], None]
    _wait_closed: Callable[[], Awaitable[None]]

    def close(self) -> None:
        self._close()

    async def wait_closed(self) -> None:
        await self._wait_closed()


@runtime_checkable
class ControlTransport(Protocol):
    """Daemon control endpoint: AF_UNIX on POSIX, TCP loopback on win32.

    The daemon binds via :meth:`listen` and answers ``b"ping\\n"``
    connections by calling *on_ping*; clients probe via :meth:`ping`.
    Endpoint artifacts (``daemon.sock`` / ``daemon.port``) live in the
    project's db dir; only the ``watcher.lock`` winner ever binds, so two
    daemons cannot race for the same endpoint.
    """

    async def listen(
        self, db_dir: Path, on_ping: Callable[[], None]
    ) -> ControlListener: ...

    def ping(self, db_dir: Path, timeout: float = 2.0) -> bool: ...

    def cleanup(self, db_dir: Path) -> None: ...

    def artifact_path(self, db_dir: Path) -> Path: ...


# ── Shared helpers ───────────────────────────────────────────────────────────


def subprocess_env() -> dict[str, str]:
    """Environment for repo-internal subprocess spawns (D6.2).

    Every spawn the repo issues inherits a UTF-8-pinned interpreter so
    child output never depends on the parent console's codepage.
    """
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return env


def spawn_detach_kwargs() -> dict:
    """Popen kwargs that detach a long-lived child from the caller (D4).

    POSIX detaches via a new session (``start_new_session``); win32 needs
    creation flags — ``start_new_session`` is a POSIX parameter and the
    pre-W1 daemon spawn never reached ``Popen`` on Windows to find out.
    """
    if os.name == "nt":
        import subprocess

        return {
            "creationflags": subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
    return {"start_new_session": True}


def configure_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 with replacement (D6.1).

    Entry points call this so a GBK/CP1252 console cannot crash a UTF-8
    message.  On Windows consoles ``reconfigure`` can raise
    ``io.UnsupportedOperation`` (a subclass of OSError and ValueError) —
    caught and ignored; ``PYTHONUTF8=1`` remains the load-bearing flag.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            log.debug("stdio reconfigure failed for %r", stream, exc_info=True)


# ── httpx client factory (D6 / NEW-W0-1) ─────────────────────────────────────

#: Every environment variable httpx 0.28.1 consults for proxies.  Entries
#: are matched case-sensitively here; ``getproxies_environment`` lowercases
#: names itself, so both spellings must be sanitized.
_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def sanitize_proxy_env() -> list[str]:
    """Drop proxy-env entries httpx 0.28.1 cannot parse (NEW-W0-1).

    Measured on httpx 0.28.1: a bracketed IPv6 entry in NO_PROXY
    (``[::1]``) raises ``httpx.InvalidURL: Invalid port ':1]'`` at Client
    construction.  Bracket-carrying NO_PROXY entries are removed from the
    environment (process-wide, so later constructions — including httpx's
    own module-level helpers — are safe); every other entry survives.
    Returns the dropped ``VAR=value`` descriptions for logging.
    """
    dropped: list[str] = []
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if not value:
            continue
        entries = [e.strip() for e in value.split(",") if e.strip()]
        kept = [e for e in entries if "[" not in e and "]" not in e]
        if kept != entries:
            dropped.append(f"{var}={value!r}")
            if kept:
                os.environ[var] = ",".join(kept)
            else:
                os.environ.pop(var, None)
    if dropped:
        log.warning(
            "Removed proxy-env entries httpx 0.28.1 cannot parse (bracketed IPv6): %s",
            "; ".join(dropped),
        )
    return dropped


def http_client(**kwargs):
    """Construct an httpx.Client with a sanitized proxy environment (D6).

    ONE construction point for every client the repo creates.  On a parse
    failure that survived sanitization, retries once with
    ``trust_env=False`` and logs the offending variable — a last resort,
    never silent.
    """
    import httpx

    sanitize_proxy_env()
    try:
        return httpx.Client(**kwargs)
    except (httpx.InvalidURL, ValueError) as exc:
        log.warning(
            "httpx.Client construction failed (%s); retrying with trust_env=False "
            "— environment proxies are ignored for this client",
            exc,
        )
        return httpx.Client(trust_env=False, **kwargs)


def http_get(url: str, **request_kwargs):
    """One-shot GET through a factory client — parity with ``httpx.get``."""
    with http_client() as client:
        return client.get(url, **request_kwargs)


def http_post(url: str, **request_kwargs):
    """One-shot POST through a factory client — parity with ``httpx.post``."""
    with http_client() as client:
        return client.post(url, **request_kwargs)


@contextmanager
def http_stream(method: str, url: str, **request_kwargs):
    """Stream a request through a factory client — parity with ``httpx.stream``.

    W1-1 follow-up (NEW-W1-1): six ``httpx.stream(...)`` call sites bypassed
    the factory, constructing their client against the ambient (hostile)
    proxy environment.  This is the same construction chain as
    :func:`http_client` — sanitize, construct, retry once with
    ``trust_env=False`` on a parse failure that survived sanitization —
    followed by ``client.stream(method, url, **kwargs)``.

    Wire semantics are preserved by construction: ``httpx.stream(method,
    url, **kwargs)`` IS ``httpx.Client().stream(method, url, **kwargs)``;
    timeouts, headers and bodies pass through as keyword arguments.  The
    trust_env retry fires ONLY at client construction — before any request
    is built — so a streamed request can never be replayed by the fallback.
    """
    import httpx

    sanitize_proxy_env()
    try:
        client = httpx.Client()
    except (httpx.InvalidURL, ValueError) as exc:
        log.warning(
            "httpx.Client construction failed (%s); retrying with trust_env=False "
            "— environment proxies are ignored for this stream",
            exc,
        )
        client = httpx.Client(trust_env=False)
    try:
        with client.stream(method, url, **request_kwargs) as response:
            yield response
    finally:
        client.close()


def _now() -> float:
    return time.time()
