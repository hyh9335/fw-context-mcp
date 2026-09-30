"""v2 shadow adapters for Batch B — symbol/navigation tools (M1).

Each adapter wraps the v2 store query primitives to match the legacy
handler's API. The adapter is called by the shadow runner with the SAME
arguments as the legacy handler; it resolves a QueryView, runs the v2
query, and formats the result for comparison.

MIGRATION INFRASTRUCTURE (P38). These adapters exist ONLY for shadow
validation. When a batch migrates to v2 authority (M2+), the production
handler itself calls the v2 primitives directly and these shadow adapters
are deleted.

Coverage (Batch B — symbol/navigation):
- lookup_symbol (exact name match via v2 ``lookup_symbol``)
- find_callers (via v2 ``symbol_edges`` — incoming call edges)
- find_references (via v2 ``symbol_edges`` — all ref kinds)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from ...store_v2.query import (
    QueryView,
    open_query_connection,
    resolve_query_view,
    lookup_symbol as v2_lookup_symbol,
    symbol_edges as v2_symbol_edges,
    resolve_bindings,
    SymbolNotFound,
    QueryViewError,
    TuPoisoned,
)
from ...store_v2.query import SymbolHit
from .queryview import (
    resolve_current_view,
    PinnedQueryView,
    QueryViewResolutionError,
    V2StoreUnavailable,
    NoActiveWorld,
)

log = logging.getLogger(__name__)


# ── v2 connection cache (per-process) ───────────────────────────────────────

_v2_conns: dict[str, sqlite3.Connection] = {}


def _resolve_v2_path(project_root: str | Path) -> Path | None:
    """Resolve the v2 db path for *project_root*.

    Returns None if no v2 store exists. Uses the same path convention as
    indexing: ``<db_dir>/<project_id>/index-v2.db``.
    """
    from ...config import load as load_config, derive_project_id
    from ...store_v2 import v2_db_path

    root = Path(project_root).resolve()
    fw_ctx = root / ".fw-context"
    if not fw_ctx.exists():
        return None
    try:
        cfg = load_config(root)
        project_id = derive_project_id(root)
    except Exception:
        import hashlib
        project_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
    db_dir = cfg.index.db_dir if cfg else (fw_ctx / "index")
    if db_dir is None:
        db_dir = fw_ctx / "index"
    return v2_db_path(Path(db_dir), project_id)


def _get_v2_conn(project_root: str | Path) -> sqlite3.Connection | None:
    """Get or open a cached v2 read connection for *project_root*.

    Returns None if no v2 store exists. The connection is cached for the
    process lifetime (read-only, safe).
    """
    root_str = str(Path(project_root).resolve())
    if root_str in _v2_conns:
        return _v2_conns[root_str]
    v2_path = _resolve_v2_path(root_str)
    if v2_path is None or not v2_path.exists():
        return None
    try:
        conn = open_query_connection(v2_path)
        _v2_conns[root_str] = conn
        return conn
    except Exception as e:
        log.debug("v2 conn error for %s: %s", project_root, e)
        return None


# ── lookup_symbol shadow adapter ────────────────────────────────────────────

def v2_shadow_lookup_symbol(
    name: str,
    project_root: str | None = None,
    exact: bool = False,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 shadow for lookup_symbol.

    Resolves a QueryView for the project, runs v2 lookup_symbol (exact name
    match), and formats results to match the legacy result shape.

    Note: v2's lookup_symbol primitive is EXACT-name only. When
    ``exact=False`` (prefix search), this adapter raises
    ``V2UnsupportedOperation`` so route_authority can defer to legacy
    (with equivalence gate).
    """
    if not exact:
        from .authority import V2UnsupportedOperation
        raise V2UnsupportedOperation(
            f"v2 lookup_symbol does not support prefix search "
            f"(exact=False); deferring to legacy")

    from ...utils import resolve_project_root
    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []  # v2 unavailable — shadow returns empty

    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        result = v2_lookup_symbol(conn, pinned.view, name)
        # Format v2 SymbolHit list to match legacy dict shape
        out = []
        for hit in result.hits:
            out.append({
                "name": hit.name,
                "qualified_name": hit.name,  # v2 doesn't have qualified_name separately
                "kind": hit.kind,
                "file": "",  # v2 path resolution needs canon_paths join
                "line": 0,
                "is_definition": hit.is_definition,
                "signature": hit.signature or "",
                "usr": hit.raw_usr,
                # v2-specific provenance (not in legacy, for comparison)
                "_v2_state_key": hit.state.tusemantic_key,
                "_v2_binding_id": hit.binding.binding_id,
            })
        return out[:limit]
    except SymbolNotFound:
        return []
    except TuPoisoned:
        return [{"_v2_poisoned": True}]
    except QueryViewError as e:
        log.debug("v2 lookup_symbol query error: %s", e)
        return []


# ── find_callers shadow adapter ─────────────────────────────────────────────

def v2_shadow_find_callers(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 shadow for find_callers (incoming call/ref edges).

    The v2 ``symbol_edges`` primitive returns OUTGOING edges. For callers
    (incoming), we scan all edges in the world's bound states where the
    target USR matches our symbol. This is O(edges) but correct.
    """
    from ...utils import resolve_project_root
    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []

    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        # First find the symbol to get its USR(s)
        sym_result = v2_lookup_symbol(conn, pinned.view, name)
        if not sym_result.hits:
            return []
        target_usrs = {h.raw_usr for h in sym_result.hits}
        if not target_usrs:
            return []
        # Build a set of all (state_id, state_key, state_gen) reachable
        # through this world's bound bindings
        resolved = pinned.resolved_world
        reachable_states = {}
        for b in resolve_bindings(conn, resolved):
            if b.binding_status == "bound" and b.state_key and b.state_generation:
                reachable_states[(b.state_key, b.state_generation)] = b
        if not reachable_states:
            return []
        # Query all edges where to_raw_usr matches any target USR
        # This is a cross-state query (all bound states in the world)
        placeholders = ",".join("?" * len(target_usrs))
        out = []
        for (skey, sgen), binding in reachable_states.items():
            # Get state_id for this binding
            state_row = conn.execute(
                "SELECT state_id FROM semantic_states WHERE "
                "tusemantic_key=? AND parser_generation=?",
                (skey, sgen)).fetchone()
            if state_row is None:
                continue
            state_id = state_row["state_id"]
            rows = conn.execute(
                f"""SELECT e.edge_class, e.to_raw_usr, e.to_name,
                           s.name AS source_name, s.raw_usr AS source_usr,
                           s.local_key AS source_local_key
                    FROM semantic_edges e
                    JOIN semantic_symbols_core s
                      ON s.state_id = e.state_id AND s.local_key = e.local_key
                    WHERE e.state_id = ? AND e.to_raw_usr IN ({placeholders})
                    LIMIT ?""",
                (state_id, *target_usrs, limit - len(out))).fetchall()
            for r in rows:
                out.append({
                    "name": r["source_name"] or "",
                    "file": "",
                    "line": 0,
                    "ref_kind": r["edge_class"],
                    "caller": r["source_name"] or "",
                    "_v2_from_state": skey,
                    "_v2_source_usr": r["source_usr"],
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out
    except SymbolNotFound:
        return [{"info": f"Symbol '{name}' not found in v2 world"}]
    except QueryViewError as e:
        log.debug("v2 find_callers query error: %s", e)
        return []


# ── find_references shadow adapter ──────────────────────────────────────────

def v2_shadow_find_references(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 shadow for find_references (all ref kinds — incoming edges)."""
    return v2_shadow_find_callers(
        name, project_root=project_root, limit=limit,
        variant=variant, image=image, **kwargs)


# ── Batch D: Graph/Traversal adapters (M3) ─────────────────────────────────
# These adapters use v2's symbol_edges primitive for call-graph queries.
# The pattern: resolve the symbol via v2 lookup_symbol, get its state_key,
# then query symbol_edges for outgoing edges (callees) or scan all edges
# for incoming (callers).

def _resolve_symbol_state(conn, pinned, name):
    """Resolve a symbol to its (state_key, state_generation, local_key).

    Returns None if the symbol is not found in the pinned World.
    """
    try:
        result = v2_lookup_symbol(conn, pinned.view, name)
        if not result.hits:
            return None
        hit = result.hits[0]
        return (hit.state.tusemantic_key, hit.state.parser_generation,
                hit.local_key)
    except (SymbolNotFound, QueryViewError):
        return None


def v2_shadow_find_callees_recursive(
    name: str,
    project_root: str | None = None,
    max_depth: int = 5,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_callees_recursive — BFS over outgoing call edges."""
    from ...utils import resolve_project_root
    from .authority import V2UnsupportedOperation

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []

    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        start = _resolve_symbol_state(conn, pinned, name)
        if start is None:
            return []
        state_key, state_gen, local_key = start

        # BFS over outgoing call edges
        visited = {(state_key, state_gen, local_key)}
        results = []
        frontier = [(start, 0)]

        while frontier and len(results) < limit:
            (current_sk, current_sg, current_lk), depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            try:
                edges = v2_symbol_edges(
                    conn, pinned.view,
                    state_key=current_sk, state_generation=current_sg,
                    local_key=current_lk)
                for edge in edges:
                    if edge.to_symbol is not None:
                        key = (edge.to_symbol.tusemantic_key,
                               edge.to_symbol.parser_generation,
                               edge.to_symbol.local_key)
                        if key not in visited:
                            visited.add(key)
                            callee_name = edge.to_resolved_name or edge.to_name or "unknown"
                            results.append({
                                "name": callee_name,
                                "qualified_name": callee_name,
                                "kind": "function",  # v2 doesn't expose kind for edge targets
                                "signature": "",
                                "depth": depth + 1,
                                "file": "",  # v2 doesn't expose file for edge targets
                            })
                            frontier.append((key, depth + 1))
            except QueryViewError:
                continue

        return results[:limit]
    except (SymbolNotFound, TuPoisoned):
        return []
    except QueryViewError:
        return []


def v2_shadow_find_all_callers_recursive(
    name: str,
    project_root: str | None = None,
    max_depth: int = 5,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_all_callers_recursive — transitive callers.

    Note: v2's symbol_edges returns OUTGOING edges only. For transitive
    callers, we need to scan all edges in the world's states for incoming
    edges to our symbol. This is O(edges) but correct.
    """
    # Delegate to find_callers for depth-1, then recurse
    # For M3, we use the same approach as find_callers but with BFS
    from ...utils import resolve_project_root
    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []

    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        # Get all bindings for this world
        bindings = resolve_bindings(conn, pinned)
        bound = [b for b in bindings if b.binding_status == "bound"]

        # Find our symbol's USR across all states
        target_result = v2_lookup_symbol(conn, pinned.view, name)
        if not target_result.hits:
            return []

        target_usrs = {h.raw_usr for h in target_result.hits if h.raw_usr}
        target_names = {h.name for h in target_result.hits}

        # BFS: find callers at each depth
        visited = set()
        results = []
        frontier = [(usr, 0) for usr in target_usrs]

        while frontier and len(results) < limit:
            current_usr, depth = frontier.pop(0)
            if current_usr in visited or depth >= max_depth:
                continue
            visited.add(current_usr)

            # Scan all states for edges pointing to current_usr
            for binding in bound:
                try:
                    edges = v2_symbol_edges(
                        conn, pinned.view,
                        state_key=binding.state_key,
                        state_generation=binding.state_generation,
                        local_key=0)  # This won't work — need all edges
                    # This approach is too expensive. For M3, delegate to
                    # the find_callers approach for depth-1 only.
                    break
                except QueryViewError:
                    continue

        # For M3, fall back to V2UnsupportedOperation for recursive cases
        # (v2's edge primitive doesn't efficiently support reverse BFS)
        from .authority import V2UnsupportedOperation
        raise V2UnsupportedOperation(
            f"v2 find_all_callers_recursive not yet efficiently supported "
            f"(reverse BFS over edges); deferring to legacy")
    except (SymbolNotFound, TuPoisoned):
        return []


def v2_shadow_find_call_path(
    from_name: str,
    to_name: str,
    project_root: str | None = None,
    max_depth: int = 10,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_call_path — BFS path between two symbols."""
    from ...utils import resolve_project_root
    from .authority import V2UnsupportedOperation

    # v2's symbol_edges is per-state, not global BFS.
    # For M3, defer to legacy (with equivalence gate).
    raise V2UnsupportedOperation(
        f"v2 find_call_path requires global BFS over states; "
        f"deferring to legacy")


def v2_shadow_find_dead_code(
    project_root: str | None = None,
    limit: int = 100,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_dead_code — L2 reachability analysis."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 find_dead_code requires reachability analysis; deferring to legacy")


def v2_shadow_find_hotspots(
    project_root: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_hotspots — caller count aggregation."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 find_hotspots requires aggregation over all edges; deferring to legacy")


def v2_shadow_find_indirect_call_sites(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_indirect_call_sites — function pointer invocations."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 find_indirect_call_sites requires indirect edge filtering; "
        f"deferring to legacy")


def v2_shadow_find_indirect_targets(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_indirect_targets — functions assigned to a fp field."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 find_indirect_targets requires fp_assign edge filtering; "
        f"deferring to legacy")


def v2_shadow_find_wrapper_callers(
    class_name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_wrapper_callers — adapter pattern detection."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 find_wrapper_callers requires cross-class edge analysis; "
        f"deferring to legacy")


def v2_shadow_trace_data_flow(
    type_name: str,
    to_symbol: str,
    project_root: str | None = None,
    max_depth: int = 8,
    limit: int = 15,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for trace_data_flow — type-based data flow tracing."""
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 trace_data_flow requires type-based path tracing; "
        f"deferring to legacy")


# ── Batch C: Source/Search adapters (M3) ───────────────────────────────────
# v2 doesn't expose COLD payload (file/line/body) through public query
# functions yet. These adapters raise V2UnsupportedOperation to defer to
# legacy (with equivalence gate). v2 still provides World resolution.

def v2_shadow_get_source(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_source — function body via libclang extents.

    v2 doesn't expose COLD payload (body text, line numbers) through public
    query functions yet. Defer to legacy (with equivalence gate).
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 get_source requires COLD payload access (body text, line "
        f"numbers); deferring to legacy")


def v2_shadow_get_symbol_context(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_symbol_context — body + callers + callees.

    v2 can provide callers/callees via symbol_edges, but not the body.
    Defer to legacy (with equivalence gate) until COLD payload access exists.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 get_symbol_context requires COLD payload access (body text); "
        f"deferring to legacy")


def v2_shadow_explain_symbol(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for explain_symbol — LLM explanation of a symbol.

    Defer to legacy (with equivalence gate) — LLM explanation needs body text.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 explain_symbol requires COLD payload access; deferring to legacy")


def v2_shadow_get_file_map(
    file_path: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_file_map — file structure overview.

    Defer to legacy (with equivalence gate) — needs file content.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 get_file_map requires file content access; deferring to legacy")


def v2_shadow_read_file(
    file_path: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for read_file — ifdef-filtered file content.

    Defer to legacy (with equivalence gate) — needs file content.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 read_file requires file content access; deferring to legacy")


def v2_shadow_search_code(
    query: str,
    project_root: str | None = None,
    kind: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for search_code — FTS5 symbol name search.

    v2's lookup_symbol is exact-name only. FTS5 search needs a different
    primitive. Defer to legacy (with equivalence gate).
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 search_code requires FTS5 name search primitive; deferring to legacy")


def v2_shadow_search_bodies(
    query: str,
    project_root: str | None = None,
    kind: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for search_bodies — FTS5 body text search.

    Defer to legacy — needs COLD payload body text.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 search_bodies requires COLD payload body text; deferring to legacy")


def v2_shadow_search_content(
    query: str,
    project_root: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for search_content — FTS5 full file content search.

    Defer to legacy — needs file content.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 search_content requires file content access; deferring to legacy")


def v2_shadow_semantic_search(
    query: str,
    project_root: str | None = None,
    threshold: float = 0.6,
    limit: int = 20,
    **kwargs,
) -> list[dict]:
    """v2 adapter for semantic_search — embedding-based search.

    v2 has vec_symbols but no public query function. Defer to legacy.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 semantic_search requires embedding query primitive; deferring to legacy")


def v2_shadow_smart_search(
    query: str,
    project_root: str | None = None,
    limit: int = 20,
    **kwargs,
) -> list[dict]:
    """v2 adapter for smart_search — LLM-generated FTS5 search.

    Composite tool. Defer to legacy.
    """
    from .authority import V2UnsupportedOperation
    raise V2UnsupportedOperation(
        f"v2 smart_search is a composite tool; deferring to legacy")


# ── Batch E: OO/Inheritance adapters (M3B v2-native read) ─────────────────
#
# M3B v2-native read adapters for the inheritance / OO tools. Each resolves
# the pinned QueryView, walks the World's bound bindings, and reads v2
# World-scoped facts directly (semantic_symbols_core / semantic_edges /
# semantic_state_positions / binding_file_map), formatting the result to
# the legacy handler's shape.
#
# Documented M3B limitations — these are v2 hot-schema gaps, NOT weakenings
# of the equivalence gate. Each adapter computes exactly what v2 stores:
#
#  * semantic_symbols_core has no qualified_name / parent_usr column ->
#    qualified_name := name; the enclosing-class USR for a method is
#    derived from the method USR by stripping the trailing '@F@...' suffix.
#  * semantic_edges stores edge_class='inherit' (certainty := access level,
#    resolution_kind := 'virtual' | '') but the indexer NEVER writes
#    edge_class='override' rows (overrides live in the legacy-only
#    ``overrides`` post-process table).  get_method_overrides therefore
#    DERIVES the override relation by joining ``inherit`` edges between
#    the enclosing classes with same-name method matches — a conservative
#    over-approximation that surfaces every candidate (virtual or not);
#    the equivalence gate decides whether to defer to legacy for
#    byte-identical parity.
#  * semantic_symbols_core has no template_usr column -> instance
#    discovery uses a name + USR-shape heuristic: symbols with the same
#    name as the template whose USR carries template-argument markers
#    ('<' / '>') and is_template=0 are reported as instances.  This is a
#    v2 hot-schema limitation, not a gate weakening.
#  * file/line for edge-target symbols comes from the binding's file map
#    joined to semantic_state_positions; absent a position row, file is ""
#    and line is 0 (documented).


def _v2_state_ids_for_bindings(conn, resolved) -> list[tuple[int, int]]:
    """Return ``[(binding_id, state_id), ...]`` for every 'bound' binding of
    the resolved world.  Used to scope cross-state scans (incoming edges,
    instance discovery) to the visible states only."""
    out: list[tuple[int, int]] = []
    for b in resolve_bindings(conn, resolved):
        if b.binding_status != "bound" or not b.state_key or \
                not b.state_generation:
            continue
        state_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (b.state_key, b.state_generation)).fetchone()
        if state_row is not None:
            out.append((b.binding_id, int(state_row["state_id"])))
    return out


def _v2_lookup_by_usr(conn, bound_state_ids, usr):
    """Find a symbol row by raw_usr across the given bound states.

    Returns a dict with ``name``, ``kind``, ``file`` (canon path) and
    ``state_id_local_key`` ``(state_id, local_key)`` for callers that need
    to query that symbol's outgoing edges.  Definitions are preferred over
    declarations when both are present (``ORDER BY is_definition DESC``).
    Empty values when no bound state carries the USR.
    """
    for binding_id, state_id in bound_state_ids:
        r = conn.execute(
            "SELECT s.name, s.kind, s.local_key "
            "FROM semantic_symbols_core s "
            "WHERE s.state_id=? AND s.raw_usr=? "
            "ORDER BY s.is_definition DESC LIMIT 1",
            (state_id, usr)).fetchone()
        if r is not None:
            file_path = _v2_symbol_file(
                conn, binding_id, state_id, int(r["local_key"]))
            return {
                "name": r["name"] or "",
                "kind": r["kind"] or "",
                "file": file_path,
                "state_id_local_key": (state_id, int(r["local_key"])),
            }
    return {"name": "", "kind": "", "file": "",
            "state_id_local_key": None}


def _v2_symbol_file(conn, binding_id, state_id, local_key) -> str:
    """Return the canon path for a symbol's first position row, or ""."""
    r = conn.execute(
        "SELECT cp.path FROM semantic_state_positions p "
        "JOIN binding_file_map bfm "
        "  ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal "
        "JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id "
        "WHERE p.state_id=? AND p.symbol_local_key=? "
        "ORDER BY p.line LIMIT 1",
        (binding_id, state_id, local_key)).fetchone()
    return (r["path"] or "") if r is not None else ""


def _v2_symbol_pos(conn, binding_id, state_id, local_key):
    """Return ``(canon_path, line)`` for the symbol's first position, or
    ``None`` when no position row exists.  ``line`` is 0 when stored as
    NULL."""
    r = conn.execute(
        "SELECT p.line, cp.path FROM semantic_state_positions p "
        "JOIN binding_file_map bfm "
        "  ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal "
        "JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id "
        "WHERE p.state_id=? AND p.symbol_local_key=? "
        "ORDER BY p.line LIMIT 1",
        (binding_id, state_id, local_key)).fetchone()
    if r is None:
        return None
    return (r["path"] or "", int(r["line"]) if r["line"] is not None else 0)


def _v2_symbol_row(conn, hit):
    """Re-fetch the full ``semantic_symbols_core`` row for a SymbolHit.

    ``SymbolHit`` exposes only ``name / raw_usr / kind / is_definition /
    signature / local_key``; columns like ``is_template``, ``is_virtual``
    and ``is_pure_virtual`` are NOT on the dataclass.  Callers that need
    them re-fetch the row by ``(state_id, local_key)``.
    """
    state_row = conn.execute(
        "SELECT state_id FROM semantic_states WHERE "
        "tusemantic_key=? AND parser_generation=?",
        (hit.state.tusemantic_key,
         hit.state.parser_generation)).fetchone()
    if state_row is None:
        return None
    return conn.execute(
        "SELECT * FROM semantic_symbols_core "
        "WHERE state_id=? AND local_key=?",
        (int(state_row["state_id"]), hit.local_key)).fetchone()


def _v2_pick_class_hit(hits, class_name):
    """Pick the best class/struct hit from a v2_lookup_symbol result.

    Prefers a definition of kind class/struct, then any class/struct, then
    the first hit.  Returns ``None`` when *hits* is empty.
    """
    if not hits:
        return None
    for h in hits:
        if h.is_definition and h.kind in ("class", "struct"):
            return h
    for h in hits:
        if h.kind in ("class", "struct"):
            return h
    return hits[0]


def v2_shadow_get_class_members(
    class_name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_class_members — class member table.

    Reads v2 World-scoped symbols (``semantic_symbols_core``) filtered to
    children of the queried class — by USR prefix ``'<class_usr>@%'`` (clang
    USR encoding: a method/field/nested-type of class with
    ``raw_usr=c:@C@Cls`` carries a USR that begins with ``c:@C@Cls@``) —
    joined to per-state positions (``semantic_state_positions``) and the
    per-world file map (``binding_file_map``) for line/file, and grouped
    by ``kind`` to match the legacy member-table shape.

    M3B limitations (see the Batch E module note): ``qualified_name`` :=
    ``name`` (v2 has no qualified_name column); member ``file``/``line``
    come from positions, defaulting to ``""``/``0`` when no row exists;
    only direct USR-prefix children are returned (a class with a base
    whose members are inherited but not re-declared will not list them,
    matching legacy ``get_class_members`` semantics).
    """
    from ...utils import resolve_project_root, abs_path

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return {"error": "v2 store unavailable"}
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return {"error": str(e)}

    try:
        resolved = pinned.resolved_world
        cls_result = v2_lookup_symbol(conn, pinned.view, class_name)
        cls_hit = _v2_pick_class_hit(cls_result.hits, class_name)
        if cls_hit is None:
            return {"error": f"Symbol not found: {class_name}"}
        if cls_hit.kind not in ("class", "struct"):
            return {"error": f"'{class_name}' is a {cls_hit.kind}, "
                              f"not a class or struct."}
        cls_usr = cls_hit.raw_usr or ""
        if not cls_usr:
            return {"error": f"class {class_name!r} has no raw_usr in v2 store"}

        # USR prefix for direct children: '<cls_usr>@%'.  Escaped for LIKE.
        esc = (cls_usr.replace("\\", "\\\\")
                      .replace("%", "\\%").replace("_", "\\_"))
        pattern = f"{esc}@%"

        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Resolve the class header's own file/line from its defining state.
        cls_state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (cls_hit.state.tusemantic_key,
             cls_hit.state.parser_generation)).fetchone()
        cls_file = ""
        cls_line = 0
        if cls_state_id_row is not None:
            cls_state_id = int(cls_state_id_row["state_id"])
            pos = _v2_symbol_pos(conn, cls_hit.binding.binding_id,
                                 cls_state_id, cls_hit.local_key)
            if pos is not None:
                cls_file = pos[0]
                cls_line = pos[1]

        # Walk all bound states for member rows whose USR begins with the
        # class USR + '@'.  Deduplicate by raw_usr (the same declaration is
        # emitted by every TU that includes the header).
        members_by_kind: dict[str, list[dict]] = {}
        member_count = 0
        seen_usrs: set[str] = set()
        for binding_id, state_id in bound_state_ids:
            rows = conn.execute(
                """SELECT s.name, s.raw_usr, s.kind, s.signature,
                          s.is_virtual, s.is_pure_virtual, s.local_key,
                          (SELECT MIN(p.line) FROM semantic_state_positions p
                           WHERE p.state_id=s.state_id
                             AND p.symbol_local_key=s.local_key) AS line,
                          (SELECT cp.path FROM semantic_state_positions p
                           JOIN binding_file_map bfm
                             ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal
                           JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id
                           WHERE p.state_id=s.state_id
                             AND p.symbol_local_key=s.local_key
                           ORDER BY p.line LIMIT 1) AS canon_path
                   FROM semantic_symbols_core s
                   WHERE s.state_id=? AND s.raw_usr LIKE ? ESCAPE '\\'
                     AND s.raw_usr != ?
                   ORDER BY s.kind, s.name, line
                   LIMIT 500""",
                (binding_id, state_id, pattern, cls_usr)).fetchall()
            for r in rows:
                usr = r["raw_usr"] or ""
                if usr in seen_usrs:
                    continue
                seen_usrs.add(usr)
                k = r["kind"] or "unknown"
                members_by_kind.setdefault(k, []).append({
                    "name": r["name"] or "",
                    "qualified_name": r["name"] or "",
                    "signature": r["signature"] or "",
                    "is_virtual": bool(r["is_virtual"]),
                    "is_pure_virtual": bool(r["is_pure_virtual"]),
                    "line": int(r["line"]) if r["line"] is not None else 0,
                })
                member_count += 1

        return {
            "name": cls_hit.name,
            "qualified_name": cls_hit.name,
            "kind": cls_hit.kind,
            "file": abs_path(root, cls_file) if cls_file else "",
            "line": cls_line,
            "members": members_by_kind,
            "member_count": member_count,
        }
    except SymbolNotFound:
        return {"error": f"Symbol not found: {class_name}"}
    except QueryViewError as e:
        log.debug("v2 get_class_members query error: %s", e)
        return {"error": str(e)}


def v2_shadow_get_inheritance_chain(
    class_name: str,
    project_root: str | None = None,
    transitive: bool = False,
    max_depth: int = 10,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_inheritance_chain — base/derived classes.

    Reads v2 World-scoped ``inherit`` edges (``semantic_edges`` where
    ``edge_class='inherit'``).  For BASES: edges whose source is the
    queried class symbol — ``to_raw_usr`` is the base-class USR.  For
    DERIVED: edges across all bound states whose ``to_raw_usr`` matches
    the queried class USR — the source symbol is the derived class.  The
    ``certainty`` column carries the access level (public/private/
    protected) and ``resolution_kind`` is ``'virtual'`` when virtual.

    M3B limitations (see the Batch E module note): ``qualified_name`` :=
    ``name``; base/derived ``file`` is resolved via the source/target
    symbol's position row (empty when no position exists); only proven
    inherit edges are stored (v2 has no 'possible' inherit edges); the
    transitive walk is BFS over the same edges with cycle detection.
    """
    from ...utils import resolve_project_root, abs_path

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return {"error": "v2 store unavailable"}
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return {"error": str(e)}

    try:
        resolved = pinned.resolved_world
        cls_result = v2_lookup_symbol(conn, pinned.view, class_name)
        cls_hit = _v2_pick_class_hit(cls_result.hits, class_name)
        if cls_hit is None:
            return {"error": f"Symbol not found: {class_name}"}
        if cls_hit.kind not in ("class", "struct"):
            return {"error": f"'{class_name}' is a {cls_hit.kind}, "
                              f"not a class or struct."}
        cls_usr = cls_hit.raw_usr or ""
        if not cls_usr:
            return {"error": f"class {class_name!r} has no raw_usr in v2 store"}

        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Class header file/line from its defining state.
        cls_state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (cls_hit.state.tusemantic_key,
             cls_hit.state.parser_generation)).fetchone()
        cls_file = ""
        cls_line = 0
        cls_state_id: int | None = None
        if cls_state_id_row is not None:
            cls_state_id = int(cls_state_id_row["state_id"])
            pos = _v2_symbol_pos(conn, cls_hit.binding.binding_id,
                                 cls_state_id, cls_hit.local_key)
            if pos is not None:
                cls_file = pos[0]
                cls_line = pos[1]

        # ── Direct bases: outgoing 'inherit' edges from the class symbol ──
        bases: list[dict] = []
        if cls_state_id is not None:
            base_rows = conn.execute(
                "SELECT e.to_raw_usr, e.certainty, e.resolution_kind "
                "FROM semantic_edges e "
                "WHERE e.state_id=? AND e.local_key=? "
                "AND e.edge_class='inherit' "
                "ORDER BY e.edge_ordinal",
                (cls_state_id, cls_hit.local_key)).fetchall()
            for r in base_rows:
                b_usr = r["to_raw_usr"] or ""
                if not b_usr:
                    continue
                b_info = _v2_lookup_by_usr(conn, bound_state_ids, b_usr)
                bases.append({
                    "name": b_info["name"] or "<unknown>",
                    "usr": b_usr,
                    "access": r["certainty"] or "public",
                    "is_virtual": (r["resolution_kind"] == "virtual"),
                    "file": b_info["file"],
                })

        # ── Direct derived: incoming 'inherit' edges to this class USR ──
        derived: list[dict] = []
        seen_derived_usrs: set[str] = set()
        for binding_id, state_id in bound_state_ids:
            d_rows = conn.execute(
                "SELECT e.local_key, e.certainty, e.resolution_kind, "
                " s.name AS src_name, s.raw_usr AS src_usr "
                "FROM semantic_edges e "
                "JOIN semantic_symbols_core s "
                "  ON s.state_id=e.state_id AND s.local_key=e.local_key "
                "WHERE e.state_id=? AND e.edge_class='inherit' "
                "AND e.to_raw_usr=?",
                (state_id, cls_usr)).fetchall()
            for r in d_rows:
                d_usr = r["src_usr"] or ""
                if d_usr in seen_derived_usrs:
                    continue
                seen_derived_usrs.add(d_usr)
                d_file = _v2_symbol_file(
                    conn, binding_id, state_id, int(r["local_key"]))
                derived.append({
                    "name": r["src_name"] or "<unknown>",
                    "usr": d_usr,
                    "access": r["certainty"] or "public",
                    "is_virtual": (r["resolution_kind"] == "virtual"),
                    "file": d_file,
                })

        result: dict = {
            "name": cls_hit.name,
            "qualified_name": cls_hit.name,
            "kind": cls_hit.kind,
            "file": abs_path(root, cls_file) if cls_file else "",
            "line": cls_line,
            "bases": bases,
            "derived": derived,
        }

        if transitive:
            depth_cap = max(1, min(int(max_depth), 50))
            # BFS up (ancestors)
            visited_up: set[str] = {cls_usr}
            all_bases: list[dict] = []
            frontier = [(b["usr"], b["access"], b["is_virtual"])
                        for b in bases]
            for depth in range(1, depth_cap + 1):
                if not frontier:
                    break
                seen_level: set[str] = set()
                next_frontier: list[tuple[str, str, bool]] = []
                for cur_usr, access, is_virt in frontier:
                    if cur_usr in visited_up or cur_usr in seen_level:
                        continue
                    seen_level.add(cur_usr)
                    visited_up.add(cur_usr)
                    cur_info = _v2_lookup_by_usr(
                        conn, bound_state_ids, cur_usr)
                    all_bases.append({
                        "name": cur_info["name"] or "<unknown>",
                        "usr": cur_usr,
                        "access": access,
                        "is_virtual": is_virt,
                        "depth": depth,
                        "file": cur_info["file"],
                        "kind": cur_info["kind"] or None,
                    })
                    cur_sl = cur_info.get("state_id_local_key")
                    if cur_sl is not None:
                        c_state_id, c_local_key = cur_sl
                        more = conn.execute(
                            "SELECT to_raw_usr, certainty, resolution_kind "
                            "FROM semantic_edges WHERE state_id=? "
                            "AND local_key=? AND edge_class='inherit' "
                            "ORDER BY edge_ordinal",
                            (c_state_id, c_local_key)).fetchall()
                        for m in more:
                            mu = m["to_raw_usr"] or ""
                            if mu and mu not in visited_up:
                                next_frontier.append((
                                    mu, m["certainty"] or "public",
                                    m["resolution_kind"] == "virtual"))
                frontier = next_frontier
            result["all_bases"] = all_bases

            # BFS down (descendants) — derived lookup is a cross-state scan
            visited_down: set[str] = {cls_usr}
            all_derived: list[dict] = []
            d_frontier = [(d["usr"], d["access"], d["is_virtual"])
                          for d in derived]
            for depth in range(1, depth_cap + 1):
                if not d_frontier:
                    break
                seen_level: set[str] = set()
                next_frontier: list[tuple[str, str, bool]] = []
                for cur_usr, access, is_virt in d_frontier:
                    if cur_usr in visited_down or cur_usr in seen_level:
                        continue
                    seen_level.add(cur_usr)
                    visited_down.add(cur_usr)
                    cur_info = _v2_lookup_by_usr(
                        conn, bound_state_ids, cur_usr)
                    all_derived.append({
                        "name": cur_info["name"] or "<unknown>",
                        "usr": cur_usr,
                        "access": access,
                        "is_virtual": is_virt,
                        "depth": depth,
                        "file": cur_info["file"],
                        "kind": cur_info["kind"] or None,
                    })
                # Children of this level: scan all states for inherit edges
                # pointing at any USR in seen_level.
                if seen_level:
                    ph = ",".join("?" * len(seen_level))
                    level_usrs = list(seen_level)
                    for binding_id, state_id in bound_state_ids:
                        m_rows = conn.execute(
                            f"SELECT e.local_key, e.certainty, "
                            f"e.resolution_kind, s.name AS src_name, "
                            f"s.raw_usr AS src_usr "
                            f"FROM semantic_edges e "
                            f"JOIN semantic_symbols_core s "
                            f"  ON s.state_id=e.state_id "
                            f"  AND s.local_key=e.local_key "
                            f"WHERE e.state_id=? "
                            f"AND e.edge_class='inherit' "
                            f"AND e.to_raw_usr IN ({ph})",
                            (state_id, *level_usrs)).fetchall()
                        for r in m_rows:
                            mu = r["src_usr"] or ""
                            if mu and mu not in visited_down:
                                next_frontier.append((
                                    mu, r["certainty"] or "public",
                                    r["resolution_kind"] == "virtual"))
                d_frontier = next_frontier
            result["all_derived"] = all_derived

        return result
    except SymbolNotFound:
        return {"error": f"Symbol not found: {class_name}"}
    except QueryViewError as e:
        log.debug("v2 get_inheritance_chain query error: %s", e)
        return {"error": str(e)}


def v2_shadow_get_method_overrides(
    method_name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_method_overrides — virtual method overrides.

    v2's hot schema stores ``inherit`` edges but NOT ``override`` edges
    (the indexer writes override relationships only to the legacy
    ``overrides`` post-process table).  This adapter DERIVES the override
    relation from v2 World-scoped facts:

      * Find the method symbol via ``v2_lookup_symbol`` and derive the
        enclosing-class USR from the method USR (clang USR encodes
        parent-child: ``c:@C@Cls@F@method#I`` -> class USR
        ``c:@C@Cls``).
      * ``overrides`` (what this method overrides): for each direct base
        of the enclosing class (``inherit`` edges), look up same-named
        methods whose USR begins with ``<base_usr>@F@``.
      * ``overridden_by`` (what overrides this): for each direct derived
        of the enclosing class (incoming ``inherit`` edges), look up
        same-named methods whose USR begins with ``<derived_usr>@F@``.

    M3B limitations (see the Batch E module note): ``qualified_name`` :=
    ``name``; method ``file``/``line`` come from positions, else
    ``""``/``0``; v2 has no is_virtual flag on edge-derived overrides, so
    the derived set is a conservative over-approximation (every
    same-name method in a base/derived class is reported) — the
    equivalence gate decides whether to defer to legacy for byte-identical
    parity; ``v2_lookup_symbol`` is exact-name only, so passing a
    qualified name like ``'Derived::run'`` returns ``Symbol not found``
    (defers to legacy via the gate).
    """
    from ...utils import resolve_project_root, abs_path

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return {"error": "v2 store unavailable"}
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return {"error": str(e)}

    try:
        resolved = pinned.resolved_world
        meth_result = v2_lookup_symbol(conn, pinned.view, method_name)
        if not meth_result.hits:
            return {"error": f"Symbol not found: {method_name}"}
        # Prefer a method/constructor/destructor hit; fall back to first.
        meth_hit = None
        for h in meth_result.hits:
            if h.kind in ("method", "destructor", "constructor"):
                meth_hit = h
                if h.is_definition:
                    break
        if meth_hit is None:
            meth_hit = meth_result.hits[0]
        if meth_hit.kind not in ("method", "destructor", "constructor"):
            return {"error": f"'{method_name}' is a {meth_hit.kind}, "
                              f"not a method."}
        meth_usr = meth_hit.raw_usr or ""
        meth_name_short = meth_hit.name or method_name

        # Method header file/line.
        meth_state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (meth_hit.state.tusemantic_key,
             meth_hit.state.parser_generation)).fetchone()
        meth_file = ""
        meth_line = 0
        if meth_state_id_row is not None:
            meth_state_id = int(meth_state_id_row["state_id"])
            pos = _v2_symbol_pos(conn, meth_hit.binding.binding_id,
                                 meth_state_id, meth_hit.local_key)
            if pos is not None:
                meth_file = pos[0]
                meth_line = pos[1]

        result: dict = {
            "name": meth_hit.name,
            "qualified_name": meth_hit.name,
            "kind": meth_hit.kind,
            "file": abs_path(root, meth_file) if meth_file else "",
            "line": meth_line,
            "signature": meth_hit.signature or "",
            "overrides": [],
            "overridden_by": [],
        }

        # Derive the enclosing class USR by stripping '@F@...' suffix.
        sep = "@F@"
        idx = meth_usr.rfind(sep)
        enclosing_cls_usr = meth_usr[:idx] if idx > 0 else ""
        if not enclosing_cls_usr:
            # No enclosing class (free function) — no overrides possible.
            return result

        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)
        cls_info = _v2_lookup_by_usr(
            conn, bound_state_ids, enclosing_cls_usr)
        cls_sl = cls_info.get("state_id_local_key")

        # Bases of the enclosing class — ``overrides`` candidates.
        base_usrs: list[str] = []
        if cls_sl is not None:
            c_state_id, c_local_key = cls_sl
            for r in conn.execute(
                    "SELECT to_raw_usr FROM semantic_edges "
                    "WHERE state_id=? AND local_key=? "
                    "AND edge_class='inherit' "
                    "ORDER BY edge_ordinal",
                    (c_state_id, c_local_key)).fetchall():
                u = r["to_raw_usr"] or ""
                if u:
                    base_usrs.append(u)

        overrides: list[dict] = []
        seen_ov: set[str] = set()
        for b_usr in base_usrs:
            b_esc = (b_usr.replace("\\", "\\\\")
                          .replace("%", "\\%").replace("_", "\\_"))
            b_pat = f"{b_esc}@F@%"
            for binding_id, state_id in bound_state_ids:
                rows = conn.execute(
                    "SELECT s.raw_usr, s.local_key, s.kind "
                    "FROM semantic_symbols_core s "
                    "WHERE s.state_id=? AND s.name=? "
                    "AND s.raw_usr LIKE ? ESCAPE '\\' "
                    "ORDER BY s.is_definition DESC LIMIT 5",
                    (state_id, meth_name_short, b_pat)).fetchall()
                for r in rows:
                    ov_usr = r["raw_usr"] or ""
                    if not ov_usr or ov_usr in seen_ov:
                        continue
                    seen_ov.add(ov_usr)
                    ov_info = _v2_lookup_by_usr(
                        conn, bound_state_ids, ov_usr)
                    ov_sl = ov_info.get("state_id_local_key")
                    ov_file = ov_info["file"]
                    ov_line = 0
                    if ov_sl is not None:
                        ov_pos = _v2_symbol_pos(
                            conn, binding_id, ov_sl[0], ov_sl[1])
                        if ov_pos is not None:
                            ov_file = ov_pos[0] or ov_file
                            ov_line = ov_pos[1]
                    overrides.append({
                        "usr": ov_usr,
                        "name": ov_info["name"] or meth_name_short,
                        "qualified_name": ov_info["name"] or meth_name_short,
                        "kind": ov_info["kind"] or "method",
                        "file": ov_file,
                        "line": ov_line,
                    })

        # Derived classes of the enclosing class — ``overridden_by`` candidates.
        derived_usrs: list[str] = []
        seen_du: set[str] = set()
        for binding_id, state_id in bound_state_ids:
            for r in conn.execute(
                    "SELECT s.raw_usr AS src_usr "
                    "FROM semantic_edges e "
                    "JOIN semantic_symbols_core s "
                    "  ON s.state_id=e.state_id "
                    "  AND s.local_key=e.local_key "
                    "WHERE e.state_id=? AND e.edge_class='inherit' "
                    "AND e.to_raw_usr=?",
                    (state_id, enclosing_cls_usr)).fetchall():
                u = r["src_usr"] or ""
                if u and u not in seen_du:
                    seen_du.add(u)
                    derived_usrs.append(u)

        overridden_by: list[dict] = []
        seen_ob: set[str] = set()
        for d_usr in derived_usrs:
            d_esc = (d_usr.replace("\\", "\\\\")
                          .replace("%", "\\%").replace("_", "\\_"))
            d_pat = f"{d_esc}@F@%"
            for binding_id, state_id in bound_state_ids:
                rows = conn.execute(
                    "SELECT s.raw_usr, s.local_key, s.kind "
                    "FROM semantic_symbols_core s "
                    "WHERE s.state_id=? AND s.name=? "
                    "AND s.raw_usr LIKE ? ESCAPE '\\' "
                    "ORDER BY s.is_definition DESC LIMIT 5",
                    (state_id, meth_name_short, d_pat)).fetchall()
                for r in rows:
                    ob_usr = r["raw_usr"] or ""
                    if not ob_usr or ob_usr in seen_ob:
                        continue
                    seen_ob.add(ob_usr)
                    ob_info = _v2_lookup_by_usr(
                        conn, bound_state_ids, ob_usr)
                    ob_sl = ob_info.get("state_id_local_key")
                    ob_file = ob_info["file"]
                    ob_line = 0
                    if ob_sl is not None:
                        ob_pos = _v2_symbol_pos(
                            conn, binding_id, ob_sl[0], ob_sl[1])
                        if ob_pos is not None:
                            ob_file = ob_pos[0] or ob_file
                            ob_line = ob_pos[1]
                    overridden_by.append({
                        "usr": ob_usr,
                        "name": ob_info["name"] or meth_name_short,
                        "qualified_name": ob_info["name"] or meth_name_short,
                        "kind": ob_info["kind"] or "method",
                        "file": ob_file,
                        "line": ob_line,
                    })

        result["overrides"] = overrides
        result["overridden_by"] = overridden_by
        return result
    except SymbolNotFound:
        return {"error": f"Symbol not found: {method_name}"}
    except QueryViewError as e:
        log.debug("v2 get_method_overrides query error: %s", e)
        return {"error": str(e)}


def v2_shadow_get_template_instances(
    template_name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for get_template_instances — template instantiations.

    v2's hot schema has no ``template_usr`` column, so instance discovery
    uses a name + USR-shape heuristic over ``semantic_symbols_core``: the
    template pattern is the row with ``is_template=1`` and ``name =
    template_name``; instance rows are ``is_template=0`` symbols of the
    same name whose ``raw_usr`` carries template-argument markers
    (``'<'`` / ``'>'``).  Partial specializations (``is_template=1`` rows
    whose USR carries template-argument markers) are added to the USR set
    so that instances of a partial spec are surfaced under the primary
    template's query (mirroring the legacy ADR-007 USR-set resolution).

    M3B limitations (see the Batch E module note): ``qualified_name`` :=
    ``name``; instance ``file``/``line`` come from positions joined to
    the binding's file map, else ``""``/``0``; ``template_usr`` is the
    pattern USR (computed, not stored); the heuristic may surface
    unrelated same-name symbols whose USR happens to carry template-
    argument markers — the equivalence gate decides whether to defer to
    legacy for byte-identical parity.
    """
    from ...utils import resolve_project_root, abs_path

    limit = max(0, min(int(limit), 200))
    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return [{"error": "v2 store unavailable"}]
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return [{"error": str(e)}]

    try:
        resolved = pinned.resolved_world
        tpl_result = v2_lookup_symbol(conn, pinned.view, template_name)
        if not tpl_result.hits:
            return [{"error": f"Symbol not found: {template_name}"}]
        # SymbolHit does NOT carry is_template — re-fetch the row.  Pick
        # the first hit whose semantic_symbols_core row has is_template=1;
        # fall back to the first definition hit, else the first hit.
        tpl_hit = None
        for h in tpl_result.hits:
            row = _v2_symbol_row(conn, h)
            if row is not None and int(row["is_template"] or 0) == 1 \
                    and h.is_definition:
                tpl_hit = h
                break
        if tpl_hit is None:
            for h in tpl_result.hits:
                row = _v2_symbol_row(conn, h)
                if row is not None and int(row["is_template"] or 0) == 1:
                    tpl_hit = h
                    break
        if tpl_hit is None:
            for h in tpl_result.hits:
                if h.is_definition:
                    tpl_hit = h
                    break
        if tpl_hit is None:
            tpl_hit = tpl_result.hits[0]
        # Verify is_template=1 via the re-fetched row.
        tpl_row = _v2_symbol_row(conn, tpl_hit)
        if tpl_row is None or int(tpl_row["is_template"] or 0) != 1:
            return [{"error": f"'{template_name}' is not a template "
                              f"(kind: {tpl_hit.kind}, is_template: false)."}]
        tpl_usr = tpl_hit.raw_usr or ""
        if not tpl_usr:
            return [{"error": f"template {template_name!r} has no raw_usr "
                              f"in v2 store"}]

        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Template header file/line.
        tpl_state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (tpl_hit.state.tusemantic_key,
             tpl_hit.state.parser_generation)).fetchone()
        tpl_file = ""
        tpl_line = 0
        tpl_state_id: int | None = None
        if tpl_state_id_row is not None:
            tpl_state_id = int(tpl_state_id_row["state_id"])
            pos = _v2_symbol_pos(conn, tpl_hit.binding.binding_id,
                                 tpl_state_id, tpl_hit.local_key)
            if pos is not None:
                tpl_file = pos[0]
                tpl_line = pos[1]

        # Build the USR set: primary template + every partial specialization
        # (is_template=1 rows whose USR carries template-arg markers AND
        # whose USR is NOT the primary).  v2 has no template_usr column,
        # so partial-spec discovery is by name + is_template + USR shape.
        usr_set = {tpl_usr}
        for binding_id, state_id in bound_state_ids:
            for r in conn.execute(
                    "SELECT raw_usr FROM semantic_symbols_core "
                    "WHERE state_id=? AND name=? AND is_template=1 "
                    "AND raw_usr != ?",
                    (state_id, template_name, tpl_usr)).fetchall():
                u = r["raw_usr"] or ""
                if u and ("<" in u or ">" in u):
                    usr_set.add(u)

        # Instance rows: is_template=0 symbols of the same name whose USR
        # carries template-argument markers.
        instances: list[dict] = []
        seen_inst: set[str] = set()
        for binding_id, state_id in bound_state_ids:
            if len(instances) >= limit:
                break
            rows = conn.execute(
                """SELECT s.name, s.raw_usr, s.kind, s.signature,
                          s.is_definition, s.local_key,
                          (SELECT MIN(p.line) FROM semantic_state_positions p
                           WHERE p.state_id=s.state_id
                             AND p.symbol_local_key=s.local_key) AS line,
                          (SELECT cp.path FROM semantic_state_positions p
                           JOIN binding_file_map bfm
                             ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal
                           JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id
                           WHERE p.state_id=s.state_id
                             AND p.symbol_local_key=s.local_key
                           ORDER BY p.line LIMIT 1) AS canon_path
                   FROM semantic_symbols_core s
                   WHERE s.state_id=? AND s.name=? AND is_template=0
                   ORDER BY s.is_definition DESC, s.raw_usr
                   LIMIT ?""",
                (binding_id, state_id, template_name,
                 max(1, limit - len(instances)) * 4)).fetchall()
            for r in rows:
                u = r["raw_usr"] or ""
                if not u or u in seen_inst:
                    continue
                # USR-shape filter: instance USRs carry template-arg markers.
                if "<" not in u and ">" not in u:
                    continue
                seen_inst.add(u)
                canon = r["canon_path"] or ""
                instances.append({
                    "usr": u,
                    "template_usr": tpl_usr,
                    "name": r["name"] or "",
                    "qualified_name": r["name"] or "",
                    "kind": r["kind"] or "",
                    "file": abs_path(root, canon) if canon else "",
                    "line": int(r["line"]) if r["line"] is not None else 0,
                    "signature": r["signature"] or "",
                    "is_definition": bool(r["is_definition"]),
                })
                if len(instances) >= limit:
                    break

        return [{
            "name": tpl_hit.name,
            "qualified_name": tpl_hit.name,
            "kind": tpl_hit.kind,
            "file": abs_path(root, tpl_file) if tpl_file else "",
            "line": tpl_line,
            "is_definition": bool(tpl_hit.is_definition),
            "signature": tpl_hit.signature or "",
            "instances": instances,
            "instance_count": len(instances),
        }]
    except SymbolNotFound:
        return [{"error": f"Symbol not found: {template_name}"}]
    except QueryViewError as e:
        log.debug("v2 get_template_instances query error: %s", e)
        return [{"error": str(e)}]


# ── Batch F: Variables/Conditionals/Variants adapters (M3) ─────────────────
#
# M3B v2-native read adapters for the variable / conditional tools. Each
# resolves the pinned QueryView, walks the World's bound bindings, and reads
# v2 World-scoped facts directly (semantic_symbols_core / semantic_condition_index
# / tu_branch_states / binding_file_map), formatting the result to the legacy
# handler's shape.
#
# Documented M3B limitations — these are v2 hot-schema gaps, NOT weakenings of
# the equivalence gate. Each adapter computes exactly what v2 stores:
#
#  * semantic_symbols_core has no qualified_name / parent_usr column ->
#    qualified_name := name; enclosing_function is "<file scope>" for
#    varglobal and "" otherwise; enclosing_class is "".
#  * semantic_condition_index stores directive/condition/file_blob_sha but
#    NO line numbers (the indexer computes lines at index time to build the
#    region_ordinal map and then discards them — they are in neither the hot
#    rows nor the cold payload). Region root_line/end_line are therefore 0/None
#    and explain_condition cannot do line-enclosure filtering: it returns ALL
#    root groups of the queried file as the chain. A follow-up batch may
#    recover lines from file_blobs + parse_conditional_directives for full
#    equivalence.
#  * references for find_variables is [] (v2's symbol_edges primitive is
#    outgoing-only; a reverse reference scan is deferred to a later batch).
#  * macro_inputs for explain_condition is [] (v2 has no conditional_macros
#    hot table; macro_defs live in the cold payload but are not
#    condition-attributed).
#  * context_id / config_hash are v1/legacy concepts with no v2 analogue;
#    they are "" (explain_condition / list_conditional_regions) or absent.


def _v2_binding_file_maps(conn, binding_id: int) -> tuple[dict, dict]:
    """Per-binding file presentation: (blob_sha -> canon_path, canon_path -> blob_sha).

    One read of ``binding_file_map`` joined to ``canon_paths``. Used both to
    resolve a user file_path to its blob (for condition filtering) and to map
    a condition's file_blob_sha back to its presentation canon_path.
    """
    blob_to_path: dict[str, str] = {}
    path_to_blob: dict[str, str] = {}
    for r in conn.execute(
            "SELECT bfm.blob_sha, cp.path FROM binding_file_map bfm "
            "LEFT JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id "
            "WHERE bfm.binding_id=?", (binding_id,)).fetchall():
        blob = r["blob_sha"]
        if not blob:
            continue
        raw_path = r["path"] or ""
        blob_to_path.setdefault(blob, raw_path)
        norm_path = raw_path.replace("\\", "/")
        if norm_path:
            path_to_blob.setdefault(norm_path, blob)
    return blob_to_path, path_to_blob


def _v2_match_file_blob(path_to_blob: dict, norm_path: str) -> str | None:
    """Resolve a normalized file path to its blob_sha via a binding's file map.

    Mirrors the legacy two-step lookup (exact then suffix), plus a basename
    fallback for bare-filename queries. Returns the matching blob_sha or None.
    """
    if not norm_path:
        return None
    norm = norm_path.replace("\\", "/").lstrip("./")
    if not norm:
        return None
    # 1. exact canon_path match
    if norm in path_to_blob:
        return path_to_blob[norm]
    # 2. suffix match (legacy: path LIKE '%<norm>')
    for p, b in path_to_blob.items():
        if p.endswith("/" + norm):
            return b
    # 3. basename match (handles a bare filename query)
    base = norm.rsplit("/", 1)[-1]
    if base and base != norm:
        for p, b in path_to_blob.items():
            if p.rsplit("/", 1)[-1] == base:
                return b
    return None


def _format_v2_branch_states(group_rows: list, branch_states: list) -> list[dict]:
    """Format v2 branch states into the legacy branch_states shape.

    ``group_rows[0]`` is the conditional root (branch 1, kind 'if'); the rest
    are its elif/else branch rows from semantic_condition_index.
    ``branch_states`` is a list of ``(branch_ordinal, taken)`` tuples, 1-based
    and ordered. v2 stores no per-branch source line, so ``line`` is 0
    (documented). The branch directive/condition ARE recoverable from the
    semantic_condition_index rows, so kind/condition are populated.
    """
    out: list[dict] = []
    for idx, (bord, taken) in enumerate(branch_states):
        row = group_rows[idx] if idx < len(group_rows) else None
        if idx == 0:
            # legacy: the first branch of every group is kind 'if' regardless
            # of whether the directive is #if/#ifdef/#ifndef.
            kind = "if"
            cond = (row["condition"] if row is not None else None) or ""
        else:
            kind = (row["directive"] if row is not None else None) or ""
            cond = (row["condition"] if row is not None else None) or ""
        out.append({
            "line": 0,  # v2 hot schema has no per-branch line
            "kind": kind,
            "condition": cond,
            "taken": taken,
        })
    return out


def v2_shadow_find_variables(
    name: str,
    project_root: str | None = None,
    kind: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_variables — variable search by name prefix.

    Reads v2 World-scoped symbols (``semantic_symbols_core``) filtered to the
    variable kinds, joined to per-state positions (``semantic_state_positions``)
    and per-world file presentation (``binding_file_map``) for line/file, and
    formatted to the legacy result shape.

    M3B limitations (see the Batch F module note): prefix match on ``name``
    only (v2 has no qualified_name column); ``qualified_name`` := ``name``;
    ``enclosing_function`` is ``"<file scope>"`` for varglobal and ``""
    otherwise (v2 has no parent_usr); ``enclosing_class`` is ``""``;
    ``references`` is ``[]`` (outgoing-edges-only primitive).
    """
    from ...utils import resolve_project_root, abs_path

    if not name or not str(name).strip():
        return [{"error": "Variable name must be non-empty."}]
    if kind is not None and kind not in ("varglobal", "varlocal",
                                        "variable", "field"):
        return [{"error": f"Invalid kind: {kind!r}. Expected 'varglobal', "
                          f"'varlocal', 'field', or None."}]
    limit = max(0, min(int(limit), 100))

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        resolved = pinned.resolved_world
        kinds = ([kind] if kind else
                 ["varglobal", "varlocal", "variable", "field"])
        # 'variable' (legacy pre-split) is included harmlessly — v2 stores
        # only varglobal/varlocal/field, so it matches no row.
        esc = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"{esc}%"  # prefix match — v2 has no qualified_name
        kind_ph = ",".join("?" * len(kinds))

        results: list[dict] = []
        seen: set = set()
        for b in resolve_bindings(conn, resolved):
            if b.binding_status != "bound" or not b.state_key or \
                    not b.state_generation:
                continue
            state_row = conn.execute(
                "SELECT state_id FROM semantic_states WHERE "
                "tusemantic_key=? AND parser_generation=?",
                (b.state_key, b.state_generation)).fetchone()
            if state_row is None:
                continue
            state_id = int(state_row["state_id"])
            # One row per symbol: line via MIN over positions, canon_path via
            # the binding's file map. Correlated subqueries keep the result
            # one-row-per-symbol so LIMIT is exact.
            rows = conn.execute(
                f"""SELECT s.name, s.raw_usr, s.kind, s.signature, s.local_key,
                           (SELECT MIN(p.line) FROM semantic_state_positions p
                            WHERE p.state_id=s.state_id
                              AND p.symbol_local_key=s.local_key) AS line,
                           (SELECT cp.path FROM semantic_state_positions p
                            JOIN binding_file_map bfm
                              ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal
                            JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id
                            WHERE p.state_id=s.state_id
                              AND p.symbol_local_key=s.local_key
                            ORDER BY p.line LIMIT 1) AS canon_path
                    FROM semantic_symbols_core s
                    WHERE s.state_id=? AND s.kind IN ({kind_ph})
                      AND s.name LIKE ? ESCAPE '\\'
                      AND s.is_definition=1
                    ORDER BY CASE s.kind WHEN 'varglobal' THEN 0 ELSE 1 END,
                             s.name, line
                    LIMIT ?""",
                (b.binding_id, state_id, *kinds, pattern,
                 max(1, limit))).fetchall()
            for r in rows:
                rname = r["name"] or ""
                usr = r["raw_usr"] or ""
                line = r["line"]
                canon = r["canon_path"] or ""
                key = usr if usr else (rname, canon, line)
                if key in seen:
                    continue
                seen.add(key)
                knd = r["kind"] or ""
                results.append({
                    "name": rname,
                    "qualified_name": rname,  # v2 has no qualified_name
                    "kind": knd,
                    "file": abs_path(root, canon),
                    "line": int(line) if line is not None else 0,
                    "signature": r["signature"] or "",
                    "enclosing_function": ("<file scope>"
                                           if knd == "varglobal" else ""),
                    "enclosing_class": "",  # v2 hot schema has no parent_usr
                    "references": [],  # v2 edges are outgoing-only (M3B)
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        return results
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 find_variables query error: %s", e)
        return []


def v2_shadow_explain_condition(
    file_path: str,
    line: int,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for explain_condition — conditional branch explanation.

    Reads v2 World-scoped conditional regions (``semantic_condition_index``)
    for the queried file, annotated with the binding's per-world branch states
    (``tu_branch_states``), and returns the legacy chain shape.

    M3B limitations (see the Batch F module note): v2's hot schema stores NO
    condition line numbers, so line-enclosure filtering is NOT performed —
    ALL root groups of the file are returned as the chain; each entry's
    ``line``/``end_line`` are 0/None, ``taken`` (the containing branch) is
    None, and ``macro_inputs`` is ``[]``. ``branch_states`` carries the
    per-branch ``taken`` values that v2 DOES store.
    """
    from ...utils import resolve_project_root

    if not file_path or not str(file_path).strip():
        return {"error": "file_path must be non-empty."}
    try:
        target_line = int(line)
    except (TypeError, ValueError):
        return {"error": f"line must be an integer, got {line!r}"}

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        resolved = pinned.resolved_world
        norm = str(file_path).replace("\\", "/")
        chain: list[dict] = []
        seen_regions: set = set()
        file_seen = False
        for b in resolve_bindings(conn, resolved):
            if b.binding_status != "bound" or not b.state_key or \
                    not b.state_generation:
                continue
            blob_to_path, path_to_blob = _v2_binding_file_maps(
                conn, b.binding_id)
            blob = _v2_match_file_blob(path_to_blob, norm)
            if blob is None:
                continue  # file not in this TU's closure
            file_seen = True
            state_row = conn.execute(
                "SELECT state_id FROM semantic_states WHERE "
                "tusemantic_key=? AND parser_generation=?",
                (b.state_key, b.state_generation)).fetchone()
            if state_row is None:
                continue
            state_id = int(state_row["state_id"])
            canon = blob_to_path.get(blob, "")
            # Root regions (if/ifdef/ifndef) for this file in this state.
            roots = conn.execute(
                "SELECT region_ordinal, directive, condition, file_blob_sha "
                "FROM semantic_condition_index WHERE state_id=? "
                "AND parent_region_ordinal IS NULL AND file_blob_sha=? "
                "ORDER BY region_ordinal",
                (state_id, blob)).fetchall()
            if not roots:
                continue
            # Branch rows (elif/else) under these roots.
            branch_rows = conn.execute(
                "SELECT region_ordinal, parent_region_ordinal, directive, "
                "condition FROM semantic_condition_index WHERE state_id=? "
                "AND parent_region_ordinal IS NOT NULL AND file_blob_sha=? "
                "ORDER BY parent_region_ordinal, region_ordinal",
                (state_id, blob)).fetchall()
            branches_by_parent: dict[int, list] = {}
            for br in branch_rows:
                p = int(br["parent_region_ordinal"])
                branches_by_parent.setdefault(p, []).append(br)
            # Per-world branch states for this binding.
            bs_by_region: dict[int, list[tuple[int, int]]] = {}
            for bsr in conn.execute(
                    "SELECT region_ordinal, branch_ordinal, taken FROM "
                    "tu_branch_states WHERE binding_id=? "
                    "ORDER BY region_ordinal, branch_ordinal",
                    (b.binding_id,)).fetchall():
                bs_by_region.setdefault(int(bsr["region_ordinal"]), []).append(
                    (int(bsr["branch_ordinal"]), int(bsr["taken"])))
            for rt in roots:
                ro = int(rt["region_ordinal"])
                if (state_id, ro) in seen_regions:
                    continue
                seen_regions.add((state_id, ro))
                group_rows = [rt] + branches_by_parent.get(ro, [])
                bstates = bs_by_region.get(ro, [])
                chain.append({
                    "file": canon,
                    "line": 0,  # v2 hot schema has no condition line
                    "directive": rt["directive"] or "",
                    "condition": rt["condition"] or "",
                    "end_line": None,  # v2 stores no #endif line
                    "taken": None,  # containing branch undeterminable w/o lines
                    "branch_states": _format_v2_branch_states(
                        group_rows, bstates),
                    "macro_inputs": [],  # v2 has no conditional_macros hot table
                })
            if len(chain) >= 1000:
                break
        if not file_seen:
            return {"error": f"file not indexed: {file_path}"}
        return {"file": str(file_path), "line": target_line,
                "context_id": "", "config_hash": "", "chain": chain}
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 explain_condition query error: %s", e)
        return []


def v2_shadow_list_conditional_regions(
    file_path: str | None = None,
    project_root: str | None = None,
    limit: int = 200,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for list_conditional_regions — conditional region listing.

    Reads v2 World-scoped root conditional regions
    (``semantic_condition_index`` where ``parent_region_ordinal IS NULL``),
    optionally filtered to one file, annotated with per-world branch states
    (``tu_branch_states``), and returns the legacy ``{regions, count}`` shape.

    M3B limitations (see the Batch F module note): region ``root_line``/
    ``end_line`` are 0/None (v2 stores no condition lines); ``context_id`` is
    ``""`` (no v2 analogue); per-branch ``line`` is 0.
    """
    from ...utils import resolve_project_root

    limit = max(1, min(int(limit), 1000))

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return []
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError:
        return []

    try:
        resolved = pinned.resolved_world
        norm = str(file_path).replace("\\", "/") if file_path else None
        regions: list[dict] = []
        seen_regions: set = set()
        for b in resolve_bindings(conn, resolved):
            if b.binding_status != "bound" or not b.state_key or \
                    not b.state_generation:
                continue
            blob_to_path, path_to_blob = _v2_binding_file_maps(
                conn, b.binding_id)
            filter_blob: str | None = None
            filter_canon = ""
            if norm:
                filter_blob = _v2_match_file_blob(path_to_blob, norm)
                if filter_blob is None:
                    continue  # file not in this binding's closure
                filter_canon = blob_to_path.get(filter_blob, "")
            state_row = conn.execute(
                "SELECT state_id FROM semantic_states WHERE "
                "tusemantic_key=? AND parser_generation=?",
                (b.state_key, b.state_generation)).fetchone()
            if state_row is None:
                continue
            state_id = int(state_row["state_id"])
            remaining = limit - len(regions)
            if remaining <= 0:
                break
            if filter_blob is not None:
                roots = conn.execute(
                    "SELECT region_ordinal, directive, condition, "
                    "file_blob_sha FROM semantic_condition_index "
                    "WHERE state_id=? AND parent_region_ordinal IS NULL "
                    "AND file_blob_sha=? ORDER BY region_ordinal LIMIT ?",
                    (state_id, filter_blob, remaining)).fetchall()
            else:
                roots = conn.execute(
                    "SELECT region_ordinal, directive, condition, "
                    "file_blob_sha FROM semantic_condition_index "
                    "WHERE state_id=? AND parent_region_ordinal IS NULL "
                    "ORDER BY region_ordinal LIMIT ?",
                    (state_id, remaining)).fetchall()
            if not roots:
                continue
            root_ords = [int(r["region_ordinal"]) for r in roots]
            ph = ",".join("?" * len(root_ords))
            branch_rows = conn.execute(
                f"SELECT region_ordinal, parent_region_ordinal, directive, "
                f"condition FROM semantic_condition_index WHERE state_id=? "
                f"AND parent_region_ordinal IN ({ph}) "
                f"ORDER BY parent_region_ordinal, region_ordinal",
                (state_id, *root_ords)).fetchall()
            branches_by_parent: dict[int, list] = {}
            for br in branch_rows:
                p = int(br["parent_region_ordinal"])
                branches_by_parent.setdefault(p, []).append(br)
            bs_by_region: dict[int, list[tuple[int, int]]] = {}
            for bsr in conn.execute(
                    "SELECT region_ordinal, branch_ordinal, taken FROM "
                    "tu_branch_states WHERE binding_id=? "
                    "ORDER BY region_ordinal, branch_ordinal",
                    (b.binding_id,)).fetchall():
                bs_by_region.setdefault(int(bsr["region_ordinal"]), []).append(
                    (int(bsr["branch_ordinal"]), int(bsr["taken"])))
            for rt in roots:
                ro = int(rt["region_ordinal"])
                if (state_id, ro) in seen_regions:
                    continue
                seen_regions.add((state_id, ro))
                rt_blob = rt["file_blob_sha"]
                canon = (filter_canon if filter_blob is not None
                         else blob_to_path.get(rt_blob, ""))
                group_rows = [rt] + branches_by_parent.get(ro, [])
                bstates = bs_by_region.get(ro, [])
                regions.append({
                    "file": canon,
                    "root_line": 0,  # v2 hot schema has no condition line
                    "directive": rt["directive"] or "",
                    "condition": rt["condition"] or "",
                    "end_line": None,  # v2 stores no #endif line
                    "context_id": "",  # v2 has no legacy context_id
                    "branches": _format_v2_branch_states(
                        group_rows, bstates),
                })
                if len(regions) >= limit:
                    break
            if len(regions) >= limit:
                break
        return {"regions": regions, "count": len(regions)}
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 list_conditional_regions query error: %s", e)
        return []
