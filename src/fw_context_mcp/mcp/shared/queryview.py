"""Production QueryView pinning and compatibility resolution (M1).

This module implements the EXPLICIT World-selection authority required by
the Authority Migration program (P1, P2, P9, P30).

Three caller models (P31):

* **Explicit historical** — caller provides a full ``QueryView``
  (repository_namespace, snapshot_id, build_id, tu_selector). No resolution
  needed; ``resolve_query_view`` validates it.

* **Pinned session** — a ``PinnedQueryView`` is resolved ONCE at a lifecycle
  boundary (session creation, explicit refresh). All subsequent calls in
  that session use the SAME view. A later indexing run creates a new World;
  the pinned session keeps seeing the old one (P10 — no view drift).

* **Default/current convenience** — caller gives only ``project_root`` (the
  legacy compatibility path). The resolver deterministically maps it to ONE
  ``QueryView`` by re-deriving the namespace and snapshot from the current
  workspace state, then finding the matching ``active`` World.

DETERMINISTIC CURRENT-WORLD POLICY (P30):

The current World is NOT ``ORDER BY created_at DESC LIMIT 1``. It is derived
from an explicit completed indexing/workspace mapping:

1. ``capture_repository_identity(project_root)`` → ``namespace_id``
   (deterministic — same inputs as indexing).
2. ``capture_snapshot(project_root)`` → ``snapshot_id``
   (deterministic — captures current tree state).
3. Find the ``active`` World for that ``(namespace_id, snapshot_id)``.
   - Zero worlds → ``WorldNotFound`` (no index for current workspace state).
   - One world → use it.
   - Multiple worlds (multiple builds of same snapshot) → tiebreak by
     highest ``world_id`` (monotonically increasing storage handle,
     deterministic, NOT ``created_at`` which has second-granularity ties).
     When a ``build_id`` hint is available (legacy compatibility), prefer
     the matching build.

This module is MIGRATION INFRASTRUCTURE (P38). It is the bridge between the
legacy ``project_root``-based API and the v2 ``QueryView``-based API. Once
all callers provide explicit QueryViews, the compatibility resolution path
can be retired.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ...store_v2.query import (
    QueryView,
    ResolvedWorld,
    QueryViewError,
    WorldNotFound,
    AmbiguousTarget,
    resolve_query_view,
)
from ...store_v2.identity_capture import (
    capture_repository_identity,
    capture_snapshot,
)
from ...store_v2.worlds import build_world_context

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# ── Error taxonomy (P17) ────────────────────────────────────────────────────

class QueryViewResolutionError(Exception):
    """Base error for QueryView compatibility resolution failures.

    These are PRODUCTION errors (P17). They are NOT silently swallowed
    and do NOT fall back to legacy (P3 — no silent semantic fallback).
    """
    code = "QUERYVIEW_RESOLUTION_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail: dict = dict(detail or {})

    def to_error_dict(self) -> dict:
        return {"error": self.message, "code": self.code, "detail": self.detail}


class NoActiveWorld(QueryViewResolutionError):
    """No ``active`` World found for the resolved (namespace, snapshot).

    This means the current workspace state has no completed v2 index.
    The caller must run ``fw-context index`` (with v2 enabled) first.
    """
    code = "NO_ACTIVE_WORLD"


class V2StoreUnavailable(QueryViewResolutionError):
    """The v2 store (``index-v2.db``) does not exist or is not readable.

    This means v2 indexing has never run for this project, or the store
    file is inaccessible. In M0/M1, this is expected for projects that
    have never had ``FW_CONTEXT_SHADOW_V2=1`` set during indexing.
    """
    code = "V2_STORE_UNAVAILABLE"


class AmbiguousWorld(QueryViewResolutionError):
    """Multiple ``active`` Worlds found and no deterministic tiebreak applied.

    This should be unreachable with the snapshot-based resolution, but is
    kept as a defense-in-depth fail-closed path.
    """
    code = "AMBIGUOUS_WORLD"

    def __init__(self, message: str, *, candidates: list[dict],
                 detail: dict | None = None):
        super().__init__(message, detail={**(detail or {}),
                                          "candidates": candidates})
        self.candidates = candidates


# ── PinnedQueryView (P2, P10) ───────────────────────────────────────────────

@dataclass(frozen=True)
class PinnedQueryView:
    """A ``QueryView`` pinned at a lifecycle boundary.

    Once pinned, this view does NOT change even if a later indexing run
    creates a new World (P10 — no view drift). The ``pinned_at`` and
    ``pin_reason`` fields record WHEN and WHY the pin was established,
    for diagnostic/audit purposes.

    The ``resolved_world`` snapshot captures the World identity at pin time.
    If the underlying World is later superseded/retired, the pin still
    references the same ``QueryView`` — but ``resolve_query_view`` will
    raise ``WorldNotQueryable`` if the World is no longer queryable.
    That is the correct fail-closed behavior: the session learns that its
    pinned World is gone, rather than silently seeing a different one.
    """

    view: QueryView
    pinned_at: float  # time.perf_counter() of pin establishment
    pin_reason: str   # "session_default", "explicit_refresh", "caller_pinned"
    resolved_world: ResolvedWorld | None = None  # cached at pin time

    def to_diag(self) -> str:
        world = ""
        if self.resolved_world is not None:
            world = f" world_id={self.resolved_world.world_id}"
        return (f"PinnedQueryView(reason={self.pin_reason!r},"
                f" pinned_at={self.pinned_at:.3f}{world}"
                f" view={self.view.to_diag()})")


# ── Session pin registry (P2, P10, P29) ─────────────────────────────────────

_pin_lock = threading.Lock()
_session_pins: dict[str, PinnedQueryView] = {}


def pin_view_for_session(session_key: str, view: PinnedQueryView) -> None:
    """Register a pinned view under *session_key*.

    The session_key is an opaque caller-chosen identifier (e.g. MCP session
    id, or a synthetic key derived from project_root). A pin PROTECTS the
    referenced World from future GC (P29) — a GC implementation must check
    this registry before collecting any World.

    Thread-safe.
    """
    with _pin_lock:
        _session_pins[session_key] = view
        log.debug("Pinned QueryView for session %s: %s", session_key,
                  view.to_diag())


def get_pinned_view(session_key: str) -> PinnedQueryView | None:
    """Return the pinned view for *session_key*, or None if not pinned."""
    with _pin_lock:
        return _session_pins.get(session_key)


def unpin_session(session_key: str) -> PinnedQueryView | None:
    """Remove and return the pin for *session_key* (session end)."""
    with _pin_lock:
        return _session_pins.pop(session_key, None)


def list_pinned_sessions() -> list[tuple[str, PinnedQueryView]]:
    """Return all (session_key, PinnedQueryView) pairs (diagnostic)."""
    with _pin_lock:
        return list(_session_pins.items())


def clear_session_pins() -> int:
    """Clear all session pins (test utility). Returns count cleared."""
    with _pin_lock:
        n = len(_session_pins)
        _session_pins.clear()
        return n


# ── Compatibility resolution (P9, P30) ──────────────────────────────────────

def _v2_db_path_for_project(project_root: Path) -> Path:
    """Resolve the ``index-v2.db`` path for *project_root*.

    Mirrors the indexing path convention:
    ``<db_dir>/<project_id>/index-v2.db`` where ``db_dir`` comes from config.

    When the project is not initialized (no ``.fw-context/`` dir), this
    returns a best-effort path so the caller can check ``.exists()``
    and raise ``V2StoreUnavailable`` rather than a config-load error.
    """
    from ...config import load as load_config, derive_project_id
    from ...store_v2 import v2_db_path

    root = Path(project_root).resolve()
    # If not initialized, use the default convention without config load
    fw_ctx = root / ".fw-context"
    if not fw_ctx.exists():
        # Best-effort: derive project_id from the path (deterministic)
        import hashlib
        project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
        db_dir = fw_ctx / "index"
        return v2_db_path(db_dir, project_id)
    try:
        cfg = load_config(root)
        project_id = derive_project_id(root)
    except Exception:
        # Config load failed — fall back to path-derived id
        import hashlib
        project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
        db_dir = fw_ctx / "index"
        return v2_db_path(db_dir, project_id)
    db_dir = cfg.index.db_dir
    if db_dir is None:
        db_dir = fw_ctx / "index"
    return v2_db_path(Path(db_dir), project_id)


def resolve_current_view(
    project_root: str | Path,
    *,
    build_id_hint: str | None = None,
    v2_conn: sqlite3.Connection | None = None,
    allow_tree_mismatch: bool = False,
) -> PinnedQueryView:
    """Deterministically resolve the CURRENT completed World for *project_root*.

    This is the compatibility path (P9/P30) for callers that give only a
    ``project_root``. The resolution is DETERMINISTIC and FAIL-CLOSED:

    1. ``capture_repository_identity`` → ``namespace_id`` (same as indexing).
    2. ``capture_snapshot`` → ``snapshot_id`` (current tree state).
    3. Find ``active`` World(s) for ``(namespace_id, snapshot_id)``.
       - Zero worlds → ``NoActiveWorld`` (workspace state has no v2 index).
       - One world → use it (exact workspace match).
       - Multiple worlds (multiple builds of same snapshot) → require
         ``build_id_hint`` or fail closed as ``AmbiguousWorld``.
    4. ``allow_tree_mismatch`` (default False since M2H): if True and no exact
       snapshot match, fall back to the latest active World for the namespace
       with ``pin_reason="session_default_tree_mismatch"``. **WARNING**: this
       is a M1 shadow-validation convenience that is NOT safe for M3 — it can
       mask semantic drift. It MUST remain False for production authority.

    A storage handle (``world_id``) is NEVER used to choose semantic Build
    identity (RISK A fix, D004).

    Args:
        project_root: Project root path (or name/id — resolved by caller).
        build_id_hint: Optional build_identity_sha to disambiguate multi-build.
            In M2H, this comes from the legacy config_hash → build_id bridge.
        v2_conn: Optional pre-opened v2 read connection.
        allow_tree_mismatch: If True, fall back to latest active World when
            exact snapshot doesn't match. **Default False** (fail-closed).
            Set True ONLY for shadow validation, never for v2 authority.

    Returns:
        A ``PinnedQueryView`` with the resolved World.

    Raises:
        V2StoreUnavailable: No ``index-v2.db`` for this project.
        NoActiveWorld: No ``active`` World for the current workspace state.
        AmbiguousWorld: Multiple worlds and no deterministic build evidence.
        QueryViewError: Underlying v2 store error.
    """
    root = Path(project_root).resolve()
    v2_path = _v2_db_path_for_project(root)
    if not v2_path.exists():
        raise V2StoreUnavailable(
            f"No v2 store at {v2_path}. Run 'fw-context index' with v2 enabled "
            f"(FW_CONTEXT_SHADOW_V2=1 or [index] shadow_v2_enabled = true).",
            detail={"v2_db_path": str(v2_path), "project_root": str(root)})

    owns_conn = v2_conn is None
    if owns_conn:
        from ...store_v2.query import open_query_connection
        v2_conn = open_query_connection(v2_path)
    try:
        return _resolve_current_view_inner(
            v2_conn, root, build_id_hint=build_id_hint,
            allow_tree_mismatch=allow_tree_mismatch)
    finally:
        if owns_conn:
            v2_conn.close()


def _resolve_current_view_inner(
    conn: sqlite3.Connection,
    project_root: Path,
    *,
    build_id_hint: str | None,
    allow_tree_mismatch: bool = False,
) -> PinnedQueryView:
    """Inner resolution — assumes conn is open and v2 store exists.

    RISK A fix (D004): A storage handle (world_id) is NEVER used to choose
    semantic Build identity. Multi-build ambiguity without evidence fails
    closed as ``AmbiguousWorld``.
    """
    # Step 1: capture repository identity → namespace_id
    repo = capture_repository_identity(project_root)
    from ...config import derive_project_id
    project_id = derive_project_id(project_root)
    if repo.namespace_tag == "git":
        namespace_id = f"r:{repo.repository_key_sha}"
    else:
        namespace_id = f"t:{project_id}"

    # Step 2: capture snapshot → snapshot_id (current tree state)
    snap = capture_snapshot(project_root)
    snap_id = snap.snapshot_id

    # Step 3: find active World(s) for EXACT (namespace_id, snapshot_id)
    worlds = conn.execute(
        """SELECT world_id, namespace_id, snapshot_id, build_identity_sha,
                  world_status, run_provenance, created_at
           FROM snapshot_builds
           WHERE namespace_id=? AND snapshot_id=? AND world_status='active'
           ORDER BY world_id""",
        (namespace_id, snap_id),
    ).fetchall()

    pin_reason = "session_default"
    current_snap_id = snap.snapshot_id

    if not worlds and allow_tree_mismatch:
        # M1 shadow-validation convenience (NOT safe for M3 authority).
        # Fall back to latest active World for this namespace.
        # RISK A fix: this path is GATED behind allow_tree_mismatch=True
        # (default False). It does NOT use world_id to choose between
        # builds of the SAME snapshot — it picks the latest across ALL
        # snapshots, which is a different (weaker) semantic claim.
        worlds = conn.execute(
            """SELECT world_id, namespace_id, snapshot_id, build_identity_sha,
                      world_status, run_provenance, created_at
               FROM snapshot_builds
               WHERE namespace_id=? AND world_status='active'
               ORDER BY world_id DESC""",
            (namespace_id,),
        ).fetchall()
        if worlds:
            # Take only the LATEST (highest world_id). This is the ONE
            # remaining place where world_id influences selection, and it
            # is explicitly gated behind allow_tree_mismatch (shadow only).
            worlds = [worlds[0]]
            pin_reason = "session_default_tree_mismatch"
            snap_id = worlds[0]["snapshot_id"]
            log.warning(
                "Tree mismatch for %s: stored snapshot %s != current %s; "
                "using latest active world_id=%d (SHADOW-ONLY fallback, "
                "NOT safe for v2 authority). allow_tree_mismatch must be "
                "False for production authority.",
                project_root, snap_id, current_snap_id,
                worlds[0]["world_id"])

    if not worlds:
        raise NoActiveWorld(
            f"No active v2 World for namespace={namespace_id!r} "
            f"snapshot={snap_id!r}. The current workspace state has no "
            f"completed v2 index. Run 'fw-context index' with v2 enabled.",
            detail={"namespace_id": namespace_id,
                    "snapshot_id": snap_id,
                    "current_snapshot_id": current_snap_id,
                    "tree_mismatch": pin_reason == "session_default_tree_mismatch",
                    "project_root": str(project_root)})

    # Step 4: RISK A fix — NO storage-handle tiebreak for multi-build.
    # Same snapshot + multiple builds = AMBIGUOUS without explicit evidence.
    if len(worlds) == 1:
        wrow = worlds[0]
    elif build_id_hint:
        # Explicit build evidence — find the matching build.
        matching = [w for w in worlds if w["build_identity_sha"] == build_id_hint]
        if len(matching) == 1:
            wrow = matching[0]
        elif len(matching) == 0:
            raise AmbiguousWorld(
                f"build_id_hint {build_id_hint!r} does not match any of "
                f"the {len(worlds)} active builds for namespace="
                f"{namespace_id!r} snapshot={snap_id!r}",
                candidates=[{"world_id": w["world_id"],
                             "build_id": w["build_identity_sha"]}
                            for w in worlds],
                detail={"namespace_id": namespace_id,
                        "snapshot_id": snap_id,
                        "build_id_hint": build_id_hint})
        else:
            # Multiple worlds with same build_id (shouldn't happen —
            # UNIQUE constraint on (ns, snap, build)). Fail closed.
            raise AmbiguousWorld(
                f"Multiple worlds match build_id_hint {build_id_hint!r}",
                candidates=[{"world_id": w["world_id"],
                             "build_id": w["build_identity_sha"]}
                            for w in matching])
    else:
        # RISK A: multiple builds, no evidence → FAIL CLOSED.
        raise AmbiguousWorld(
            f"{len(worlds)} active builds exist for namespace="
            f"{namespace_id!r} snapshot={snap_id!r}, but no build_id_hint "
            f"was provided. A storage handle cannot choose semantic Build "
            f"identity. Provide build_id_hint or specify an explicit "
            f"QueryView.",
            candidates=[{"world_id": w["world_id"],
                         "build_id": w["build_identity_sha"]}
                        for w in worlds],
            detail={"namespace_id": namespace_id,
                    "snapshot_id": snap_id,
                    "build_count": len(worlds)})

    view = QueryView(
        repository_namespace=namespace_id,
        snapshot_id=wrow["snapshot_id"],
        build_id=wrow["build_identity_sha"],
    )
    resolved = resolve_query_view(conn, view)
    return PinnedQueryView(
        view=view,
        pinned_at=time.perf_counter(),
        pin_reason=pin_reason,
        resolved_world=resolved,
    )


# ── Legacy config_hash → build_id bridge (compatibility) ───────────────────

def legacy_config_hash_to_build_id(
    v2_conn: sqlite3.Connection,
    legacy_config_hash: str,
) -> str | None:
    """Map a legacy ``config_hash`` to a v2 ``build_identity_sha``.

    This is the COMPATIBILITY BRIDGE for the migration period (D005).
    The v2 ``reuse_admissions`` table records the legacy ``config_hash``
    per TU admission. By joining reuse_admissions → tu_bindings →
    snapshot_builds, we find the v2 World (and thus build_id) whose TUs
    were compiled with the same config_hash.

    Returns None if no v2 build matches the legacy config_hash.
    """
    if not legacy_config_hash:
        return None
    try:
        row = v2_conn.execute(
            """SELECT DISTINCT sb.build_identity_sha
               FROM reuse_admissions ra
               JOIN tu_bindings tb ON tb.tu_context_id = ra.tu_context_id
               JOIN snapshot_builds sb ON sb.world_id = tb.world_id
               WHERE ra.config_hash = ?
               ORDER BY sb.world_id
               LIMIT 1""",
            (legacy_config_hash,)).fetchone()
        if row:
            return row["build_identity_sha"] if "build_identity_sha" in row.keys() else row[0]
    except sqlite3.Error as e:
        log.debug("legacy_config_hash_to_build_id query failed: %s", e)
    return None


def verify_legacy_equivalence(
    v2_conn: sqlite3.Connection,
    pinned_view: PinnedQueryView,
    legacy_config_hash: str,
) -> bool:
    """Verify that the legacy config_hash maps to the SAME build as the pinned view.

    This is the RISK B fix (D005). Before falling back to legacy, the
    authority router MUST call this to confirm the legacy handler will
    resolve to the same World/Build as the pinned QueryView.

    Returns True ONLY if:
    - legacy_config_hash is non-empty, AND
    - legacy_config_hash maps to a v2 build_id, AND
    - that build_id matches the pinned view's build_id.

    Returns False if equivalence cannot be established (no fallback).
    """
    if not legacy_config_hash:
        return False
    mapped_build_id = legacy_config_hash_to_build_id(v2_conn, legacy_config_hash)
    if mapped_build_id is None:
        return False
    return mapped_build_id == pinned_view.view.build_id


# ── Public API: resolve or reuse a pinned view ──────────────────────────────

def resolve_or_reuse_view(
    project_root: str | Path,
    session_key: str,
    *,
    force_refresh: bool = False,
    build_id_hint: str | None = None,
    allow_tree_mismatch: bool = False,
) -> PinnedQueryView:
    """Resolve a pinned view for *session_key*, reusing if already pinned.

    This is the primary entry point for MCP handlers in M1+:

    - If *session_key* already has a pin AND *force_refresh* is False,
      return the existing pin (P2 — stable session semantics).
    - Else resolve a new ``PinnedQueryView`` and register it.

    The ``session_key`` should be stable for the lifetime of a logical
    session. For MCP, this is typically derived from the MCP session id
    (or a synthetic key if no session id is available).

    Args:
        project_root: Project root path.
        session_key: Opaque session identifier.
        force_refresh: If True, re-resolve even if a pin exists (explicit
            refresh — e.g. after the user runs ``fw-context index``).
        build_id_hint: Optional build_identity_sha for disambiguation.
        allow_tree_mismatch: If True, allow tree-mismatch fallback (shadow
            validation only; default False for production safety).

    Returns:
        A ``PinnedQueryView`` (existing or freshly resolved).

    Raises:
        V2StoreUnavailable, NoActiveWorld, AmbiguousWorld, QueryViewError.
    """
    if not force_refresh:
        existing = get_pinned_view(session_key)
        if existing is not None:
            # Verify the pinned World is still queryable (not retired).
            # If retired, fall through to re-resolve.
            v2_path = _v2_db_path_for_project(Path(project_root).resolve())
            if v2_path.exists():
                from ...store_v2.query import open_query_connection
                conn = open_query_connection(v2_path)
                try:
                    resolve_query_view(conn, existing.view)
                    return existing  # still queryable
                except QueryViewError:
                    pass  # World gone — re-resolve
                finally:
                    conn.close()
            return existing  # can't verify, return as-is

    view = resolve_current_view(
        project_root, build_id_hint=build_id_hint,
        allow_tree_mismatch=allow_tree_mismatch)
    pin_view_for_session(session_key, view)
    return view


def make_session_key(project_root: str | Path,
                     mcp_session_id: str | None = None) -> str:
    """Derive a stable session key for pinning.

    If an MCP session id is available, use it directly. Otherwise, derive
    a synthetic key from the project_root. The synthetic key is stable for
    the process lifetime (same project_root → same key), which provides
    session stability within one process even without a real session id.
    """
    if mcp_session_id:
        return f"mcp:{mcp_session_id}"
    root = str(Path(project_root).resolve())
    return f"project:{root}"
