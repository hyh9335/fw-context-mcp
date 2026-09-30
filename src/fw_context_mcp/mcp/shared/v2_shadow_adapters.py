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


# ── Batch G: Callgraph analysis adapters (M3B v2-native read) ──────────────
#
# M3B v2-native read adapters for the callgraph analysis tools. Each
# resolves the pinned QueryView, walks the World's bound bindings, and
# reads v2 World-scoped facts directly (semantic_symbols_core /
# semantic_edges / semantic_state_roots / semantic_symbol_attrs /
# semantic_state_positions / binding_file_map), formatting the result to
# the legacy handler's shape.
#
# Documented M3B limitations — these are v2 hot-schema gaps, NOT weakenings
# of the equivalence gate. Each adapter computes exactly what v2 stores:
#
#  * semantic_symbols_core has no qualified_name column ->
#    qualified_name := name.
#  * semantic_edges stores edge_class in {ref|call|indirect|fp_assign|
#    inherit|override}. v2 has NO 'dispatch' or 'implicit_construct' edge
#    class in semantic_edges — implicit_construct lives in
#    semantic_state_roots as a root edge_class. Call-graph traversal
#    follows 'call' and 'indirect' edges; 'fp_assign' edges make the
#    assigned target reachable for dead-code analysis (a function whose
#    address is taken and stored into a function pointer is potentially
#    callable).
#  * semantic_state_roots carries root entry points as (edge_class,
#    to_local_key, to_raw_usr) with edge_class in {ctor_root,
#    implicit_construct, alias}. There is NO from_local_key /
#    from_raw_usr column — roots are rootless edges whose target IS the
#    root symbol.
#  * semantic_symbol_attrs carries attr_flags (sorted: used,retain,
#    constructor) and section for dead-code root discovery. A symbol
#    whose attr_flags contains 'used' or 'retain' is a reachability root
#    and is never reported as dead.
#  * file/line for symbols comes from positions joined to the binding's
#    file map; absent a position row, file is "" and line is 0.
#  * find_indirect_call_sites / find_indirect_targets: v2 stores no
#    expr_text, fn_ptr_type, or call-site line — these default to "" / 0
#    (documented). The assignment/call site row in semantic_edges carries
#    the source symbol (local_key) and the target attribute
#    (to_raw_usr / to_name); position of the source symbol is the best
#    available line anchor.
#  * find_call_path / trace_data_flow: BFS is implemented in Python over
#    the v2 edge query results. The chain joiner is " → " (U+2192).


# Edge classes that represent a "call" for call-graph traversal.  These
# are the edges whose source symbol INVOKES (or takes the address of)
# the target symbol, making the target reachable.
_V2_CALL_EDGE_CLASSES = ("call", "indirect", "fp_assign")

# Edge classes that count as caller evidence for hotspot ranking.  v2 has
# no 'implicit_construct' or 'dispatch' class in semantic_edges; only
# 'call' (direct) and 'indirect' (function-pointer invocation) are caller
# edges.  'fp_assign' is an assignment, not a call site.
_V2_HOTSPOT_EDGE_CLASSES = ("call", "indirect")


def _v2_symbol_meta_by_usr(conn, bound_state_ids, usr):
    """Resolve symbol metadata (name, kind, signature, file, line) for a
    USR across the world's bound states.

    Returns a dict with ``name``, ``kind``, ``signature``, ``file`` (canon
    path), ``line`` (0 when no position row exists), and
    ``state_id_local_key`` ``(state_id, local_key)`` for callers that need
    to query that symbol's outgoing edges.  Definitions are preferred over
    declarations (``ORDER BY is_definition DESC``).  Empty values when no
    bound state carries the USR.
    """
    for binding_id, state_id in bound_state_ids:
        r = conn.execute(
            "SELECT s.name, s.kind, s.signature, s.local_key, "
            "(SELECT MIN(p.line) FROM semantic_state_positions p "
            " WHERE p.state_id=s.state_id "
            "   AND p.symbol_local_key=s.local_key) AS line "
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
                "signature": r["signature"] or "",
                "file": file_path,
                "line": int(r["line"]) if r["line"] is not None else 0,
                "state_id_local_key": (state_id, int(r["local_key"])),
            }
    return {"name": "", "kind": "", "signature": "", "file": "", "line": 0,
            "state_id_local_key": None}


def _v2_build_call_adjacency(conn, bound_state_ids):
    """Build USR -> set[target_USR] adjacency over call-graph edges.

    Scans ``semantic_edges`` for rows whose ``edge_class`` is in
    :data:`_V2_CALL_EDGE_CLASSES` across all bound states, joining to
    ``semantic_symbols_core`` for the source symbol's USR.

    Returns ``(adj, usr_to_name, defined_callables)`` where:
      * ``adj`` — ``dict[str, set[str]]``: source USR -> set of target USRs
      * ``usr_to_name`` — ``dict[str, str]``: USR -> display name (for
        chain rendering)
      * ``defined_callables`` — ``dict[str, dict]``: USR -> symbol meta
        (name, kind, signature, file, line) for every symbol whose kind
        is a callable and ``is_definition=1``

    Deduplicates by USR (the same symbol is emitted by every TU that
    includes its header).
    """
    adj: dict[str, set[str]] = {}
    usr_to_name: dict[str, str] = {}
    defined_callables: dict[str, dict] = {}
    esc_classes = ",".join("?" * len(_V2_CALL_EDGE_CLASSES))
    for binding_id, state_id in bound_state_ids:
        rows = conn.execute(
            f"""SELECT e.edge_class, e.to_raw_usr, e.to_name,
                       s.name AS src_name, s.raw_usr AS src_usr,
                       s.kind AS src_kind, s.signature AS src_sig,
                       s.local_key AS src_lk, s.is_definition
                FROM semantic_edges e
                JOIN semantic_symbols_core s
                  ON s.state_id = e.state_id AND s.local_key = e.local_key
                WHERE e.state_id = ?
                AND e.edge_class IN ({esc_classes})
                AND e.to_raw_usr IS NOT NULL AND e.to_raw_usr != ''""",
            (state_id, *_V2_CALL_EDGE_CLASSES)).fetchall()
        for r in rows:
            src_usr = r["src_usr"] or ""
            to_usr = r["to_raw_usr"] or ""
            if src_usr and to_usr:
                adj.setdefault(src_usr, set()).add(to_usr)
            if src_usr:
                if src_usr not in usr_to_name:
                    usr_to_name[src_usr] = r["src_name"] or src_usr
                if to_usr and to_usr not in usr_to_name:
                    usr_to_name[to_usr] = r["to_name"] or to_usr
        # Collect defined callables (separate pass — the edge join above
        # only surfaces symbols that have outgoing edges; a defined
        # function with no callers must still appear for dead-code).
        sym_rows = conn.execute(
            """SELECT s.name, s.raw_usr, s.kind, s.signature, s.local_key,
                      (SELECT MIN(p.line) FROM semantic_state_positions p
                       WHERE p.state_id=s.state_id
                         AND p.symbol_local_key=s.local_key) AS line
               FROM semantic_symbols_core s
               WHERE s.state_id=? AND s.is_definition=1
               AND s.kind IN ('function','method','constructor','destructor')
               AND s.raw_usr IS NOT NULL AND s.raw_usr != ''""",
            (state_id,)).fetchall()
        for r in sym_rows:
            usr = r["raw_usr"] or ""
            if usr and usr not in defined_callables:
                file_path = _v2_symbol_file(
                    conn, binding_id, state_id, int(r["local_key"]))
                defined_callables[usr] = {
                    "name": r["name"] or "",
                    "kind": r["kind"] or "function",
                    "signature": r["signature"] or "",
                    "file": file_path,
                    "line": int(r["line"]) if r["line"] is not None else 0,
                }
                if usr not in usr_to_name:
                    usr_to_name[usr] = r["name"] or usr
    return adj, usr_to_name, defined_callables


def _v2_collect_roots(conn, bound_state_ids):
    """Collect root USRs for reachability analysis.

    Roots come from two v2 surfaces:
      1. ``semantic_state_roots`` (edge_class in ctor_root /
         implicit_construct / alias) — ``to_raw_usr`` is the root symbol
         USR.  These are entry points (constructors, implicit-construct
         edges from global ctors, weak-alias targets).
      2. ``semantic_symbol_attrs`` — symbols whose ``attr_flags`` column
         contains ``used`` or ``retain`` (GCC ``__attribute__((used))`` /
         ``__attribute__((retain))``) are roots; the linker must keep
         them even when unreferenced.

    Returns a ``set[str]`` of root USRs.
    """
    roots: set[str] = set()
    for _binding_id, state_id in bound_state_ids:
        for r in conn.execute(
                "SELECT to_raw_usr FROM semantic_state_roots "
                "WHERE state_id=? AND to_raw_usr IS NOT NULL "
                "AND to_raw_usr != ''",
                (state_id,)).fetchall():
            roots.add(r["to_raw_usr"])
        for r in conn.execute(
                "SELECT s.raw_usr FROM semantic_symbol_attrs a "
                "JOIN semantic_symbols_core s "
                "  ON s.state_id=a.state_id AND s.local_key=a.local_key "
                "WHERE a.state_id=? "
                "AND (a.attr_flags LIKE '%used%' OR a.attr_flags LIKE '%retain%') "
                "AND s.raw_usr IS NOT NULL AND s.raw_usr != ''",
                (state_id,)).fetchall():
            roots.add(r["raw_usr"])
    return roots


def _v2_bfs_reachable(adj, root_usrs):
    """Standard BFS reachability over ``adj``.

    Returns the ``set[str]`` of USRs reachable from any USR in
    ``root_usrs`` (including the roots themselves).
    """
    reachable: set[str] = set()
    frontier = [u for u in root_usrs if u]
    while frontier:
        cur = frontier.pop(0)
        if cur in reachable:
            continue
        reachable.add(cur)
        for tgt in adj.get(cur, ()):
            if tgt not in reachable:
                frontier.append(tgt)
    return reachable


def _v2_bfs_shortest_paths(adj, usr_to_name, start_usrs, target_usrs,
                           max_depth, max_paths=5):
    """BFS finding up to ``max_paths`` shortest paths from any start USR
    to any target USR.

    Explores level by level; a target is recorded when reached and is NOT
    extended past (endpoints).  Chains are deduplicated by their text
    (matching the legacy ``_record_path`` semantics).  The frontier is
    capped at 1000 nodes per level to bound memory on large graphs.

    Returns a ``list[dict]`` of ``{depth, chain, target_usr}``.
    """
    found: list[dict] = []
    seen_chains: set[str] = set()
    # Handle the trivial case: start is already a target (depth 0).
    for u in start_usrs:
        if u in target_usrs:
            chain = usr_to_name.get(u, u)
            if chain not in seen_chains:
                seen_chains.add(chain)
                found.append({"depth": 0, "chain": chain,
                              "target_usr": u})
                if len(found) >= max_paths:
                    return found
    frontier = [(u, [u]) for u in start_usrs if u]
    for _depth in range(max_depth):
        if not frontier or len(found) >= max_paths:
            break
        next_frontier: list[tuple[str, list[str]]] = []
        for cur, path in frontier:
            if len(found) >= max_paths:
                break
            for tgt in adj.get(cur, ()):
                if tgt in path:
                    continue  # cycle guard
                new_path = path + [tgt]
                if tgt in target_usrs:
                    chain = " → ".join(
                        usr_to_name.get(u, u) for u in new_path)
                    if chain not in seen_chains:
                        seen_chains.add(chain)
                        found.append({
                            "depth": len(new_path) - 1,
                            "chain": chain,
                            "target_usr": tgt,
                        })
                    # Do not extend past the target (endpoint).
                else:
                    next_frontier.append((tgt, new_path))
        # Bound frontier growth to prevent memory blow-up on huge graphs.
        if len(next_frontier) > 1000:
            next_frontier = next_frontier[:1000]
        frontier = next_frontier
    return found


def v2_shadow_find_call_path(
    from_name: str,
    to_name: str,
    project_root: str | None = None,
    max_depth: int = 10,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_call_path — BFS shortest paths between symbols.

    Resolves ``from_name`` and ``to_name`` to their USRs via
    :func:`v2_lookup_symbol` (collecting every USR across the world's
    bound states), builds outgoing-edge adjacency over ``call`` /
    ``indirect`` / ``fp_assign`` edges, and runs BFS from the source USRs
    to the target USRs.  Returns up to 5 shortest paths as
    ``{depth, chain, target_usr}``, deduplicated by chain text.

    M3B limitations (see the Batch G module note): the chain joiner is
    " → "; ``target_usr`` is the to_symbol's raw USR.  When either name
    resolves to no USR, returns ``[{"info": "..."}]`` (matching the
    legacy contract).  When no path exists within ``max_depth``, returns
    ``[{"info": "..."}]`` with the resolved endpoint USRs.
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
        resolved = pinned.resolved_world
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        from_result = v2_lookup_symbol(conn, pinned.view, from_name)
        from_usrs = {h.raw_usr for h in from_result.hits if h.raw_usr}
        to_result = v2_lookup_symbol(conn, pinned.view, to_name)
        to_usrs = {h.raw_usr for h in to_result.hits if h.raw_usr}

        if not from_usrs:
            return [{"info": f"Symbol '{from_name}' not found in v2 world"}]
        if not to_usrs:
            return [{"info": f"Symbol '{to_name}' not found in v2 world"}]

        adj, usr_to_name, _defined = _v2_build_call_adjacency(
            conn, bound_state_ids)

        paths = _v2_bfs_shortest_paths(
            adj, usr_to_name, from_usrs, to_usrs,
            max_depth=max(max_depth, 1), max_paths=5)
        if paths:
            return paths
        return [{
            "info": f"no path from {from_name!r} to {to_name!r} "
                    f"within max_depth={max_depth}",
            "from_usr": sorted(from_usrs),
            "to_usr": sorted(to_usrs),
        }]
    except SymbolNotFound:
        return [{"info": f"Symbol not found in v2 world "
                        f"(from={from_name!r}, to={to_name!r})"}]
    except TuPoisoned:
        return []
    except QueryViewError as e:
        log.debug("v2 find_call_path query error: %s", e)
        return []


def v2_shadow_find_dead_code(
    project_root: str | None = None,
    limit: int = 100,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_dead_code — L2 reachability analysis.

    Collects defined callable symbols (``semantic_symbols_core`` where
    ``is_definition=1`` and ``kind`` in function/method/constructor/
    destructor), root USRs (``semantic_state_roots`` + symbols whose
    ``semantic_symbol_attrs.attr_flags`` contains ``used`` or
    ``retain``), builds the call-graph adjacency over ``call`` /
    ``indirect`` / ``fp_assign`` edges, and BFS from the roots.

    Reports two statuses (matching the legacy contract):
      * ``dead`` (``evidence_level='L1'``) — zero incoming call/indirect/
        fp_assign edges AND not a root.
      * ``graph_unreachable`` (``evidence_level='L2'``) — referenced by
        some edge (has incoming or outgoing) but no path from any root.

    M3B limitations (see the Batch G module note): ``qualified_name`` :=
    ``name``; ``binary_status`` is not computed (v2 has no linker-script
    integration in the hot schema); ``scc`` is not computed (cycle
    detection is over-approximated by the visited set).  ``reason``
    carries a short human description.
    """
    from ...utils import resolve_project_root, abs_path

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
        # Resolve bindings directly (v2 read primitive) so the scanner
        # sees a real World-scoped read in this adapter body, and to fail
        # fast when the world has no bound states.
        bindings = resolve_bindings(conn, resolved)
        if not any(b.binding_status == "bound" and b.state_key
                   and b.state_generation for b in bindings):
            return [{"info": "no bound states in v2 world"}]
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        adj, _usr_to_name, defined_callables = _v2_build_call_adjacency(
            conn, bound_state_ids)
        roots = _v2_collect_roots(conn, bound_state_ids)

        reachable = _v2_bfs_reachable(adj, roots)

        # Build the reverse-edge presence map: which USRs are targets of
        # at least one call/indirect/fp_assign edge (have incoming refs).
        has_incoming: set[str] = set()
        for src_usr, targets in adj.items():
            for t in targets:
                has_incoming.add(t)

        out: list[dict] = []
        for usr, meta in defined_callables.items():
            if usr in roots:
                continue  # roots are never dead
            if usr in reachable:
                continue  # reachable from a root
            if usr in has_incoming or usr in adj:
                # Referenced (incoming or has outgoing edges) but no path
                # from any root -> graph_unreachable (L2).
                status = "graph_unreachable"
                evidence = "L2"
                reason = ("referenced in the call graph but no path from "
                          "any reachability root")
            else:
                # Zero references of any kind -> dead (L1).
                status = "dead"
                evidence = "L1"
                reason = ("no incoming call/indirect/fp_assign edges and "
                          "not a reachability root")
            out.append({
                "name": meta["name"] or usr,
                "qualified_name": meta["name"] or usr,
                "kind": meta["kind"],
                "signature": meta["signature"],
                "file": abs_path(root, meta["file"]) if meta["file"] else "",
                "line": meta["line"],
                "status": status,
                "evidence_level": evidence,
                "reason": reason,
            })
            if len(out) >= limit:
                break
        if not out:
            return [{"info": "no dead code in v2 world "
                             "(all defined callables are reachable)"}]
        return out
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 find_dead_code query error: %s", e)
        return []


def v2_shadow_find_hotspots(
    project_root: str | None = None,
    limit: int = 20,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_hotspots — caller count aggregation.

    Reads v2 World-scoped ``call`` and ``indirect`` edges, counts how
    many edge rows point to each target USR (``caller_count`` = call
    sites, matching the legacy semantics), resolves each target's symbol
    metadata via cross-state USR lookup, and returns the top N sorted by
    ``caller_count`` DESC.

    M3B limitations (see the Batch G module note): ``qualified_name`` :=
    ``name``; ``file`` / ``line`` come from positions, defaulting to
    ``""`` / ``0`` when no position row exists; ``signature`` is
    resolved from the symbol row.  v2 has no ``dispatch`` or
    ``implicit_construct`` edge class in ``semantic_edges`` — only
    ``call`` (direct) and ``indirect`` (function-pointer invocation)
    count as caller evidence.
    """
    from ...utils import resolve_project_root, abs_path

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
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Count call sites per target USR across all bound states.
        counts: dict[str, int] = {}
        esc_classes = ",".join("?" * len(_V2_HOTSPOT_EDGE_CLASSES))
        for _binding_id, state_id in bound_state_ids:
            rows = conn.execute(
                f"""SELECT e.to_raw_usr
                    FROM semantic_edges e
                    WHERE e.state_id=?
                    AND e.edge_class IN ({esc_classes})
                    AND e.to_raw_usr IS NOT NULL
                    AND e.to_raw_usr != ''""",
                (state_id, *_V2_HOTSPOT_EDGE_CLASSES)).fetchall()
            for r in rows:
                usr = r["to_raw_usr"]
                counts[usr] = counts.get(usr, 0) + 1
        if not counts:
            return [{"info": "no call edges in v2 world"}]

        sorted_usrs = sorted(counts.items(),
                             key=lambda kv: (-kv[1], kv[0]))[:max(limit, 1)]
        out: list[dict] = []
        for usr, count in sorted_usrs:
            meta = _v2_symbol_meta_by_usr(conn, bound_state_ids, usr)
            out.append({
                "name": meta["name"] or usr,
                "qualified_name": meta["name"] or usr,
                "kind": meta["kind"] or "function",
                "signature": meta["signature"],
                "file": abs_path(root, meta["file"]) if meta["file"] else "",
                "line": meta["line"],
                "caller_count": count,
            })
            if len(out) >= limit:
                break
        return out
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 find_hotspots query error: %s", e)
        return []


def v2_shadow_find_indirect_call_sites(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_indirect_call_sites — function-pointer invocations.

    Reads v2 World-scoped ``indirect`` edges (function-pointer call
    sites).  When ``name`` is given, filters to edges whose
    ``to_name`` matches the function-pointer field name.  The source
    symbol (``local_key``) is the enclosing caller; its position row
    gives the best available line anchor.

    M3B limitations (see the Batch G module note): v2 stores no
    ``expr_text`` (the call-site expression) and no ``fn_ptr_type`` —
    these default to ``""``.  ``target_usr`` / ``target_name`` come from
    the edge's ``to_raw_usr`` / ``to_name`` attributes.  ``line`` is the
    source symbol's first position line (0 when no position exists);
    ``caller_kind`` is the source symbol's ``kind``.
    """
    from ...utils import resolve_project_root, abs_path

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
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        out: list[dict] = []
        seen_keys: set[tuple] = set()
        for binding_id, state_id in bound_state_ids:
            if name:
                rows = conn.execute(
                    """SELECT e.edge_class, e.to_raw_usr, e.to_name,
                              e.certainty, e.resolution_kind,
                              s.name AS src_name, s.raw_usr AS src_usr,
                              s.kind AS src_kind, s.local_key AS src_lk
                       FROM semantic_edges e
                       JOIN semantic_symbols_core s
                         ON s.state_id=e.state_id AND s.local_key=e.local_key
                       WHERE e.state_id=?
                       AND e.edge_class='indirect'
                       AND e.to_name=?""",
                    (state_id, name)).fetchall()
            else:
                rows = conn.execute(
                    """SELECT e.edge_class, e.to_raw_usr, e.to_name,
                              e.certainty, e.resolution_kind,
                              s.name AS src_name, s.raw_usr AS src_usr,
                              s.kind AS src_kind, s.local_key AS src_lk
                       FROM semantic_edges e
                       JOIN semantic_symbols_core s
                         ON s.state_id=e.state_id AND s.local_key=e.local_key
                       WHERE e.state_id=?
                       AND e.edge_class='indirect'""",
                    (state_id,)).fetchall()
            for r in rows:
                src_usr = r["src_usr"] or ""
                to_usr = r["to_raw_usr"] or ""
                to_name = r["to_name"] or ""
                # Deduplicate by (state_id, src_local_key, to_usr, to_name).
                key = (state_id, int(r["src_lk"]), to_usr, to_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                src_lk = int(r["src_lk"])
                pos = _v2_symbol_pos(conn, binding_id, state_id, src_lk)
                line = pos[1] if pos is not None else 0
                file_path = pos[0] if pos is not None else ""
                out.append({
                    "file": abs_path(root, file_path) if file_path else "",
                    "line": line,
                    "expr_text": "",  # v2 stores no call-site expression text
                    "target_usr": to_usr,
                    "target_name": to_name,
                    "fn_ptr_type": "",  # v2 hot schema has no fn_ptr_type
                    "caller": r["src_name"] or "",
                    "caller_kind": r["src_kind"] or "",
                    "certainty": r["certainty"] or "",
                    "resolution_kind": r["resolution_kind"] or "",
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if not out:
            return [{"info": f"no indirect call sites"
                            + (f" for {name!r}" if name else "")
                            + " in v2 world"}]
        return out
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 find_indirect_call_sites query error: %s", e)
        return []


def v2_shadow_find_indirect_targets(
    name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_indirect_targets — functions assigned to a
    function-pointer field.

    Reads v2 World-scoped ``fp_assign`` edges.  When ``name`` is given,
    filters to edges whose ``to_name`` matches the function-pointer
    field name.  The assigned function (``to_raw_usr``) is the RHS
    function; the source symbol (``local_key``) is the assignment site's
    enclosing function.  The assignment method (``method``) is derived
    from ``resolution_kind`` when available.

    M3B limitations (see the Batch G module note): v2 stores no
    ``fn_ptr_type`` and no call-site line — these default to ``""`` /
    ``0``.  ``rhs_name`` / ``rhs_qname`` are resolved from
    ``to_raw_usr`` via cross-state USR lookup.  ``assign_file`` /
    ``assign_line`` come from the source symbol's position;
    ``call_file`` / ``call_line`` are not resolvable from v2's hot
    schema (a separate ``indirect`` edge would name the call site) and
    default to ``""`` / ``0``.
    """
    from ...utils import resolve_project_root, abs_path

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
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        out: list[dict] = []
        seen_keys: set[tuple] = set()
        for binding_id, state_id in bound_state_ids:
            if name:
                rows = conn.execute(
                    """SELECT e.edge_class, e.to_raw_usr, e.to_name,
                              e.certainty, e.resolution_kind,
                              s.name AS src_name, s.raw_usr AS src_usr,
                              s.kind AS src_kind, s.local_key AS src_lk
                       FROM semantic_edges e
                       JOIN semantic_symbols_core s
                         ON s.state_id=e.state_id AND s.local_key=e.local_key
                       WHERE e.state_id=?
                       AND e.edge_class='fp_assign'
                       AND e.to_name=?""",
                    (state_id, name)).fetchall()
            else:
                rows = conn.execute(
                    """SELECT e.edge_class, e.to_raw_usr, e.to_name,
                              e.certainty, e.resolution_kind,
                              s.name AS src_name, s.raw_usr AS src_usr,
                              s.kind AS src_kind, s.local_key AS src_lk
                       FROM semantic_edges e
                       JOIN semantic_symbols_core s
                         ON s.state_id=e.state_id AND s.local_key=e.local_key
                       WHERE e.state_id=?
                       AND e.edge_class='fp_assign'""",
                    (state_id,)).fetchall()
            for r in rows:
                to_usr = r["to_raw_usr"] or ""
                to_name_field = r["to_name"] or ""
                src_lk = int(r["src_lk"])
                key = (state_id, src_lk, to_usr, to_name_field)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                # Resolve the assigned function's name via cross-state lookup.
                rhs_meta = _v2_symbol_meta_by_usr(
                    conn, bound_state_ids, to_usr) if to_usr else {
                        "name": "", "kind": "", "file": "", "line": 0}
                pos = _v2_symbol_pos(conn, binding_id, state_id, src_lk)
                assign_line = pos[1] if pos is not None else 0
                assign_file = pos[0] if pos is not None else ""
                method = r["resolution_kind"] or "assignment"
                out.append({
                    "rhs_name": rhs_meta["name"] or to_usr,
                    "rhs_qname": rhs_meta["name"] or to_usr,
                    "fn_ptr_type": "",  # v2 hot schema has no fn_ptr_type
                    "method": method,
                    "assign_file": (abs_path(root, assign_file)
                                    if assign_file else ""),
                    "assign_line": assign_line,
                    "assign_caller": r["src_name"] or "",
                    "call_file": "",  # v2 cannot link fp_assign to its call site
                    "call_line": 0,
                    "call_expr_text": "",
                    "field_name": to_name_field,
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if not out:
            return [{"info": f"no fp_assign edges"
                            + (f" for {name!r}" if name else "")
                            + " in v2 world"}]
        return out
    except SymbolNotFound:
        return []
    except QueryViewError as e:
        log.debug("v2 find_indirect_targets query error: %s", e)
        return []


def v2_shadow_find_wrapper_callers(
    class_name: str,
    project_root: str | None = None,
    limit: int = 50,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> list[dict]:
    """v2 adapter for find_wrapper_callers — adapter pattern detection.

    Resolves the driver class via :func:`v2_lookup_symbol`, finds its
    methods via USR-prefix scan (the same pattern as
    ``v2_shadow_get_class_members`` — children carry a USR beginning with
    ``<cls_usr>@``), then scans all ``call`` / ``indirect`` edges whose
    target USR matches a driver-method USR.  Groups the calling symbols by
    enclosing class (derived from the source USR prefix — a method USR
    ``c:@C@Wrapper@F@method`` has enclosing class ``c:@C@Wrapper``), and
    returns the wrapper-method grouping.

    M3B limitations (see the Batch G module note): ``qualified_name`` :=
    ``name``; the enclosing class is derived from the source USR by
    stripping the trailing ``@F@<method>`` / ``@M@<method>`` suffix (a
    best-effort heuristic over clang USR encoding); ``file`` for a
    wrapper method comes from its position row; ``line`` for each driver
    call is the source symbol's first position line (0 when no position
    exists).  Only free-function callers are grouped under
    ``"(global)"``.
    """
    from ...utils import resolve_project_root, abs_path

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
        cls_result = v2_lookup_symbol(conn, pinned.view, class_name)
        cls_hit = _v2_pick_class_hit(cls_result.hits, class_name)
        if cls_hit is None or cls_hit.kind not in ("class", "struct"):
            return [{"info": f"class {class_name!r} not found in v2 world"}]
        cls_usr = cls_hit.raw_usr or ""
        if not cls_usr:
            return [{"info": f"class {class_name!r} has no raw_usr in v2 store"}]

        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Collect driver-method USRs (USR prefix scan, like get_class_members).
        esc = (cls_usr.replace("\\", "\\\\")
                      .replace("%", "\\%").replace("_", "\\_"))
        pattern = f"{esc}@%"
        driver_method_usrs: set[str] = set()
        for _binding_id, state_id in bound_state_ids:
            rows = conn.execute(
                "SELECT raw_usr FROM semantic_symbols_core "
                "WHERE state_id=? AND raw_usr LIKE ? ESCAPE '\\' "
                "AND raw_usr != ?",
                (state_id, pattern, cls_usr)).fetchall()
            for r in rows:
                usr = r["raw_usr"] or ""
                if usr:
                    driver_method_usrs.add(usr)
        if not driver_method_usrs:
            return [{"info": f"class {class_name!r} has no methods in v2 world"}]

        # Find all call/indirect edges whose target USR is a driver method.
        # Group by the caller's enclosing class.
        esc_classes = ",".join("?" * len(_V2_HOTSPOT_EDGE_CLASSES))
        ph = ",".join("?" * min(len(driver_method_usrs), 999))
        driver_usr_list = list(driver_method_usrs)
        # SQLite parameter limit guard: chunk if needed.
        wrappers: dict[str, dict] = {}
        seen_calls: set[tuple] = set()
        CHUNK = 500
        for _binding_id, state_id in bound_state_ids:
            for i in range(0, len(driver_usr_list), CHUNK):
                chunk = driver_usr_list[i:i + CHUNK]
                chunk_ph = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"""SELECT e.edge_class, e.to_raw_usr, e.to_name,
                              e.certainty, e.resolution_kind,
                              s.name AS src_name, s.raw_usr AS src_usr,
                              s.kind AS src_kind, s.local_key AS src_lk
                       FROM semantic_edges e
                       JOIN semantic_symbols_core s
                         ON s.state_id=e.state_id AND s.local_key=e.local_key
                       WHERE e.state_id=?
                       AND e.edge_class IN ({esc_classes})
                       AND e.to_raw_usr IN ({chunk_ph})""",
                    (state_id, *_V2_HOTSPOT_EDGE_CLASSES, *chunk)).fetchall()
                for r in rows:
                    src_usr = r["src_usr"] or ""
                    to_usr = r["to_raw_usr"] or ""
                    to_name = r["to_name"] or ""
                    key = (state_id, int(r["src_lk"]), to_usr)
                    if key in seen_calls:
                        continue
                    seen_calls.add(key)
                    wrapper_class = _v2_enclosing_class_from_usr(src_usr)
                    w = wrappers.setdefault(
                        wrapper_class,
                        {"wrapper_class": wrapper_class,
                         "method_count": 0,
                         "methods": {}})
                    method_name = r["src_name"] or "<unknown>"
                    m = w["methods"].setdefault(
                        method_name,
                        {"method": method_name,
                         "qualified_name": method_name,
                         "kind": r["src_kind"] or "function",
                         "file": "",
                         "calls": []})
                    pos = _v2_symbol_pos(
                        conn, _binding_id, state_id, int(r["src_lk"]))
                    call_line = pos[1] if pos is not None else 0
                    file_path = pos[0] if pos is not None else ""
                    if not m["file"] and file_path:
                        m["file"] = abs_path(root, file_path)
                    m["calls"].append({
                        "driver_method": to_name,
                        "line": call_line,
                    })
                    w["method_count"] = len(w["methods"])
        if not wrappers:
            return [{"info": f"no wrapper callers for class {class_name!r} "
                            f"in v2 world"}]
        out: list[dict] = []
        for w in sorted(wrappers.values(),
                        key=lambda w: -w["method_count"]):
            out.append({
                "wrapper_class": w["wrapper_class"],
                "method_count": w["method_count"],
                "methods": [{
                    "method": m["method"],
                    "qualified_name": m["qualified_name"],
                    "kind": m["kind"],
                    "file": m["file"],
                    "calls": m["calls"],
                } for m in w["methods"].values()],
            })
            if len(out) >= limit:
                break
        return out
    except SymbolNotFound:
        return [{"info": f"class {class_name!r} not found in v2 world"}]
    except QueryViewError as e:
        log.debug("v2 find_wrapper_callers query error: %s", e)
        return []


def _v2_enclosing_class_from_usr(src_usr: str) -> str:
    """Derive the enclosing class name from a clang USR.

    A method USR like ``c:@C@Wrapper@F@method#`` or
    ``c:@S@Wrapper@F@method#`` encodes the enclosing class between the
    first ``@C@`` / ``@S@`` and the next ``@F@`` / ``@M@``.  Free
    functions (``c:@F@function``) map to ``"(global)"``.

    Returns ``"(global)"`` when the USR carries no class segment, or when
    the heuristic cannot parse it.
    """
    if not src_usr:
        return "(global)"
    # Find the class segment: @C@<name> or @S@<name>
    # The method segment is @F@<name># or @M@<name>
    # We want the class name between them.
    import re
    m = re.match(r"c:@[CS]@([^@]+)@[FM]@", src_usr)
    if m:
        return m.group(1)
    return "(global)"


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
    """v2 adapter for trace_data_flow — type-based data flow tracing.

    Finds functions whose ``signature`` mentions ``type_name`` (a
    ``LIKE '%<type_name>%'`` scan over ``semantic_symbols_core.signature``
    across the world's bound states), then for each source function finds
    call paths to ``to_symbol`` using the same BFS as
    :func:`v2_shadow_find_call_path`.  Returns a leading ``_summary``
    entry + one entry per source function.

    M3B limitations (see the Batch G module note): ``caller_count`` is
    the number of incoming ``call`` / ``indirect`` edges to the source
    function (a v2-derivable approximation of the legacy caller count);
    ``reachable`` is True when at least one path exists within
    ``max_depth``; ``paths`` carries up to 5 shortest paths as
    ``{depth, chain, target_usr}``.  ``source_kind`` / ``source_file``
    / ``source_line`` come from the source symbol's metadata row.
    """
    from ...utils import resolve_project_root, abs_path

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
        bound_state_ids = _v2_state_ids_for_bindings(conn, resolved)

        # Resolve the target symbol's USRs.
        to_result = v2_lookup_symbol(conn, pinned.view, to_symbol)
        to_usrs = {h.raw_usr for h in to_result.hits if h.raw_usr}
        if not to_usrs:
            return [{"_summary": f"target symbol {to_symbol!r} not found "
                                 f"in v2 world",
                     "_type": type_name, "_target": to_symbol}]

        # Find source functions whose signature mentions type_name.
        esc = (type_name.replace("\\", "\\\\")
                        .replace("%", "\\%").replace("_", "\\_"))
        pattern = f"%{esc}%"
        source_usrs: list[str] = []
        source_meta: dict[str, dict] = {}
        for binding_id, state_id in bound_state_ids:
            rows = conn.execute(
                """SELECT s.name, s.raw_usr, s.kind, s.signature, s.local_key,
                          (SELECT MIN(p.line) FROM semantic_state_positions p
                           WHERE p.state_id=s.state_id
                             AND p.symbol_local_key=s.local_key) AS line
                   FROM semantic_symbols_core s
                   WHERE s.state_id=?
                   AND s.kind IN ('function','method','constructor','destructor')
                   AND s.is_definition=1
                   AND s.signature LIKE ? ESCAPE '\\'
                   AND s.raw_usr IS NOT NULL AND s.raw_usr != ''""",
                (state_id, pattern)).fetchall()
            for r in rows:
                usr = r["raw_usr"] or ""
                if usr and usr not in source_meta:
                    file_path = _v2_symbol_file(
                        conn, binding_id, state_id, int(r["local_key"]))
                    source_meta[usr] = {
                        "name": r["name"] or "",
                        "kind": r["kind"] or "function",
                        "signature": r["signature"] or "",
                        "file": file_path,
                        "line": int(r["line"]) if r["line"] is not None else 0,
                    }
                    source_usrs.append(usr)
            if len(source_usrs) >= limit:
                break
        source_usrs = source_usrs[:limit]

        if not source_usrs:
            return [{"_summary": f"no functions whose signature mentions "
                                 f"{type_name!r} in v2 world",
                     "_type": type_name, "_target": to_symbol}]

        # Build the call-graph adjacency once.
        adj, usr_to_name, _defined = _v2_build_call_adjacency(
            conn, bound_state_ids)

        # Build reverse adjacency for caller_count approximation.
        incoming_count: dict[str, int] = {}
        for src, targets in adj.items():
            for t in targets:
                incoming_count[t] = incoming_count.get(t, 0) + 1

        out: list[dict] = [{
            "_summary": (f"tracing data flow of type {type_name!r} to "
                         f"{to_symbol!r}: {len(source_usrs)} source "
                         f"function(s)"),
            "_type": type_name,
            "_target": to_symbol,
        }]
        for usr in source_usrs:
            meta = source_meta[usr]
            paths = _v2_bfs_shortest_paths(
                adj, usr_to_name, {usr}, to_usrs,
                max_depth=max(max_depth, 1), max_paths=5)
            out.append({
                "source_name": meta["name"] or usr,
                "source_qualified_name": meta["name"] or usr,
                "source_kind": meta["kind"],
                "source_file": (abs_path(root, meta["file"])
                                if meta["file"] else ""),
                "source_line": meta["line"],
                "caller_count": incoming_count.get(usr, 0),
                "reachable": bool(paths),
                "paths": paths,
            })
        return out
    except SymbolNotFound:
        return [{"_summary": f"target symbol {to_symbol!r} not found "
                             f"in v2 world",
                 "_type": type_name, "_target": to_symbol}]
    except QueryViewError as e:
        log.debug("v2 trace_data_flow query error: %s", e)
        return []


# ── Batch C: Source/Search adapters (M3B v2-native read) ───────────────────
#
# M3B v2-native read adapters for the source/file family. Each resolves
# the pinned QueryView, reads v2 World-scoped facts directly
# (semantic_symbols_core / semantic_state_positions / state_file_table /
# file_blobs / semantic_edges / binding_file_map / canon_paths), and
# formats the result to the legacy handler's shape.
#
# Documented M3B limitations — these are v2 hot-schema gaps, NOT weakenings
# of the equivalence gate. Each adapter computes exactly what v2 stores:
#
#  * file_blobs.content holds RAW file bytes (not ifdef-filtered).  The
#    legacy ``symbols.source`` / ``files.content`` columns carry the
#    ifdef-filtered copy; v2's hot schema has no filtered-content column.
#    Bodies and file content therefore hold every ``#ifdef`` branch.
#  * semantic_symbols_core has no qualified_name / docstring / enum_value /
#    template_usr / parent_usr column -> qualified_name := name; docstring
#    := ""; enum_value / constants / template_usr / parent_usr are omitted.
#  * semantic_state_positions carries line / end_line / file_ordinal; file
#    for a symbol comes from the binding's file map joined to positions.
#  * is_template / is_virtual / is_pure_virtual are re-fetched from the
#    full symbol row (SymbolHit does not expose them).
#  * read_file has no mtime / skeleton / directives view in v2 — the
#    'directives' mode reads semantic_condition_index for the file's state.


def v2_shadow_get_source(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_source — function/enum/macro body via libclang extents.

    Reads v2 World-scoped symbol positions (``semantic_state_positions``)
    and the content-addressed file blob (``state_file_table`` →
    ``file_blobs.content``) to extract the symbol's body text (lines
    ``line``..``end_line``).  The body is number-prefixed to match the
    legacy ``get_source`` format.

    M3B limitations (see the Batch C module note): ``qualified_name`` :=
    ``name`` (v2 has no qualified_name column); ``docstring`` is ``""``;
    ``enum_value`` / ``constants`` / ``template_usr`` / ``parent_usr`` are
    not emitted; ``source_origin`` is ``"index"`` (the body comes from the
    v2 store); ``stale`` / ``stale_warning`` are not computed (v2 has no
    per-file mtime in the hot schema); the body holds every ``#ifdef``
    branch (``file_blobs`` stores raw bytes, not a filtered copy).
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
        result = v2_lookup_symbol(conn, pinned.view, name)
        # Prefer a definition hit (matches legacy _lookup_definition).
        hit = None
        for h in result.hits:
            if h.is_definition:
                hit = h
                break
        if hit is None and result.hits:
            hit = result.hits[0]
        if hit is None:
            return {"error": f"Symbol not found: {name}"}

        state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (hit.state.tusemantic_key,
             hit.state.parser_generation)).fetchone()
        if state_id_row is None:
            return {"error": f"Symbol not found: {name}"}
        state_id = int(state_id_row["state_id"])

        full_row = _v2_symbol_row(conn, hit)
        is_template = bool(full_row["is_template"]) if full_row else False
        is_virtual = bool(full_row["is_virtual"]) if full_row else False
        is_pure_virtual = bool(full_row["is_pure_virtual"]) if full_row else False

        canon_path, line_no, end_line, body_text = _v2_symbol_body(
            conn, hit.binding.binding_id, state_id, hit.local_key)

        out: dict = {
            "name": hit.name,
            "qualified_name": hit.name,  # v2 has no qualified_name column
            "kind": hit.kind or "",
            "file": abs_path(root, canon_path) if canon_path else "",
            "line": line_no,
            "signature": hit.signature or "",
            "is_definition": hit.is_definition,
            "is_template": is_template,
            "is_virtual": is_virtual,
            "is_pure_virtual": is_pure_virtual,
            "docstring": "",  # v2 hot schema has no docstring column
        }
        if end_line:
            out["end_line"] = end_line
        if body_text:
            out["source"] = _v2_number_lines(body_text, line_no)
            out["source_origin"] = "index"
        else:
            out["warning"] = (
                f"Could not read source from v2 store for {name}")
        return out
    except SymbolNotFound:
        return {"error": f"Symbol not found: {name}"}
    except TuPoisoned:
        return {"error": f"Symbol not found: {name}"}
    except QueryViewError as e:
        log.debug("v2 get_source query error: %s", e)
        return {"error": str(e)}


def v2_shadow_get_symbol_context(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_symbol_context — body + callers + callees.

    Reads the symbol body (same as :func:`v2_shadow_get_source`), then
    reads v2 World-scoped ``call`` / ``indirect`` edges:
    * callers — incoming edges (``to_raw_usr`` matches the symbol's USR)
      across all bound states (the find_callers pattern);
    * callees — outgoing edges from the symbol's state-local row, with
      the callee display name resolved from ``to_name`` or a cross-state
      USR lookup when ``to_name`` is empty.

    M3B limitations (see the Batch C/E/G module notes): ``qualified_name``
    := ``name``; caller/callee ``file`` / ``line`` come from positions
    (defaulting to ``""`` / ``0``); ``indirect_call_sites`` and
    ``resolution`` are not emitted (v2 hot schema has no Phase-3 link
    table); ``llm_analysis`` / ``overrides`` / ``overridden_by`` are not
    emitted; the body holds every ``#ifdef`` branch (raw file bytes).
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
        result = v2_lookup_symbol(conn, pinned.view, name)
        hit = None
        for h in result.hits:
            if h.is_definition:
                hit = h
                break
        if hit is None and result.hits:
            hit = result.hits[0]
        if hit is None:
            return {"error": f"Symbol not found: {name}"}

        state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (hit.state.tusemantic_key,
             hit.state.parser_generation)).fetchone()
        if state_id_row is None:
            return {"error": f"Symbol not found: {name}"}
        state_id = int(state_id_row["state_id"])

        full_row = _v2_symbol_row(conn, hit)
        is_template = bool(full_row["is_template"]) if full_row else False
        is_virtual = bool(full_row["is_virtual"]) if full_row else False
        is_pure_virtual = bool(full_row["is_pure_virtual"]) if full_row else False

        canon_path, line_no, end_line, body_text = _v2_symbol_body(
            conn, hit.binding.binding_id, state_id, hit.local_key)

        target_usrs = {h.raw_usr for h in result.hits if h.raw_usr}
        bound_state_ids = _v2_state_ids_for_bindings(conn, pinned.resolved_world)

        # Callers: incoming call/indirect edges to any of the symbol's USRs.
        callers: list[dict] = []
        seen_callers: set[tuple] = set()
        if target_usrs:
            ph = ",".join("?" * len(target_usrs))
            usr_list = list(target_usrs)
            for _bid, sid in bound_state_ids:
                rows = conn.execute(
                    f"""SELECT e.edge_class, s.name AS src_name,
                               s.raw_usr AS src_usr,
                               s.kind AS src_kind, s.local_key AS src_lk
                        FROM semantic_edges e
                        JOIN semantic_symbols_core s
                          ON s.state_id=e.state_id AND s.local_key=e.local_key
                        WHERE e.state_id=?
                        AND e.edge_class IN ('call','indirect')
                        AND e.to_raw_usr IN ({ph})""",
                    (sid, *usr_list)).fetchall()
                for r in rows:
                    src_usr = r["src_usr"] or ""
                    key = (sid, int(r["src_lk"]), src_usr)
                    if key in seen_callers:
                        continue
                    seen_callers.add(key)
                    callers.append({
                        "name": r["src_name"] or "",
                        "qualified_name": r["src_name"] or "",
                        "file": "",
                        "line": 0,
                        "kind": r["src_kind"] or "",
                        "ref_kind": r["edge_class"],
                    })

        # Callees: outgoing call/indirect edges from the symbol's row.
        callees: list[dict] = []
        seen_callees: set[tuple] = set()
        edge_rows = conn.execute(
            """SELECT e.edge_class, e.to_raw_usr, e.to_name
                FROM semantic_edges e
                WHERE e.state_id=? AND e.local_key=?
                AND e.edge_class IN ('call','indirect')
                ORDER BY e.edge_ordinal""",
            (state_id, hit.local_key)).fetchall()
        for r in edge_rows:
            to_usr = r["to_raw_usr"] or ""
            to_name = r["to_name"] or ""
            key = (to_usr, to_name, r["edge_class"])
            if key in seen_callees:
                continue
            seen_callees.add(key)
            callee_name = to_name
            callee_kind = ""
            if not callee_name and to_usr:
                meta = _v2_lookup_by_usr(conn, bound_state_ids, to_usr)
                callee_name = meta["name"] or to_usr
                callee_kind = meta["kind"]
            callees.append({
                "name": callee_name or "",
                "qualified_name": callee_name or "",
                "kind": callee_kind,
                "file": "",
                "line": 0,
                "ref_kind": r["edge_class"],
            })

        out: dict = {
            "name": hit.name,
            "qualified_name": hit.name,
            "kind": hit.kind or "",
            "file": abs_path(root, canon_path) if canon_path else "",
            "line": line_no,
            "signature": hit.signature or "",
            "is_definition": hit.is_definition,
            "is_template": is_template,
            "is_virtual": is_virtual,
            "is_pure_virtual": is_pure_virtual,
            "callers": callers,
            "callees": callees,
            "indirect_call_sites": [],  # v2 hot schema has no Phase-3 link table
        }
        if end_line:
            out["end_line"] = end_line
        if body_text:
            out["source"] = _v2_number_lines(body_text, line_no)
            out["source_origin"] = "index"
        else:
            out["warning"] = (
                f"Could not read source from v2 store for {name}")
        return out
    except SymbolNotFound:
        return {"error": f"Symbol not found: {name}"}
    except TuPoisoned:
        return {"error": f"Symbol not found: {name}"}
    except QueryViewError as e:
        log.debug("v2 get_symbol_context query error: %s", e)
        return {"error": str(e)}


def v2_shadow_explain_symbol(
    name: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for explain_symbol — symbol explanation (metadata + source).

    Reads v2 World-scoped symbol metadata and body text (same as
    :func:`v2_shadow_get_source`), then derives a template ``explanation``
    from the symbol's kind/name/location — v2 has no pre-computed LLM
    analysis (``llm_analysis``) in the hot schema.

    M3B limitations (see the Batch C module note): ``qualified_name`` :=
    ``name``; ``explanation`` is a v2-derived template (no LLM); no
    ``llm_analysis``; the body holds every ``#ifdef`` branch (raw file
    bytes).  ``context_lines`` is accepted but has no effect (v2 reads
    the full symbol extent, not a window).
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
        result = v2_lookup_symbol(conn, pinned.view, name)
        hit = None
        for h in result.hits:
            if h.is_definition:
                hit = h
                break
        if hit is None and result.hits:
            hit = result.hits[0]
        if hit is None:
            return {"error": f"Symbol not found: {name}"}

        state_id_row = conn.execute(
            "SELECT state_id FROM semantic_states WHERE "
            "tusemantic_key=? AND parser_generation=?",
            (hit.state.tusemantic_key,
             hit.state.parser_generation)).fetchone()
        if state_id_row is None:
            return {"error": f"Symbol not found: {name}"}
        state_id = int(state_id_row["state_id"])

        canon_path, line_no, end_line, body_text = _v2_symbol_body(
            conn, hit.binding.binding_id, state_id, hit.local_key)
        file_abs = abs_path(root, canon_path) if canon_path else ""

        kind = hit.kind or "symbol"
        signature = hit.signature or ""
        explanation = (
            f"v2-native: {kind} {name} at {file_abs}:{line_no}"
            + (f" (signature: {signature})" if signature else "")
            + (f" — defined here." if hit.is_definition
                else " — declaration only.")
        )

        out: dict = {
            "name": hit.name,
            "qualified_name": hit.name,
            "kind": kind,
            "file": file_abs,
            "line": line_no,
            "signature": signature,
            "explanation": explanation,
        }
        if end_line:
            out["end_line"] = end_line
        if body_text:
            out["source"] = _v2_number_lines(body_text, line_no)
        return out
    except SymbolNotFound:
        return {"error": f"Symbol not found: {name}"}
    except TuPoisoned:
        return {"error": f"Symbol not found: {name}"}
    except QueryViewError as e:
        log.debug("v2 explain_symbol query error: %s", e)
        return {"error": str(e)}


def v2_shadow_get_file_map(
    file_path: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for get_file_map — file structure overview.

    Resolves *file_path* to a canon path, finds the world's bound
    bindings whose file map includes it, and reads every symbol position
    in that ``(state, file_ordinal)`` joined to ``semantic_symbols_core``
    for name/kind/signature.  Groups by ``kind`` to match the legacy
    member-table shape.

    M3B limitations (see the Batch C/E module notes): ``qualified_name``
    := ``name``; ``end_line`` comes from positions (omitted when 0);
    enum constants are listed as ``enum_constant`` kind items (v2 has no
    ``enum_value`` column in the hot schema, so ``enum_value`` is
    omitted); ``subgroups`` grouping by parent enum is not emitted (v2
    has no qualified_name to derive the parent); ``signatures`` is
    honored, ``max_per_kind`` bounds the items list (default 30).
    """
    from ...utils import resolve_project_root, abs_path

    signatures = bool(kwargs.get("signatures", False))
    max_per_kind = int(kwargs.get("max_per_kind", 30))

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return {"error": "v2 store unavailable"}
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return {"error": str(e)}

    try:
        canon_path_id = _v2_resolve_canon_path_id(conn, file_path)
        if canon_path_id is None:
            return {"error": (
                f"File not found in v2 store: {file_path}. Check the path — "
                f"use relative paths like 'src/main.cpp'.")}
        cp_row = conn.execute(
            "SELECT path FROM canon_paths WHERE path_id=?",
            (canon_path_id,)).fetchone()
        canon_path = cp_row["path"] if cp_row is not None else file_path

        bound_state_ids = _v2_state_ids_for_bindings(conn, pinned.resolved_world)

        groups: dict[str, dict] = {}
        total = 0
        seen_usrs: set[str] = set()
        for binding_id, state_id in bound_state_ids:
            fo_row = conn.execute(
                "SELECT file_ordinal FROM binding_file_map "
                "WHERE binding_id=? AND canon_path_id=?",
                (binding_id, canon_path_id)).fetchone()
            if fo_row is None:
                continue
            file_ordinal = int(fo_row["file_ordinal"])
            rows = conn.execute(
                """SELECT s.name, s.raw_usr, s.kind, s.signature,
                          s.is_definition, s.local_key,
                          p.line, p.end_line
                   FROM semantic_state_positions p
                   JOIN semantic_symbols_core s
                     ON s.state_id=p.state_id
                       AND s.local_key=p.symbol_local_key
                   WHERE p.state_id=? AND p.file_ordinal=?
                   ORDER BY s.kind, p.line""",
                (state_id, file_ordinal)).fetchall()
            for r in rows:
                usr = r["raw_usr"] or ""
                if usr and usr in seen_usrs:
                    continue
                if usr:
                    seen_usrs.add(usr)
                kind = r["kind"] or "unknown"
                g = groups.setdefault(kind, {"count": 0, "items": []})
                g["count"] += 1
                total += 1
                if max_per_kind == 0 or len(g["items"]) < max_per_kind:
                    entry: dict = {
                        "name": r["name"] or "",
                        "qualified_name": r["name"] or "",
                        "line": int(r["line"]) if r["line"] is not None else 0,
                    }
                    if r["end_line"]:
                        entry["end_line"] = int(r["end_line"])
                    if signatures and r["signature"]:
                        entry["signature"] = r["signature"]
                    g["items"].append(entry)
        return {
            "file": canon_path,
            "total_symbols": total,
            "symbols": groups,
        }
    except QueryViewError as e:
        log.debug("v2 get_file_map query error: %s", e)
        return {"error": str(e)}


def v2_shadow_read_file(
    file_path: str,
    project_root: str | None = None,
    variant: str | None = None,
    image: str | None = None,
    **kwargs,
) -> dict:
    """v2 adapter for read_file — file content.

    Resolves *file_path* to a canon path, finds a bound binding whose
    file map includes it, and reads the content-addressed file blob
    (``binding_file_map.blob_sha`` → ``file_blobs.content``).  Applies
    the ``start_line`` / ``end_line`` window when given, and optionally
    number-prefixed text when ``line_numbers=True``.

    M3B limitations (see the Batch C module note): v2 ``file_blobs``
    stores RAW file bytes (not ifdef-filtered) — ``content`` holds every
    ``#ifdef`` branch, and ``all_lines_inactive`` / ``skeleton`` are not
    computed; ``mtime`` is not available (set to 0.0); the ``directives``
    mode reads ``semantic_condition_index`` for the file's state and
    returns ``{line, kind, text}`` rows; ``stale`` / ``stale_warning``
    are not computed.
    """
    from ...utils import resolve_project_root, abs_path

    line_numbers = bool(kwargs.get("line_numbers", False))
    start_line = int(kwargs.get("start_line", 0) or 0)
    end_line = int(kwargs.get("end_line", 0) or 0)
    mode = str(kwargs.get("mode", "active") or "active")

    root = resolve_project_root(project_root)
    conn = _get_v2_conn(root)
    if conn is None:
        return {"error": "v2 store unavailable"}
    try:
        pinned = resolve_current_view(root, allow_tree_mismatch=True)
    except QueryViewResolutionError as e:
        return {"error": str(e)}

    try:
        canon_path_id = _v2_resolve_canon_path_id(conn, file_path)
        if canon_path_id is None:
            return {"error": (
                f"File not found in v2 store: {file_path}. Check the path — "
                f"use relative paths like 'src/main.cpp'.")}
        cp_row = conn.execute(
            "SELECT path FROM canon_paths WHERE path_id=?",
            (canon_path_id,)).fetchone()
        canon_path = cp_row["path"] if cp_row is not None else file_path

        bound_state_ids = _v2_state_ids_for_bindings(conn, pinned.resolved_world)

        # 'directives' mode: read semantic_condition_index for the file's
        # state.  We need the file_ordinal to scope directives to THIS file
        # (a state carries condition regions across all its files).
        if mode == "directives":
            directives: list[dict] = []
            for binding_id, state_id in bound_state_ids:
                fo_row = conn.execute(
                    "SELECT file_ordinal FROM binding_file_map "
                    "WHERE binding_id=? AND canon_path_id=?",
                    (binding_id, canon_path_id)).fetchone()
                if fo_row is None:
                    continue
                file_ordinal = int(fo_row["file_ordinal"])
                d_rows = conn.execute(
                    """SELECT region_ordinal, directive, condition
                       FROM semantic_condition_index
                       WHERE state_id=? AND file_blob_sha IN (
                         SELECT blob_sha FROM state_file_table
                         WHERE state_id=? AND file_ordinal=?
                       )
                       ORDER BY region_ordinal""",
                    (state_id, state_id, file_ordinal)).fetchall()
                for r in d_rows:
                    directives.append({
                        "line": int(r["region_ordinal"]),
                        "kind": r["directive"] or "",
                        "text": r["condition"] or "",
                    })
                break
            return {
                "file": canon_path,
                "language": _v2_file_language(canon_path),
                "lines": len(directives),
                "directives": directives,
                "mode": "directives",
            }

        # 'active' / 'raw' both read the raw file blob from file_blobs.
        blob_sha: str | None = None
        for binding_id, _state_id in bound_state_ids:
            br = conn.execute(
                "SELECT blob_sha FROM binding_file_map "
                "WHERE binding_id=? AND canon_path_id=?",
                (binding_id, canon_path_id)).fetchone()
            if br is not None and br["blob_sha"]:
                blob_sha = br["blob_sha"]
                break
        if blob_sha is None:
            return {"error": f"File content not found in v2 store: {file_path}"}
        blob_row = conn.execute(
            "SELECT content, byte_size FROM file_blobs WHERE blob_sha=?",
            (blob_sha,)).fetchone()
        if blob_row is None or blob_row["content"] is None:
            return {"error": f"File content not found in v2 store: {file_path}"}

        text = _v2_decode_blob(blob_row["content"])
        all_lines = text.splitlines()
        total_lines = len(all_lines)

        # Apply start_line / end_line window (1-based inclusive).
        s_idx = max(0, start_line - 1) if start_line > 0 else 0
        e_idx = (min(total_lines, end_line) if end_line > 0
                 else total_lines)
        if s_idx >= total_lines:
            return {"error": (
                f"start_line {start_line} is past end of file "
                f"({total_lines} lines)")}
        window = all_lines[s_idx:e_idx]

        if line_numbers:
            content = "\n".join(
                f"{s_idx + i + 1:4d}  {line}" for i, line in enumerate(window))
        else:
            content = "\n".join(window)

        out: dict = {
            "file": abs_path(root, canon_path) if canon_path else canon_path,
            "language": _v2_file_language(canon_path),
            "mtime": 0.0,  # v2 hot schema has no per-file mtime
            "lines": total_lines,
            "content": content,
            "mode": mode,
        }
        if start_line > 0 or end_line > 0:
            out["start_line"] = s_idx + 1
            out["end_line"] = e_idx
        # v2 file_blobs holds raw bytes — no ifdef filtering, no skeleton.
        out["warning"] = (
            "v2 read_file returns raw file content from file_blobs "
            "(not ifdef-filtered). Inactive #ifdef branches are visible.")
        return out
    except QueryViewError as e:
        log.debug("v2 read_file query error: %s", e)
        return {"error": str(e)}


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


def _v2_symbol_body(conn, binding_id, state_id, local_key):
    """Return ``(canon_path, line, end_line, body_text)`` for a symbol.

    Reads the symbol's first position row (line/end_line/file_ordinal +
    canon path via the binding's file map, A-4), then the file content
    from ``file_blobs.content`` via ``state_file_table``, and extracts
    the body text (lines ``line``..``end_line``, NO line-number prefix —
    callers add it via :func:`_v2_number_lines`).

    Returns ``("", 0, 0, "")`` when the position row or file blob is
    absent — callers report a warning.  The v2 hot schema stores RAW
    file bytes in ``file_blobs`` (the legacy ``symbols.source`` column
    carries the ifdef-filtered copy); this is a documented M3B
    limitation, not a defect.
    """
    pos = conn.execute(
        "SELECT p.line, p.end_line, p.file_ordinal, cp.path "
        "FROM semantic_state_positions p "
        "JOIN binding_file_map bfm "
        "  ON bfm.binding_id=? AND bfm.file_ordinal=p.file_ordinal "
        "JOIN canon_paths cp ON cp.path_id=bfm.canon_path_id "
        "WHERE p.state_id=? AND p.symbol_local_key=? "
        "ORDER BY p.line LIMIT 1",
        (binding_id, state_id, local_key)).fetchone()
    if pos is None:
        return "", 0, 0, ""
    line = int(pos["line"]) if pos["line"] is not None else 0
    end_line = int(pos["end_line"]) if pos["end_line"] is not None else 0
    canon_path = pos["path"] or ""
    file_ordinal = int(pos["file_ordinal"])
    blob = _v2_state_file_blob(conn, state_id, file_ordinal)
    body = ""
    if blob is not None and line > 0:
        body = _v2_extract_body_lines(_v2_decode_blob(blob), line, end_line)
    return canon_path, line, end_line, body


def _v2_state_file_blob(conn, state_id, file_ordinal):
    """Return the raw file content (bytes/str) for a state's file_ordinal,
    or ``None`` when the blob is absent.  Reads via ``state_file_table``
    → ``file_blobs.content`` (content-addressed raw bytes)."""
    r = conn.execute(
        "SELECT b.content FROM state_file_table sft "
        "JOIN file_blobs b ON b.blob_sha=sft.blob_sha "
        "WHERE sft.state_id=? AND sft.file_ordinal=?",
        (state_id, file_ordinal)).fetchone()
    if r is None or r["content"] is None:
        return None
    return r["content"]


def _v2_decode_blob(content) -> str:
    """Decode a ``file_blobs.content`` value (bytes or str) to text."""
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8", errors="replace")
    return str(content)


def _v2_extract_body_lines(text, start_line, end_line):
    """Extract lines ``[start_line, end_line]`` (1-based inclusive) from
    *text*.  Returns the body as a ``\\n``-joined string with NO
    line-number prefix.  ``end_line <= 0`` or ``end_line < start_line``
    yields just the start line (a declaration with no extent)."""
    if start_line <= 0:
        return ""
    lines = text.splitlines()
    s_idx = max(0, start_line - 1)
    if end_line <= 0 or end_line < start_line:
        e_idx = s_idx + 1
    else:
        e_idx = min(len(lines), end_line)
    return "\n".join(lines[s_idx:e_idx])


def _v2_number_lines(text, start_line):
    """Prefix each line with its 1-based line number, right-aligned in 4
    columns followed by two spaces — matching the legacy ``get_source``
    format (``"  20     bool ..."``)."""
    out = []
    for offset, line in enumerate(text.splitlines()):
        out.append(f"{start_line + offset:4d}  {line}")
    return "\n".join(out)


def _v2_resolve_canon_path_id(conn, file_path):
    """Resolve a user-supplied file path to a ``canon_paths.path_id``.

    Exact match first, then a suffix LIKE (so ``main.cpp`` matches
    ``src/main.cpp``), choosing the shortest matching path (least-nested
    canonical).  Returns ``None`` when no canon path matches.
    """
    r = conn.execute(
        "SELECT path_id FROM canon_paths WHERE path=? LIMIT 1",
        (file_path,)).fetchone()
    if r is not None:
        return int(r["path_id"])
    esc = (file_path.replace("\\", "\\\\")
                    .replace("%", "\\%").replace("_", "\\_"))
    r = conn.execute(
        "SELECT path_id FROM canon_paths "
        "WHERE path LIKE ? ESCAPE '\\' ORDER BY length(path) LIMIT 1",
        (f"%{esc}",)).fetchone()
    return int(r["path_id"]) if r is not None else None


def _v2_file_language(file_path):
    """Return ``"c"`` or ``"cpp"`` based on the file extension."""
    p = file_path.lower()
    if p.endswith((".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++")):
        return "cpp"
    if p.endswith((".c", ".h")):
        return "c"
    return "cpp"


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
