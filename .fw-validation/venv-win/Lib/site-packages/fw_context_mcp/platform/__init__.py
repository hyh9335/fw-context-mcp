"""Platform facade — resolves ONCE at import (D1).

Business modules import from HERE and never ask the operating system
what it is running on::

    from ..platform import lock, process, fs, transport   # adapters
    from ..platform import write_pid_marker, read_pid_marker
    from ..platform import http_client, http_get, http_post, http_stream

The single ``os.name`` check of the package lives below; everything
behind it is a concrete backend whose surface is the protocol in
``platform.base``.  Import cost: stdlib only — httpx stays lazy inside
the factory helpers.
"""

from __future__ import annotations

import os

from .base import (
    ControlListener,
    ControlTransport,
    FileSystemCapabilities,
    InterProcessLock,
    LockHandle,
    ProcessIdentity,
    ProcessService,
    PlatformError,
    ROLE_BACKGROUND_INDEX,
    ROLE_FOREGROUND_INDEX,
    ROLE_WATCHER_DAEMON,
    StopOutcome,
    configure_stdio,
    http_client,
    http_get,
    http_post,
    http_stream,
    marker_pid,
    read_pid_marker,
    sanitize_proxy_env,
    spawn_detach_kwargs,
    spawn_detach_kwargs,
    stop_marker_process,
    subprocess_env,
    write_pid_marker,
)

if os.name == "posix":
    from .posix import (
        PosixControlTransport as _ControlTransport,
        PosixFileSystem as _FileSystem,
        PosixLock as _Lock,
        PosixProcessService as _ProcessService,
    )
elif os.name == "nt":
    from .windows import (
        WindowsControlTransport as _ControlTransport,
        WindowsFileSystem as _FileSystem,
        WindowsLock as _Lock,
        WindowsProcessService as _ProcessService,
    )
else:  # pragma: no cover — no supported backend for this OS
    raise PlatformError(
        f"unsupported platform {os.name!r}: no backend provides "
        "interprocess locks, process queries, or a control transport"
    )

#: Protocol-typed adapter singletons.
lock: "InterProcessLock" = _Lock()
process: "ProcessService" = _ProcessService()
fs: "FileSystemCapabilities" = _FileSystem()
transport: "ControlTransport" = _ControlTransport()

__all__ = [
    "ControlListener",
    "ControlTransport",
    "FileSystemCapabilities",
    "InterProcessLock",
    "LockHandle",
    "PlatformError",
    "ProcessIdentity",
    "ProcessService",
    "ROLE_BACKGROUND_INDEX",
    "ROLE_FOREGROUND_INDEX",
    "ROLE_WATCHER_DAEMON",
    "StopOutcome",
    "configure_stdio",
    "fs",
    "http_client",
    "http_get",
    "http_post",
    "http_stream",
    "lock",
    "marker_pid",
    "process",
    "read_pid_marker",
    "sanitize_proxy_env",
    "spawn_detach_kwargs",
    "stop_marker_process",
    "subprocess_env",
    "transport",
    "write_pid_marker",
]
