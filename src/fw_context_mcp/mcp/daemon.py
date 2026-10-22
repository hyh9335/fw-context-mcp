"""Per-project background daemon: watches source files, runs ``fw-context index`` on
changes, exits when all MCP clients disconnect.

One daemon per project, shared by all MCP server instances over that project.
Communication is via a Unix domain socket in ``<db_dir>/<project_id>/daemon.sock``.

Lifecycle
---------
1. First MCP server spawns the daemon (via ``_ensure_daemon_running``).
2. Each MCP server pings the daemon every 15 s over the socket.
3. If no ping arrives for 60 s, the daemon exits (all clients gone).
4. SIGTERM / SIGINT → clean shutdown, signal forwarded to index subprocess.

Isolation
---------
Each project has its own socket file at ``<db_dir>/<project_id>/daemon.sock``.
Pings from an MCP server on project A never affect project B's daemon.

Design rationale
----------------
WHY a per-project daemon instead of a single system-wide watcher:
- Each project has a different root directory, index database, and
  build configuration.  A single watcher would need to multiplex
  watchfiles across unrelated directory trees, complicating error
  handling (one project's NFS mount hangs → all projects stall).
- Socket-based ping-per-project gives clean client counting:
  when no MCP server pings project A's daemon for 60 s, that daemon
  exits — independent of project B's activity.

WHY Unix domain sockets instead of a message queue or shared memory:
- Sockets work on all Unix-like systems without additional dependencies.
- No message broker process needed (ZeroMQ, Redis, etc.).
- Socket existence doubles as a daemon-alive indicator for ``ping_daemon``.
- 0o600 permissions prevent other users on the same machine from sending
  spurious pings.

WHY ``watchfiles`` instead of ``inotify`` directly:
- ``watchfiles`` provides a cross-platform polling + inotify backend with
  debouncing built in.  Raw inotify requires manual event coalescing
  (a ``git checkout`` generates hundreds of events).

WHY the file watcher is a separate task: an index run takes minutes on
a large project.  A loop that watches and indexes in turn must leave its
``async for`` to start the run, and that closes the inotify subscription.
``watchfiles`` keeps no events while the subscription is closed, thus each
edit made during the run is lost.  A watcher task that lives as long as
the daemon keeps those edits as a flag for the reindex loop.

WHY ``debounce=2000 ms``: ``git checkout``, ``git pull``, and IDE
auto-save generate bursts of file-change events.  Without debouncing,
the daemon would spawn a ``fw-context index`` for each event — then
kill and restart it for the next event arriving 20 ms later.  The 2 s
window collects all rapid changes into a single reindex run.

WHY 60 s ping timeout (not shorter): the MCP server pings every 15 s.
Four missed pings = 60 s.  This gives headroom for:
- MCP server process being paged out under memory pressure.
- Temporary socket buffer saturation.
- Brief NFS hangs on the project directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..config import derive_project_id
from ..config import load as load_config
from ..exit_codes import EXIT_ALREADY_RUNNING, EXIT_SUPERSEDED
from ..platform import (
    ROLE_WATCHER_DAEMON,
    ProcessIdentity,
    fs,
    lock as _platform_lock,
    process as _process,
    read_pid_marker,
    subprocess_env,
    transport as _transport,
    write_pid_marker,
)
from ..utils import HEADER_EXTENSIONS, TU_EXTENSIONS
from .shared.pid_file import PidFile
from .background import PENDING_MARKER

_SUPERSEDED_RUNS_MAX = 3


_HELD_POLL_S = 2.0  # How often the loop looks if the other process released the index
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DAEMON_SOCK_NAME = "daemon.sock"
PING_INTERVAL = 15   # How often each MCP server pings (seconds)
PING_TIMEOUT = 60    # Exit after this long without any ping (seconds)
_DEBOUNCE_S = 2.0    # Collect changes before spawning index
_WATCH_TIMEOUT = 5   # watchfiles yield_on_timeout interval (seconds)
# A reindex abandoned because a manual operation took the index over is
# retried, not dropped — its changes are still unindexed.  Bounded because a
# manual operation that keeps re-taking the index would otherwise spin here;
# after this the next file change picks the work up.
_SUPERSEDED_RETRIES = 3
_PAUSE_WAIT_S = 120.0  # How long to wait for a manual operation to release

# ── Watched file extensions ──────────────────────────────────────────────────
# What the watcher reacts to: a translation unit or a header, by the
# compiler rules.  The hand-written set this replaced was missing `.c++`,
# `.tcc` and every uppercase form — measured against the real manifests,
# `.tcc` alone appears 3 to 18 times per project.
#
# Extension-less headers stay out: the compiler has no rule for them, so
# they belong to the derived set of the project rather than here.
_SOURCE_EXTS_WATCH: frozenset[str] = TU_EXTENSIONS | HEADER_EXTENSIONS
_EXCLUDE_RX = re.compile(r"(/\.git/|/\.pio/|/build/|/__pycache__/|/node_modules/)")


# ── Public helpers ───────────────────────────────────────────────────────────


def _get_db_dir(project_root: Path) -> Path:
    """Return ``<index_dir>/<project_id>/`` for *project_root*."""
    cfg = load_config(project_root=project_root)
    project_id = derive_project_id(project_root)
    return cfg.index.db_dir / project_id


def _socket_path(db_dir: Path) -> Path:
    """Return the Unix domain socket path for a project."""
    return db_dir / DAEMON_SOCK_NAME


def ping_daemon(project_root: Path) -> bool:
    """Send a ping to the project daemon.

    Connects to the project's control endpoint (AF_UNIX ``daemon.sock`` on
    POSIX; TCP loopback port from ``daemon.port`` on win32), sends
    ``"ping\\n"``, and closes.  Returns ``True`` when the daemon accepted
    the connection; ``False`` when the endpoint does not exist or no
    daemon is listening.

    Kept as a module-level wrapper because ``mcp.background`` and the CLI
    import it from here; the transport logic lives in the platform
    adapter.
    """
    db_dir = _get_db_dir(project_root)
    return _transport.ping(db_dir)


# ── Daemon main (asyncio) ────────────────────────────────────────────────────


async def daemon_main(project_root: Path) -> None:
    """Run the watcher daemon for *project_root*.

    Does not return until shutdown (SIGTERM / SIGINT / ping timeout).
    Intended to be run as a standalone process::

        python -m fw_context_mcp.mcp.daemon <project_root>
    """
    db_dir = _get_db_dir(project_root)
    db_dir.mkdir(parents=True, exist_ok=True)

    # ── Acquire watcher lock ─────────────────────────────────────────────
    lock_file = db_dir / "watcher.lock"
    lock_handle = _platform_lock.open(lock_file)
    if not lock_handle.try_lock():
        lock_handle.release()
        log.error("Daemon already running for %s", project_root)
        sys.exit(1)

    pid_file = db_dir / "watcher.pid"
    # Exclusive create through the platform fs adapter — O_EXCL does not
    # follow symlinks; win32 additionally refuses a reparse point at the
    # path.  The marker carries the start-time identity (D2), which is
    # what authorizes `fw-context watch restart` to stop THIS process.
    identity = _process.current_identity(ROLE_WATCHER_DAEMON)
    try:
        write_pid_marker(pid_file, identity)
    except FileExistsError:
        # Stale PID file from a previous crash — clean up and retry
        pid_file.unlink(missing_ok=True)
        write_pid_marker(pid_file, identity)

    # ── Shared state ─────────────────────────────────────────────────────
    # No lock needed — asyncio runs in a single thread; all accesses to
    # last_ping_time are cooperative and never preempted mid-operation.
    last_ping_time: float = time.monotonic()
    shutdown: asyncio.Event = asyncio.Event()
    index_proc: asyncio.subprocess.Process | None = None

    # ── Control endpoint (AF_UNIX / TCP loopback per platform) ──────────
    def _on_ping() -> None:
        nonlocal last_ping_time
        last_ping_time = time.monotonic()

    try:
        listener = await _transport.listen(db_dir, _on_ping)
    except OSError:
        lock_handle.release()
        pid_file.unlink(missing_ok=True)
        sys.exit(1)

    # ── Signal handlers ──────────────────────────────────────────────────
    _setup_signal_handlers(shutdown)

    # ── Main loop ────────────────────────────────────────────────────────
    # ── Structured logger — attach project context for log aggregation ──
    dlog = logging.LoggerAdapter(
        log,
        {"project_root": str(project_root), "pid": os.getpid()},
    )

    dlog.info("Daemon started  endpoint=%s", listener.endpoint)

    watch_task: asyncio.Task | None = None
    try:
        # Load the build-dir exclusion patterns from the manifest (derived
        # from SDK detection and the ``[index] exclude_paths`` config).  They
        # supplement the hardcoded _EXCLUDE_RX during change filtering.  This
        # runs before the initial index, because the watcher starts first and
        # reads this list.
        build_patterns = await asyncio.get_running_loop().run_in_executor(
            None, _load_build_patterns, db_dir
        )

        # Start the watcher BEFORE the initial index.  The initial index runs
        # as long as any other index run, thus a watcher that starts after it
        # loses each edit that a user makes while it runs.
        pending: asyncio.Event = asyncio.Event()
        watch_task = asyncio.create_task(
            _watch_changes(project_root, build_patterns, pending, shutdown, dlog)
        )

        # ── Initial staleness check ──────────────────────────────────────
        # watchfiles reports new events only.  Files that changed before the
        # daemon started are found here, and become the first pending change.
        # The check is synchronous, thus it runs in an executor and does not
        # block the socket server.
        from .background import _check_bg_pause as _bg_paused
        needs, reasons = await asyncio.get_running_loop().run_in_executor(
            None, _staleness_check, project_root
        )
        force_refs = False
        if needs and not _bg_paused(project_root):
            dlog.info("Initial index needed (%s)", ", ".join(reasons))
            force_refs = "refs missing" in reasons
            pending.set()

        def _set_index_proc(proc: asyncio.subprocess.Process | None) -> None:
            """Publish the running subprocess, thus the cleanup can stop it."""
            nonlocal index_proc
            index_proc = proc

        def _ping_expired() -> bool:
            """Return True when no MCP client sent a ping for PING_TIMEOUT."""
            elapsed = time.monotonic() - last_ping_time
            if elapsed <= PING_TIMEOUT:
                return False
            dlog.info(
                "No ping for %.0f s — all clients disconnected, exiting", elapsed
            )
            return True

        await _reindex_loop(
            project_root,
            db_dir,
            pending=pending,
            shutdown=shutdown,
            dlog=dlog,
            ping_expired=_ping_expired,
            on_index_proc=_set_index_proc,
            build_patterns=build_patterns,
            force_refs=force_refs,
        )

    finally:
        shutdown.set()
        if watch_task is not None:
            await _stop_watcher(watch_task)
        await _cleanup_daemon(index_proc, listener, db_dir, pid_file, lock_handle, shutdown)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _write_marker_text(path: Path, identity: ProcessIdentity) -> None:
    """Overwrite *path* with a JSON pid marker for *identity*.

    Same payload as ``platform.write_pid_marker``, through an explicit
    write so the two-step guard-then-child update stays atomic enough for
    the daemon's needs.
    """
    payload = {
        "pid": identity.pid,
        "create_token": identity.create_token,
        "role": identity.role,
        "recorded_at": identity.recorded_at,
    }
    with open(path, "w", encoding="utf-8") as pf:
        pf.write(json.dumps(payload))
        pf.flush()


# ── Change watcher ───────────────────────────────────────────────────────────


async def _watch_changes(
    project_root: Path,
    build_patterns: list[str],
    pending: _PendingWork,
    shutdown: asyncio.Event,
    dlog: logging.LoggerAdapter,
) -> None:
    """Set *pending* for each C/C++ change under *project_root*, until shutdown.

    WHY this is a task, and not an ``async for`` inside the reindex loop:
    an index run takes minutes on a large project.  A loop that watches and
    indexes in turn must leave its ``async for`` to start the run, and that
    closes the inotify subscription.  ``watchfiles`` keeps no events while
    the subscription is closed, thus each edit made during the run is lost,
    and the index stays stale until an unrelated edit starts the next run.
    This task lives as long as the daemon, thus it keeps those edits as a
    flag that the reindex loop reads when the current run stops.

    ``awatch`` gets *shutdown* as its ``stop_event``, thus it returns as soon
    as the daemon stops, and does not wait for the Rust timeout.

    *build_patterns* is read for each change, thus the caller can refresh the
    list in place after an index run writes a new manifest.
    """
    from watchfiles import awatch

    while not shutdown.is_set():
        try:
            async for changes in awatch(
                project_root,
                debounce=int(_DEBOUNCE_S * 1000),
                recursive=True,
                rust_timeout=_WATCH_TIMEOUT * 1000,
                yield_on_timeout=True,
                stop_event=shutdown,
            ):
                if shutdown.is_set():
                    return
                for _, changed_path_str in changes:
                    if _is_source_file(changed_path_str, build_patterns):
                        pending.set()
                        break
        except (OSError, RuntimeError) as exc:
            dlog.warning("watchfiles error: %s", exc)
            await asyncio.sleep(5)


async def _stop_watcher(task: asyncio.Task) -> None:
    """Stop the watcher *task*, and do not let its error stop the shutdown.

    The task stops on its own when the daemon sets the shutdown event.  The
    cancel is the fallback for a task that is between two ``awatch`` calls at
    that moment.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except (OSError, RuntimeError):
        log.exception("Watcher task stopped with an error")


# ── Reindex loop ─────────────────────────────────────────────────────────────


class _PendingWork(asyncio.Event):
    """The flag of the work that the daemon has, and the marker that shows it to the other processes.

    The marker ``reindex.pending`` holds the PID of the daemon
    (``background.read_reindex_state`` reads it).  It means "the daemon has
    work that is not done".  :meth:`set` writes it.  Three places remove
    it:

    - The reindex loop, at the end of a turn that leaves no work
      (:meth:`hide_if_done`), also a turn that gives the work up.
    - ``_cleanup_daemon``, when the daemon stops.
    - ``daemon_main``, when the daemon starts: a daemon that a SIGKILL
      stopped leaves its marker, and its PID can come back.

    WHY the marker stays while a run works: the run owns ``reindex.pid``
    only from the moment that it holds the index lock until it releases
    it.  The start of the subprocess comes before, and the outcome of the
    run, which can be "do it again", comes after.  A marker that went away
    at the start of the run left these times without a state, and the
    answers of that time said "run fw-context index" for work that came.
    While the run owns ``reindex.pid``, the reader gives "reindexing"
    first.

    WHY :meth:`set` writes the marker, and not the loop: the loop takes the
    flag only on its next turn.  A change during a run was invisible until
    that turn, measured at 1.4 s.
    """

    def __init__(self, db_dir: Path) -> None:
        super().__init__()
        self.marker = PidFile(db_dir / PENDING_MARKER)

    def set(self) -> None:
        super().set()
        self.show()

    def show(self) -> None:
        """Write the marker.  A failed write costs the state only, thus the work still runs."""
        try:
            self.marker.write()
        except OSError as exc:
            log.warning("Cannot write %s: %s", self.marker.path, exc)

    def hide_if_done(self) -> None:
        """Remove the marker when no change came after the last clear of the flag."""
        if not self.is_set():
            self.marker.unlink_if_ours()


async def _reindex_loop(
    project_root: Path,
    db_dir: Path,
    *,
    pending: asyncio.Event,
    shutdown: asyncio.Event,
    dlog: logging.LoggerAdapter,
    ping_expired: Callable[[], bool],
    on_index_proc: Callable[[asyncio.subprocess.Process | None], None],
    build_patterns: list[str] | None = None,
    force_refs: bool = False,
    startup_check: Callable[[Path], tuple[bool, list[str]]] | None = None,
) -> None:
    """Run one index for each pending change, until shutdown or a ping timeout.

    WHY the loop clears *pending* BEFORE it starts the index subprocess: a
    change that arrives while the subprocess runs sets the flag again, thus
    the next turn of the loop indexes that change.  A clear after the run
    drops such a change, and the index then stays stale until an unrelated
    edit starts the next run.  The cost of this order is one more run when
    the subprocess already saw the change.  A run that is not necessary is
    cheaper than an index that is not correct.
    """
    from .background import _check_bg_pause as _bg_paused
    loop = asyncio.get_running_loop()
    force_refs = False
    if startup_check is not None:
        if not await _index_is_free(db_dir, shutdown, ping_expired, dlog):
            return
        # Synchronous, thus in an executor: the socket server answers the
        # pings in this time.
        #
        # WHY the errors stop here: the check finds only the work of the
        # time when no daemon watched.  Some errors made the exception leave
        # the loop: a locked or damaged database, a file that cannot be
        # read, or a config that lost its project ID after the start
        # (`ProjectNotInitializedError`).  The daemon then stopped, and a
        # new daemon stopped on the same error.  Without the check the
        # watcher continues.  The status tools do their own check: they
        # show the error, or that no run updates the index
        # ("not_scheduled"), and they name the command.
        try:
            needs, reasons = await loop.run_in_executor(None, startup_check, project_root)
        except (sqlite3.Error, OSError, ValueError, RuntimeError):
            dlog.warning("The check at the start failed; the watcher continues without it", exc_info=True)
            needs, reasons = False, []
        if needs:
            dlog.info("Initial index needed (%s)", ", ".join(reasons))
            # A reason of a build carries its variant/image in front.
            force_refs = any(reason.endswith("refs missing") for reason in reasons)
            pending.set()

    loop = asyncio.get_running_loop()

    while not shutdown.is_set():
        if not pending.is_set():
            try:
                await asyncio.wait_for(pending.wait(), timeout=_WATCH_TIMEOUT)
            except TimeoutError:
                pass
        if ping_expired():
            return
        if shutdown.is_set() or not pending.is_set():
            continue

        # Collect the rest of the burst.  A git checkout and a build both
        # touch many files, and one index run covers all of them.
        await asyncio.sleep(_DEBOUNCE_S)

        # A manual operation can hold the index.  Keep the pending flag while
        # it does — a single-file reindex does not cover the other changes.
        if _bg_paused(project_root):
            dlog.info("Manual index in progress — the background reindex waits")
            if not await _wait_for_pause_to_clear(project_root, shutdown):
                continue

        pending.clear()

        # A manual operation can also take the index over mid-run, and the run
        # then abandons itself rather than finishing from a snapshot that
        # operation invalidated.  Its work is still outstanding, thus retry
        # after the marker clears.  After the last attempt the loop gives the
        # work up, because a manual operation that takes the index over again
        # and again would otherwise spawn one index subprocess after another.
        for _attempt in range(_SUPERSEDED_RETRIES):
            proc = await _run_index_async(project_root, db_dir, force_refs=force_refs)
            on_index_proc(proc)
            superseded = await _wait_index(proc, shutdown, db_dir=db_dir)
            on_index_proc(None)
            if not superseded or shutdown.is_set():
                break
            if not await _wait_for_pause_to_clear(project_root, shutdown):
                dlog.info(
                    "Pause marker still held — leaving the reindex to the next change"
                )
                break
        else:
            dlog.warning(
                "Background reindex superseded %d times — giving up until the "
                "next change",
                _SUPERSEDED_RETRIES,
            )
        force_refs = False

        # A completed run can write a new manifest.  Refresh the exclusions in
        # place, because the watcher task holds the same list object.
        if build_patterns is not None and not shutdown.is_set():
            build_patterns[:] = await loop.run_in_executor(
                None, _load_build_patterns, db_dir
            )


def _index_held(db_dir: Path) -> bool:
    """Return True while another process holds the index.

    ``reindex.pause``: ``reindex_file`` and ``reset_index`` of an MCP
    server write it, and each ``fw-context index`` run.  ``reindex.pid``:
    each ``fw-context index`` run writes it when it holds the index lock
    (``cli._index._claim_index``), also a run that holds no pause marker
    any more because a ``reindex_file`` replaced it and then removed it.
    Between two turns no run of this daemon owns either file: the run
    removes them when it ends, and ``_wait_index`` removes the files of a
    run that a kill stopped.
    """
    return PidFile.is_active(db_dir / "reindex.pause") or PidFile.is_active(db_dir / "reindex.pid")


async def _index_is_free(
    db_dir: Path,
    shutdown: asyncio.Event,
    ping_expired: Callable[[], bool],
    dlog: logging.LoggerAdapter,
) -> bool:
    """Wait while another process holds the index.

    Returns True when the index is free, and False on shutdown or a ping
    timeout.  WHY no time limit: a manual index of a large project runs
    for hours, and work that the loop gives up after a limit was not done
    by anybody.  The loop must still stop with the daemon, thus this
    function looks at the shutdown and the pings.
    """
    if not _index_held(db_dir):
        return True
    dlog.info("Another process holds the index — the background reindex waits")
    while _index_held(db_dir):
        if shutdown.is_set() or ping_expired():
            return False
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=_HELD_POLL_S)
        except TimeoutError:
            pass
    return not shutdown.is_set()


async def _setup_unix_socket(db_dir: Path, on_ping) -> tuple[asyncio.Server, Path]:
    if sys.platform == 'win32':  # no Unix-socket ping server on Windows
        return None, db_dir / 'fw-context.sock'
    """Create and bind the Unix domain socket for daemon pings.

    *on_ping* is called (no arguments) each time a valid ping arrives.
    Returns the server and socket path.
    """
    sock_path = _socket_path(db_dir)
    sock_path.unlink(missing_ok=True)

    async def _handle_ping(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=2.0)
            if b"ping" in data:
                on_ping()
        except (TimeoutError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    try:
        server = await asyncio.start_unix_server(_handle_ping, path=str(sock_path))
    except OSError as exc:
        # Not "daemon already running": this process holds `watcher.lock`,
        # thus no other daemon of the project runs.  The error says the
        # cause, for example "AF_UNIX path too long" for a socket path that
        # is longer than `sun_path` (108 bytes on Linux, 104 on macOS).  The
        # index directory sets that path.
        log.error("Cannot bind the socket %s: %s", sock_path, exc)
        raise

    os.chmod(sock_path, 0o600)
    return server, sock_path


def _setup_signal_handlers(shutdown: asyncio.Event) -> None:
    """Register SIGTERM / SIGINT handlers that set *shutdown*."""
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass


async def _cleanup_daemon(
    index_proc: asyncio.subprocess.Process | None,
    listener,
    db_dir: Path,
    pid_file: Path,
    lock_handle,
    shutdown: asyncio.Event,
) -> None:
    """Gracefully shut down the daemon: terminate subprocess, close endpoint, clean up files.

    Kill-authority note (D2/W5): the index subprocess is stopped through
    its OWN asyncio handle (``index_proc.terminate()``/``.kill()``).
    That kill is process-relative — the handle can only ever reach the
    child THIS function was handed — so no start-time-identity check
    applies: there is no pid-reuse window between "read a pid from a
    marker" and "signal it" because no pid is read; the handle IS the
    identity.  Marker-named pids (``reindex.pid`` / ``watcher.pid``)
    never go through here — they must use the platform's identity-checked
    stop sequence (:func:`fw_context_mcp.platform.stop_marker_process`,
    see ``cli/_index._kill_bg_reindex`` and ``cli/_watch.cmd_watch_restart``).
    """
    log.info("Daemon shutting down")
    if index_proc is not None and index_proc.returncode is None:
        log.info("Terminating index subprocess (pid %d)", index_proc.pid)
        index_proc.terminate()
        try:
            await asyncio.wait_for(index_proc.wait(), timeout=10)
        except TimeoutError:
            log.warning("Index subprocess did not exit, killing")
            index_proc.kill()
            await index_proc.wait()
    shutdown.set()
    listener.close()
    await listener.wait_closed()
    _cleanup_files(db_dir, pid_file)
    lock_handle.release()


def _cleanup_files(db_dir: Path, pid_file: Path) -> None:
    """Remove daemon runtime artifacts (endpoint + pid marker)."""
    _transport.cleanup(db_dir)
    pid_file.unlink(missing_ok=True)


def _is_source_file(path_str: str, build_patterns: list[str] | None = None) -> bool:
    """Return True when *path_str* is a C/C++ source file outside excluded dirs.

    Uses hardcoded ``_EXCLUDE_RX`` as baseline and supplements with
    *build_patterns* from the project manifest when available (derived
    from SDK detection and ``[index] exclude_paths`` config).
    """
    p = Path(path_str)
    if p.suffix not in _SOURCE_EXTS_WATCH:
        return False
    if _EXCLUDE_RX.search(path_str):
        return False
    if build_patterns and any(pat in path_str for pat in build_patterns):
        return False
    return True


def _load_build_patterns(db_dir: Path) -> list[str]:
    """Load ``build_dir_patterns`` from manifest.json, returning [] on any error.

    Reads the patterns without parsing the whole manifest — the file runs to
    tens of megabytes and this needs one short list from it.  The daemon calls
    this at startup and after each index run that can write a new manifest,
    thus the cost is per run rather than per query.
    """
    try:
        from ..indexer.manifest import load_build_dir_patterns_any
        return load_build_dir_patterns_any(db_dir)
    except (OSError, ImportError, ValueError, KeyError):
        return []


# ── Staleness check (minimal — same spirit as _fast_staleness_check) ─────────


def _staleness_check(project_root: Path) -> tuple[bool, list[str]]:
    """Return ``(needs_reindex, reasons)`` for the initial daemon startup.

    Checks structural staleness (compile_commands.json, schema, row format,
    refs) AND file-level staleness (the stored hash against the file on
    disk, or the stored time for a row without a hash,
    ``_count_modified_files``).
    Without the file-level check the daemon would never detect files that
    were modified BEFORE it started — watchfiles only detects NEW changes.

    The row-format check is the reason a daemon can ask for a reindex over
    an index whose files did not change: the columns are the same, but the
    text in them carries an older meaning.  See ``CURRENT_ROW_FORMAT``.

    Every build of the project is checked, each ``(variant, image)``, and
    not only the newest one: an edit to a file that only another image
    compiles made that build stale, and the check of the newest build did
    not see it.  A reason of a build carries its ``variant/image``.

    A checkout that happened while no daemon ran is a reason too: the
    watcher sees a change of HEAD only while it runs
    (``git_context.head_moved``).  A new source file that the run builds on
    its own (``_unindexed_sources``) is also a reason: the watcher sees a
    new file only while it runs, and without this check no run came for a
    file that the user added before the daemon started.
    """
    from ..indexer.git_context import read_head
    from .shared.context import _db_path, _quick_open_readonly
    from .shared.stale import _count_modified_files, check_structural_staleness, checkout_reason

    db_path = _db_path(project_root)
    if not db_path.exists():
        return False, []

    # Read-only quick open: a full open_db would run ensure_schema's
    # unconditional executescript — a write transaction on every check
    # interval — causing lock contention with the executor.  The daemon
    # only reads here.
    try:
        conn = _quick_open_readonly(db_path)
    except sqlite3.Error:
        log.warning("Database unreadable — skipping staleness check")
        return False, []
    reasons: list[str] = []
    try:
        from .shared.stale import build_label, latest_builds, outdated_index_fact

        # Before the first read of a build row.  The run that this reason
        # starts opens the index with `open_db`, which adds the columns.
        outdated = outdated_index_fact(conn)
        if outdated:
            return True, [outdated]

        project_id = derive_project_id(project_root)
        live_head = read_head(project_root)
        for cfg in latest_builds(conn, project_id):
            config_hash = cfg["config_hash"]
            label = build_label(cfg)
            prefix = f"{label}: " if label else ""

            # 1-4. Structural checks: compile_commands.json, schema version,
            #      row format, refs.  `index_state` does checks 1-3 and not the refs.
            found = check_structural_staleness(conn, config_hash, dict(cfg), project_root)

            # 5. Modified source files — files changed before daemon started.
            #    watchfiles only detects NEW events, so without this check
            #    already-stale files would never be reindexed.
            modified = _count_modified_files(conn, config_hash, project_root, use_cache=True)
            if modified > 0:
                found.append(f"{modified} modified files")

            # 6. Another checkout than the build: the run then builds
            #    (`cli._index._plan_auto_build`).
            moved = checkout_reason(cfg, live_head, project_root)
            if moved:
                found.append(moved)
            reasons.extend(prefix + reason for reason in found)

        # 7. Source files that compile_commands.json does not cover, which
        #    came while no daemon ran: the watcher sees a new file only while
        #    it runs.  Only where the run builds them on its own
        #    (`cli._index._plan_auto_build`): for another backend a run
        #    changes nothing, and the status tools name the command.
        unindexed = _unindexed_sources(conn, project_id, project_root)
        if unindexed:
            reasons.append(f"{len(unindexed)} source file(s) not in compile_commands.json")
    finally:
        conn.close()

    return len(reasons) > 0, reasons


def _unindexed_sources(conn, project_id: str, project_root: Path) -> list[str]:
    """Return the new source files of the project that an index run builds on its own.

    The function reads the build that the status tools check
    (`variants.checked_build`), thus the daemon and `get_project_status` see
    the same files.  The list is empty when the backend cannot build in the
    background (`autobuild.project_state`): a run then changes nothing.
    """
    from ..config import load as load_config
    from ..indexer.autobuild import AutobuildState
    from ..indexer.autobuild import project_state as autobuild_state
    from .shared.stale import find_unindexed_sources
    from .shared.variants import checked_build

    cfg = load_config(project_root)
    checked = checked_build(conn, project_id, cfg, project_root)
    if checked is None or not checked["compile_commands_path"]:
        return []
    found = find_unindexed_sources(
        conn, checked["config_hash"], project_root, Path(checked["compile_commands_path"]),
    )
    if not found or autobuild_state(project_root, cfg) is not AutobuildState.WILL_BUILD:
        return []
    return found


# ── Index subprocess ─────────────────────────────────────────────────────────


async def _run_index_async(
    project_root: Path, db_dir: Path, *, force_refs: bool = False,
) -> asyncio.subprocess.Process:
    """Spawn ``fw-context index --background``, write stdout to ``reindex.log``.

    No ``--build`` here, and the daemon decides nothing about building.  The
    index run turns the build on itself where it is needed and allowed —
    ``cli._index._plan_auto_build`` — because only that path also sets the
    isolated output directory that makes a background build legal.  A
    ``--build`` from here failed with "--build and --background are mutually
    exclusive" in under a second.

    Writes the subprocess PID to ``reindex.pid`` so ``_is_bg_reindex_running``
    can reliably detect an active index run (without false positives from
    the daemon's ``watcher.lock``).

    Returns the asyncio Process handle — caller must wait or terminate.
    """
    log_file = db_dir / "reindex.log"
    fd = fs.safe_open_log(log_file, append=False)
    if fd is not None:
        log_fh = os.fdopen(fd, "w", encoding="utf-8", errors="replace", closefd=True)
    else:
        # Refused (symlink/reparse at the log path) or unwritable — the
        # run's own output goes to devnull, per this function's original
        # caught-path fallback; the run itself still proceeds.
        log.warning("reindex.log at %s not openable — subprocess output discarded", log_file)
        log_fh = _DEVNULL  # type: ignore[assignment]

    env = subprocess_env()
    if force_refs:
        env["FW_CONTEXT_FORCE_REFINDEX"] = "1"

    log.info("Starting background index for %s (force_refs=%s)", project_root, force_refs)

    # Write reindex.pid BEFORE spawning so a concurrent daemon
    # restart sees the marker and doesn't spawn a duplicate.  The marker
    # carries the start-time identity (D2) — a concurrent
    # `_kill_bg_reindex` verifies the token before terminating anything.
    rp = db_dir / "reindex.pid"
    old_marker = read_pid_marker(rp)
    if (
        old_marker is not None
        and old_marker.pid != os.getpid()
        and PidFile._pid_exists(old_marker.pid)
    ):
        raise RuntimeError(
            f"Another index process (pid {old_marker.pid}) is already running for {project_root}"
        )
    # Clean up any stale file before writing ours.
    rp.unlink(missing_ok=True)
    identity = _process.current_identity("background-index")
    _write_marker_text(rp, ProcessIdentity(
        pid=identity.pid,
        create_token=identity.create_token,
        role="background-index",
        recorded_at=identity.recorded_at,
    ))

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-m", "fw_context_mcp.cli", "index", "--background",
            cwd=str(project_root),
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        # Update the marker with the real subprocess identity — the child's
        # own start-time token, so a concurrent `_kill_bg_reindex` can
        # verify it before terminating.
        child = _process.identify(proc.pid)
        _write_marker_text(rp, ProcessIdentity(
            pid=proc.pid,
            create_token=child.create_token if child else None,
            role="background-index",
            recorded_at=identity.recorded_at,
        ))
        if log_fh is not _DEVNULL:
            log_fh.close()
    except (ValueError, TypeError, RuntimeError, AttributeError, FileExistsError, OSError):
        log.exception("Failed to spawn index subprocess")
        rp.unlink(missing_ok=True)
        if log_fh is not _DEVNULL:
            log_fh.close()
        raise
    return proc


async def _wait_for_pause_to_clear(
    project_root: Path, shutdown: asyncio.Event
) -> bool:
    """Wait for the manual operation to release the index.

    Returns True when the marker cleared and a retry makes sense, False on
    shutdown or when the operation outlasts the wait — a manual reindex of a
    large project can run for an hour, and holding the daemon in a poll loop
    that whole time buys nothing over letting the next file change trigger it.
    """
    from .background import _check_bg_pause as _bg_paused

    deadline = time.monotonic() + _PAUSE_WAIT_S
    while time.monotonic() < deadline:
        if shutdown.is_set():
            return False
        if not _bg_paused(project_root):
            return True
        await asyncio.sleep(2.0)
    return False


async def _wait_index(
    proc: asyncio.subprocess.Process,
    shutdown: asyncio.Event,
    *,
    db_dir: Path,
) -> bool:
    """Wait for *proc* to finish, polling *shutdown* every 500 ms.

    When *shutdown* is set, terminates the subprocess gracefully
    (SIGTERM → 10 s → SIGKILL).  Removes ``reindex.pid`` on exit
    so ``_is_bg_reindex_running`` doesn't report a stale PID.

    Kill-authority note (D2/W5): the SIGTERM/SIGKILL here go through the
    subprocess's OWN handle (``proc.terminate()``/``proc.kill()``) —
    process-relative, so they need no start-time-identity check (the
    handle cannot reach a reused pid).  Only marker-read pids need the
    identity-checked path (``platform.stop_marker_process``).

    Returns True when the run was superseded by a manual operation and its
    work still has to be done.
    """
    try:
        while proc.returncode is None:
            if shutdown.is_set():
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except TimeoutError:
                    log.warning("Index subprocess did not exit after SIGTERM, killing")
                    proc.kill()
                    await proc.wait()
                return False
            await asyncio.sleep(0.5)
    finally:
        PidFile(db_dir / "reindex.pid", pid=proc.pid).unlink_if_ours()

    if proc.returncode == EXIT_SUPERSEDED:
        # The run stopped because a manual operation took the index over.
        # The changes that triggered it are still unindexed, so this must not
        # be treated as done — going back to waiting for the NEXT change
        # would leave them out until something else happens to be edited.
        log.info("Background index superseded by a manual operation — will retry")
        return True
    if proc.returncode == EXIT_ALREADY_RUNNING:
        # Another indexing run owns the index.  Nothing to retry — that run
        # covers this project, and racing it is what the lock prevents.
        log.info("Background index skipped — another index run holds the index")
        return False
    if proc.returncode == 0:
        log.info("Background index completed")
        # WAL files grow unboundedly during long reindex runs because
        # the index subprocess writes continuously without checkpointing.
        # PRAGMA optimize triggers a checkpoint + ANALYZE to shrink the
        # WAL and refresh query-planner statistics.  This runs AFTER the
        # index completes — no lock contention with the subprocess.
        _optimize_db(db_dir)
    else:
        log.warning("Background index exited with %d", proc.returncode)
    return False


# ── DB helper ────────────────────────────────────────────────────────────────


def _optimize_db(db_dir: Path) -> None:
    """Run PRAGMA optimize to shrink the WAL file and defragment indexes.

    Deliberately uses a writable ``open_db`` (whitelisted write path):
    this is a rare maintenance operation outside the request path, and
    WAL allows concurrent readers while it runs.  Not a bug — do not
    "convert" this to the read-only quick open; writes need a writable
    connection.

    WHY no integrity check: the daemon calls this after an index run that
    ended with 0, and that run did the check.  A second check reads the
    full file again, and the daemon does not use its result.  The next
    index run does the check.

    WHY the errors stop here: the reindex loop calls this function, and an
    error of the open went out of the loop.  The daemon then stopped, and
    no change got an index run.  The maintenance is not necessary, thus a
    warning is sufficient.
    """
    from ..indexer.db import open_db

    db_path = db_dir / "index.db"
    if not db_path.exists():
        return
    try:
        conn = open_db(db_path, skip_integrity_check=True)
    except (sqlite3.Error, OSError):
        log.warning("Cannot open the database for PRAGMA optimize", exc_info=True)
        return
    try:
        conn.execute("PRAGMA optimize")
    except (sqlite3.Error, ValueError, TypeError, RuntimeError, AttributeError):
        log.debug("PRAGMA optimize failed", exc_info=True)
    finally:
        conn.close()


# ── Sentinel for log_fh default ──────────────────────────────────────────────
# ``object()`` suffices — all call sites use ``is`` comparison.
_DEVNULL = object()


# ── Entry point ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys as _sys
    from ..platform import configure_stdio

    configure_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if len(_sys.argv) < 2:
        print("Usage: python -m fw_context_mcp.mcp.daemon <project_root>", file=_sys.stderr)
        _sys.exit(1)
    target = Path(_sys.argv[1]).resolve()
    if not target.is_dir():
        print(f"Error: {target} is not a valid directory", file=_sys.stderr)
        _sys.exit(1)
    asyncio.run(daemon_main(target))
