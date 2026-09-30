"""Authority switching for v2 opt-in read authority (M2).

Implements the OPT-IN v2 read authority path (P14, P22, P24, V2_DEFAULT_READ_AUTHORITY).

When ``FW_CONTEXT_V2_READ_AUTHORITY=1`` is set (or ``[index] v2_read_authority
= true`` in config), participating Batch B tools route through v2 FIRST:

    request → QueryView resolution → v2 query → v2 result (formatted as legacy)
                    ↓ on v2 failure (EXPLICIT, not silent — P3)
                legacy fallback (with logged warning)

When the flag is OFF (default), production is 100% legacy — unchanged.

MIGRATION INFRASTRUCTURE (P38). This module is the authority switch. When
v2 becomes the default (M3), this module is simplified to always-v2, and
the legacy fallback is retired (M5).

The v2 adapters here FORMAT their output to match the legacy handler's
result shape, so callers see no difference. This is the PRESENTATION
COMPATIBILITY layer (P18) — v2 results are adapted to the legacy API
contract, not the other way around.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from .shadow import (
    shadow_v2_enabled,
    get_telemetry,
)
from .queryview import (
    resolve_or_reuse_view,
    make_session_key,
    PinnedQueryView,
    QueryViewResolutionError,
    verify_legacy_equivalence,
)


class V2UnsupportedOperation(Exception):
    """v2 cannot handle this query (e.g. prefix search, regex).

    Raised by v2 adapters when the v2 primitive doesn't support the
    requested operation. This is NOT a v2 failure — it's an explicit
    "v2 can't do this, defer to legacy" signal.

    route_authority treats this as fallback-eligible (subject to the
    same equivalence gate as v2 failures — RISK B applies).
    """


log = logging.getLogger(__name__)


# ── Authority flags ─────────────────────────────────────────────────────────

def v2_read_authority_enabled() -> bool:
    """Whether v2 is the READ authority for participating tools.

    Separate from ``shadow_v2_enabled()``: shadow runs v2 alongside legacy
    for comparison; read authority routes production through v2 FIRST.

    M3 (V2_DEFAULT_READ_AUTHORITY): v2 is now the DEFAULT authority for
    participating tools (those wrapped with ``v2_authority_route``).
    Tools without v2 adapters are unaffected — they call legacy directly.

    Control:
    - ``FW_CONTEXT_V2_READ_AUTHORITY=0`` → explicitly DISABLE v2 authority
      (force legacy sole, even for participating tools)
    - ``FW_CONTEXT_V2_READ_AUTHORITY=1`` → explicitly ENABLE v2 authority
      (same as default, but explicit)
    - Unset → DEFAULT: v2 authority ENABLED (M3 cutover)

    When True AND shadow is also enabled, the shadow still runs (comparing
    v2-authoritative vs legacy-shadow) — useful for validation.
    """
    val = os.environ.get("FW_CONTEXT_V2_READ_AUTHORITY")
    if val == "0":
        return False  # explicit opt-out
    if val == "1":
        return True   # explicit opt-in (same as default)
    return True        # M3 DEFAULT: v2 authority enabled


def authority_mode() -> str:
    """Return the current authority mode for diagnostic purposes.

    - ``"legacy_sole"`` — legacy only, no v2 (default)
    - ``"legacy_shadow_v2"`` — legacy authoritative + v2 shadow (M1)
    - ``"v2_authority_legacy_fallback"`` — v2 first, legacy fallback (M2)
    - ``"v2_authority_shadow_legacy"`` — v2 authoritative + legacy shadow (M2+)
    """
    if v2_read_authority_enabled():
        if shadow_v2_enabled():
            return "v2_authority_shadow_legacy"
        return "v2_authority_legacy_fallback"
    if shadow_v2_enabled():
        return "legacy_shadow_v2"
    return "legacy_sole"


# ── Authority routing ──────────────────────────────────────────────────────

def route_authority(
    tool_name: str,
    legacy_fn: Callable,
    v2_fn: Callable,
    *args,
    format_v2: Callable[[Any], Any] | None = None,
    pinned_view: PinnedQueryView | None = None,
    legacy_config_hash: str | None = None,
    **kwargs,
) -> Any:
    """Route a tool call through the appropriate authority.

    - If v2 read authority is OFF: call legacy_fn (unchanged behavior).
    - If v2 read authority is ON: call v2_fn FIRST; on failure, fall back
      to legacy_fn ONLY IF World equivalence is established (RISK B fix, D005).
      If equivalence cannot be established, return the v2 error.

    RISK B fix (D005): Legacy fallback is permitted ONLY when:
    1. ``pinned_view`` is provided (the caller resolved a QueryView), AND
    2. ``legacy_config_hash`` is provided (the caller knows what legacy
       will resolve to), AND
    3. ``verify_legacy_equivalence`` confirms they map to the same build.
    Otherwise, the v2 error is returned (no unsafe fallback).
    """
    if not v2_read_authority_enabled():
        # Legacy sole authority — unchanged production behavior
        return legacy_fn(*args, **kwargs)

    # Extract project_root for equivalence checking (if needed for fallback)
    project_root = kwargs.get("project_root")

    # v2 authority — try v2 first
    try:
        v2_result = v2_fn(*args, **kwargs)
        if format_v2 is not None:
            v2_result = format_v2(v2_result)
        return v2_result
    except V2UnsupportedOperation as e:
        # v2 explicitly can't handle this query (e.g. prefix search).
        # Fallback-eligible, but still subject to RISK B equivalence gate.
        if _can_safely_fallback(pinned_view, legacy_config_hash, project_root):
            log.info(
                "v2 authority for %s: unsupported operation (%s); deferring "
                "to legacy. Equivalence verified (same build).",
                tool_name, e)
            get_telemetry().fallback_attempts += 1
            return legacy_fn(*args, **kwargs)
        # RISK B: equivalence NOT established — return the v2 error
        log.warning(
            "v2 authority for %s: unsupported operation (%s); NOT deferring "
            "to legacy (equivalence not established). Returning v2 error "
            "(RISK B).",
            tool_name, e)
        raise
    except QueryViewResolutionError as e:
        # v2 World resolution failed.
        # RISK B: can we safely fall back to legacy?
        if _can_safely_fallback(pinned_view, legacy_config_hash, project_root):
            log.warning(
                "v2 read authority for %s failed (QueryView resolution: %s); "
                "falling back to legacy. Equivalence verified (same build).",
                tool_name, e)
            get_telemetry().fallback_attempts += 1
            return legacy_fn(*args, **kwargs)
        # RISK B: equivalence NOT established — return the v2 error
        log.warning(
            "v2 read authority for %s failed (QueryView resolution: %s); "
            "NOT falling back to legacy (equivalence not established). "
            "Returning v2 error to avoid semantic mismatch (RISK B).",
            tool_name, e)
        raise
    except Exception as e:
        # v2 query failed.
        # RISK B: can we safely fall back to legacy?
        if _can_safely_fallback(pinned_view, legacy_config_hash, project_root):
            log.warning(
                "v2 read authority for %s failed (%s: %s); falling back to "
                "legacy. Equivalence verified (same build).",
                tool_name, type(e).__name__, e)
            get_telemetry().fallback_attempts += 1
            return legacy_fn(*args, **kwargs)
        # RISK B: equivalence NOT established — return the v2 error
        log.warning(
            "v2 read authority for %s failed (%s: %s); NOT falling back to "
            "legacy (equivalence not established). Returning v2 error "
            "(RISK B).",
            tool_name, type(e).__name__, e)
        raise


def _can_safely_fallback(
    pinned_view: PinnedQueryView | None,
    legacy_config_hash: str | None,
    project_root: str | None = None,
) -> bool:
    """Check whether legacy fallback is safe (RISK B fix, D005).

    Legacy fallback is safe ONLY when ALL conditions hold:
    1. ``pinned_view`` is provided (explicit QueryView was resolved), AND
    2. ``legacy_config_hash`` is provided (legacy resolves to a known build), AND
    3. The legacy config_hash maps to the SAME build_id as the pinned view.

    If ANY condition is not met, fallback is UNSAFE and must NOT be used.
    """
    if pinned_view is None:
        return False
    if not legacy_config_hash:
        return False
    if not project_root:
        return False
    # Need a v2 connection to verify equivalence.
    from .v2_shadow_adapters import _resolve_v2_path
    from ...store_v2.query import open_query_connection
    v2_path = _resolve_v2_path(project_root)
    if v2_path is None or not v2_path.exists():
        return False
    try:
        conn = open_query_connection(v2_path)
        try:
            return verify_legacy_equivalence(conn, pinned_view, legacy_config_hash)
        finally:
            conn.close()
    except Exception:
        return False


# ── M3 Production routing primitive ────────────────────────────────────────

def v2_authority_route(
    tool_name: str,
    legacy_fn: Callable,
    v2_fn: Callable,
    format_v2: Callable[[Any], Any] | None = None,
    *,
    allow_tree_mismatch: bool = False,
):
    """Decorator that wraps a production MCP handler with v2 authority routing.

    This is the M3 PRODUCTION ROUTING PRIMITIVE (section 23/24).

    When v2 read authority is enabled (default in M3), the wrapped handler:
    1. Resolves project_root from kwargs
    2. Resolves a pinned QueryView (resolve_or_reuse_view)
    3. Gets the legacy config_hash for equivalence checking
    4. Calls route_authority with pinned_view + legacy_config_hash
    5. Returns v2 result (formatted) or legacy result (if equivalence-gated)

    When v2 read authority is disabled, the wrapped handler calls legacy_fn
    directly (unchanged production behavior).

    Args:
        allow_tree_mismatch: False (default, PRODUCTION-SAFE) — v2 resolution
            fails closed on tree mismatch, falling through to legacy.
            True — SHADOW VALIDATION ONLY, allows tree-mismatch fallback
            (NOT safe for v2 authority; use only in test/validation).

    The REAL request path (section 23):
        MCP request → handler (decorated) → resolve QueryView →
        route_authority → v2 query → format_v2 → response

    No tool may independently rediscover current World after a QueryView
    has been pinned (section 23).

    MIGRATION INFRASTRUCTURE (P38). When v2 becomes sole authority (M5),
    this decorator is simplified to always-v2, and the legacy fallback
    is removed.
    """
    import functools
    from .queryview import resolve_or_reuse_view, make_session_key
    from .context import _db_path
    from fw_context_mcp.indexer.db._projects import get_active_config

    @functools.wraps(legacy_fn)
    def wrapper(*args, **kwargs):
        if not v2_read_authority_enabled():
            # Legacy sole authority — unchanged production behavior
            return legacy_fn(*args, **kwargs)

        # Extract project_root from kwargs
        project_root = kwargs.get("project_root")
        if project_root is None:
            # Try to resolve from CWD
            from fw_context_mcp.utils import resolve_project_root
            project_root = resolve_project_root(None)
            kwargs["project_root"] = project_root

        if project_root is None:
            # Can't resolve project_root — fall back to legacy
            return legacy_fn(*args, **kwargs)

        # Step 1: Resolve pinned QueryView for this session
        # PRODUCTION: allow_tree_mismatch=False (fail-closed).
        # If the workspace tree doesn't match the v2 store's snapshot,
        # resolve_current_view raises NoActiveWorld → we fall through to
        # legacy (with equivalence gate). This is CORRECT: v2 authority
        # requires an exact World match. Tree-mismatch is SHADOW-ONLY.
        try:
            session_key = make_session_key(project_root)
            pinned = resolve_or_reuse_view(
                project_root, session_key,
                allow_tree_mismatch=allow_tree_mismatch)
        except QueryViewResolutionError as e:
            # v2 World resolution failed (no exact snapshot match, or no
            # v2 store). M3B: v2 is the READ AUTHORITY — a World
            # resolution failure must propagate VISIBLY, NOT silently
            # execute legacy semantics.
            #
            # Safe legacy fallback requires a pinned QueryView
            # (``_can_safely_fallback`` returns False when ``pinned_view``
            # is None — RISK B fix, D005). Here ``pinned`` was never
            # assigned (resolution raised before the assignment) and
            # ``legacy_config_hash`` is not yet computed (step 2 below),
            # so the guard is always False at this site — the fallback
            # branch never executes and the failure propagates. The
            # guard is kept explicit and structural so this site is
            # recognized as GUARDED (not unguarded) and so a future
            # refactor that resolves ``pinned`` earlier remains
            # correctly gated by the same equivalence contract used in
            # ``route_authority``.
            if _can_safely_fallback(None, None, project_root):
                log.info(
                    "v2 authority for %s: QueryView resolution failed "
                    "(%s) but legacy equivalence verified (same build); "
                    "falling back to legacy.", tool_name, e)
                get_telemetry().fallback_attempts += 1
                return legacy_fn(*args, **kwargs)
            log.warning(
                "v2 authority for %s: QueryView resolution failed (%s); "
                "NOT falling back to legacy (no pinned QueryView — "
                "equivalence cannot be established). Propagating v2 "
                "resolution failure. (M3B: v2 is the read authority; a "
                "missing/stale v2 store is a visible failure, not silent "
                "legacy execution.)", tool_name, e)
            raise

        # Step 2: Get legacy config_hash for equivalence checking
        # Legacy DB schema: build_configs table has config_hash per build
        legacy_config_hash = None
        try:
            from pathlib import Path
            root = Path(project_root).resolve() if not isinstance(project_root, Path) else project_root
            db_path = _db_path(root)
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                try:
                    row = conn.execute(
                        "SELECT config_hash FROM build_configs "
                        "ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        legacy_config_hash = row[0] if isinstance(row, tuple) else row["config_hash"]
                finally:
                    conn.close()
        except Exception:
            pass  # legacy_config_hash stays None

        # Step 3: Route through authority with equivalence-gated fallback
        # Note: project_root is already in kwargs — route_authority extracts it
        return route_authority(
            tool_name,
            legacy_fn,
            v2_fn,
            format_v2=format_v2,
            pinned_view=pinned,
            legacy_config_hash=legacy_config_hash,
            *args, **kwargs,
        )

    return wrapper


# ── v2 result formatters (P18 — presentation compatibility) ────────────────

def format_v2_lookup_symbol(v2_result: list[dict]) -> list[dict]:
    """Format v2 lookup_symbol result to match legacy shape.

    Strips v2-specific fields (``_v2_*``) and ensures all legacy fields
    are present (with defaults for fields v2 doesn't populate yet).
    """
    out = []
    for r in v2_result:
        if not isinstance(r, dict):
            continue
        out.append({
            "name": r.get("name", ""),
            "qualified_name": r.get("qualified_name", r.get("name", "")),
            "kind": r.get("kind", ""),
            "file": r.get("file", ""),
            "line": r.get("line", 0),
            "is_definition": r.get("is_definition", False),
            "signature": r.get("signature", ""),
            "docstring": r.get("docstring", ""),
            "is_template": r.get("is_template", False),
            "is_virtual": r.get("is_virtual", False),
            "is_pure_virtual": r.get("is_pure_virtual", False),
        })
    return out


def format_v2_find_callers(v2_result: list[dict]) -> list[dict]:
    """Format v2 find_callers result to match legacy shape."""
    out = []
    for r in v2_result:
        if not isinstance(r, dict):
            continue
        if r.get("_v2_poisoned"):
            continue  # skip poisoned-TU markers
        out.append({
            "file": r.get("file", ""),
            "line": r.get("line", 0),
            "ref_kind": r.get("ref_kind", "call"),
            "caller": r.get("caller", r.get("name", "")),
            "caller_kind": r.get("caller_kind", ""),
            "certainty": r.get("certainty", "exact"),
        })
    return out


# ── Convenience: route with shadow ─────────────────────────────────────────

def route_with_shadow(
    tool_name: str,
    legacy_fn: Callable,
    v2_fn: Callable,
    comparator: Callable | None = None,
    format_v2: Callable[[Any], Any] | None = None,
    *args, **kwargs,
) -> Any:
    """Route through authority AND run shadow when both are enabled.

    When v2 read authority is ON and shadow is also ON:
    - v2 is authoritative (user gets v2 result)
    - legacy runs as shadow (for comparison/telemetry)

    When v2 read authority is OFF and shadow is ON:
    - legacy is authoritative (user gets legacy result)
    - v2 runs as shadow

    When both are OFF: legacy only (unchanged).
    """
    from .shadow import run_shadow

    if not v2_read_authority_enabled():
        # Legacy authoritative + v2 shadow (M1 mode)
        return run_shadow(
            tool_name, legacy_fn, v2_fn, comparator, *args, **kwargs)[0]

    # v2 authoritative + legacy shadow (M2+ mode)
    # Run legacy as shadow, return v2 result
    def legacy_shadow():
        return legacy_fn(*args, **kwargs)

    legacy_result, shadow = run_shadow(
        f"{tool_name}_legacy_shadow", legacy_shadow, None, None)
    # legacy_result is the shadow; user gets v2
    # Extract pinned_view/legacy_config_hash/project_root for RISK B check
    pinned_view = kwargs.get("pinned_view")
    legacy_config_hash = kwargs.get("legacy_config_hash")
    project_root = kwargs.get("project_root")
    try:
        v2_result = v2_fn(*args, **kwargs)
        if format_v2 is not None:
            v2_result = format_v2(v2_result)
        return v2_result
    except Exception as e:
        # RISK B fix (D005): gate fallback through equivalence check
        if _can_safely_fallback(pinned_view, legacy_config_hash, project_root):
            log.warning(
                "v2 authority for %s failed (%s); returning legacy shadow "
                "result. Equivalence verified (same build).",
                tool_name, e)
            get_telemetry().fallback_attempts += 1
            return legacy_result
        log.warning(
            "v2 authority for %s failed (%s); NOT returning legacy shadow "
            "(equivalence not established). Returning v2 error (RISK B).",
            tool_name, e)
        raise
