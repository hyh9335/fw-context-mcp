"""M6 query primitives — M7.2 production read authority.

Implements the semantic query API on top of the M6 shared-fact store.
Each query function reads from the M6 store (via project_build_kind) and
returns results in the same shape as the production MCP API expects.

This module IS the M6 query implementation (§8: "production request →
M6 query implementation → M6 result"). It does NOT read from the legacy
DB or v2 store — the M6 store is the sole read authority.

The M6 store is populated by direct per-TU materialization
(`m6_direct_materialization.materialize_unit_into_m6`) during the normal
indexing pipeline. The legacy-DB conversion path
(`m6_production_indexing.index_legacy_db_into_m6`) is an explicit
compat/benchmark mode, NOT the normal production path.
"""
from __future__ import annotations
import sqlite3
import logging
from pathlib import Path
from typing import Any

from fw_context_mcp.store_v2.m6_store import BuildSetStore, create_m6_shadow_db

log = logging.getLogger(__name__)

# ── M6 store connection cache (per-process) ────────────────────────────────

_m6_stores: dict[str, BuildSetStore] = {}


def _m6_db_path(project_root: Path) -> Path:
    """Resolve the M6 store DB path for a project."""
    fw_ctx = Path(project_root) / ".fw-context"
    return fw_ctx / "index-m6.db"


def open_m6_store(project_root: Path) -> BuildSetStore:
    """Open (or create) the M6 store for a project."""
    db_path = _m6_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = create_m6_shadow_db(db_path)
    return BuildSetStore(conn)


def get_m6_store(project_root: str | Path) -> BuildSetStore | None:
    """Get or open the M6 store for a project.

    Returns None if no M6 store exists (not yet indexed).
    """
    root = str(Path(project_root).resolve())
    if root in _m6_stores:
        return _m6_stores[root]

    db_path = _m6_db_path(Path(root))
    if not db_path.exists():
        return None

    try:
        store = open_m6_store(Path(root))
        _m6_stores[root] = store
        return store
    except Exception as e:
        log.debug("M6 store open failed for %s: %s", root, e)
        return None


def resolve_build_name(store: BuildSetStore, project_root: str | Path,
                       variant: str | None = None,
                       image: str | None = None) -> str:
    """Resolve the active build name for a project.

    The M6 store uses build names (from the BuildSet registry). Build
    names are derived from variant/image during indexing:
    - If both variant and image are given: "{variant}/{image}"
    - If only one is given: that value
    - If neither is given: the first registered build (or "default")

    This supports multi-build coexistence: each build gets a distinct
    build_name, and queries select the build via variant/image kwargs.
    """
    # If variant/image specified, construct the build name directly
    if variant and image:
        return f"{variant}/{image}"
    if variant:
        return variant
    if image:
        return image

    # No explicit selector — try the first registered build
    try:
        builds = store._build_to_buildsets
        if builds:
            return next(iter(builds.keys()))
    except Exception:
        pass
    return "default"


def list_builds(store: BuildSetStore) -> list[str]:
    """List all builds registered in the M6 store.

    Returns build names in insertion order. Useful for multi-build
    queries and debugging.
    """
    try:
        return list(store._build_to_buildsets.keys())
    except Exception:
        return []


# ── Semantic query primitives ───────────────────────────────────────────────

def lookup_symbol(store: BuildSetStore, build_name: str,
                  name: str, exact: bool = False, limit: int = 50,
                  offset: int = 0) -> dict:
    """M6 implementation of lookup_symbol.

    Projects Symbol+Function+Variable+Type facts and filters by name.
    Returns ``{rows, total, offset, shown}`` for the adapter to page.
    """
    results = []
    name_lower = name.lower() if not exact else None
    name_exact = name if exact else None

    for kind in ("Symbol", "Function", "Variable", "Type"):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            sym_name = payload.get("name", "")
            if exact:
                if sym_name != name_exact:
                    continue
            else:
                if name_lower and name_lower not in sym_name.lower():
                    continue
            results.append(_format_symbol_result(payload))

    return _paginate(results, offset, limit)


def _paginate(results: list, offset: int, limit: int) -> dict:
    """Split a full matched list into ``{rows, total, offset, shown}``.

    *offset* counts rows already returned by earlier pages; *limit* is the
    page size the caller asked for.  The full match list is sliced — the
    caller materializes candidates once and this decides what the page
    shows, keeping the count stable across pages.
    """
    total = len(results)
    page = results[offset: offset + limit]
    return {"rows": page, "total": total, "offset": offset, "shown": len(page)}


def search_code(store: BuildSetStore, build_name: str,
                query: str, limit: int = 20,
                offset: int = 0) -> dict:
    """M6 implementation of search_code — symbol name search.

    Returns a dict ``{rows, total, offset, shown}`` so the adapter can
    build a paged answer (page_notice header + row slice).  ``offset``
    pages over the full matched set, which the caller materializes once.
    """
    # Split query into terms (like FTS5 would)
    terms = query.lower().split()
    results = []

    for kind in ("Symbol", "Function", "Variable", "Type"):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            sym_name = payload.get("name", "").lower()
            # OR-join: match if ANY term is in the name
            if any(term in sym_name for term in terms):
                results.append(_format_symbol_result(payload))

    return _paginate(results, offset, limit)


def find_callers(store: BuildSetStore, build_name: str,
                 target: str, limit: int = 50) -> list[dict]:
    """M6 implementation of find_callers — find calls to target."""
    results = []
    target_lower = target.lower()
    count = 0

    facts = store.project_build_kind(build_name, "Call")
    for f in facts:
        payload = f.get("payload", {})
        callee_name = payload.get("callee_name", "").lower()
        if target_lower in callee_name:
            results.append({
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "ref_kind": "call",
                "caller": "",  # caller name derived from caller_usr
                "caller_kind": "function",
                "certainty": "exact",
            })
            count += 1
            if count >= limit:
                break

    return results


def find_references(store: BuildSetStore, build_name: str,
                    name: str, limit: int = 50) -> list[dict]:
    """M6 implementation of find_references."""
    results = []
    name_lower = name.lower()
    count = 0

    facts = store.project_build_kind(build_name, "Reference")
    for f in facts:
        payload = f.get("payload", {})
        target_name = payload.get("target_name", "").lower()
        src_name = payload.get("src_name", "").lower()
        if name_lower in target_name or name_lower in src_name:
            results.append({
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "ref_kind": payload.get("ref_kind", "ref"),
                "caller": payload.get("src_name", ""),
                "caller_kind": "function",
                "certainty": "exact",
            })
            count += 1
            if count >= limit:
                break

    return results


def get_symbol_context(store: BuildSetStore, build_name: str,
                       name: str) -> dict:
    """M6 implementation of get_symbol_context — symbol + callers + callees."""
    symbol = lookup_symbol(store, build_name, name, exact=False, limit=1)
    callers = find_callers(store, build_name, name)
    refs = find_references(store, build_name, name)
    body = get_source(store, build_name, name)
    # get_source returns {"error": ...} when absent; keep source key always.
    source = body.get("source", "") if "error" not in body else ""
    callees = _m6_callees(store, build_name, name)

    return {
        "name": name,
        "symbol": symbol[0] if symbol else None,
        "callers": callers,
        "callees": callees,
        "references": refs,
        "source": source,
    }


def _m6_callees(store: BuildSetStore, build_name: str, name: str) -> list[dict]:
    """Outgoing call edges for *name* from M6 Call facts (caller_usr == usr)."""
    usr = ""
    for sym in lookup_symbol(store, build_name, name, exact=False, limit=5):
        if sym.get("name", "").lower() == name.lower():
            usr = sym.get("usr", "") or ""
            break
    if not usr:
        return []
    out = []
    for f in store.project_build_kind(build_name, "Call"):
        p = f.get("payload", {}) or {}
        if p.get("caller_usr", "") == usr and p.get("callee_name", ""):
            out.append({
                "name": p.get("callee_name", ""),
                "usr": p.get("callee_usr", ""),
                "file": p.get("file", ""),
                "line": p.get("line", 0),
            })
    return out


def get_class_members(store: BuildSetStore, build_name: str,
                      class_name: str) -> dict:
    """M6 implementation of get_class_members."""
    members = {"methods": [], "fields": [], "constructors": [], "destructors": [],
               "enums": [], "typedefs": [], "classes": [], "structs": [],
               "member_count": 0}

    # Find fields for this type
    cn_lower = class_name.lower()
    facts = store.project_build_kind(build_name, "Variable")
    for f in facts:
        payload = f.get("payload", {})
        if payload.get("kind") == "field":
            # Check if the field belongs to this class
            parent_usr = payload.get("parent_usr", "")
            qualified_name = payload.get("qualified_name", "")
            if cn_lower in qualified_name.lower():
                members["fields"].append({
                    "name": payload.get("name", ""),
                    "qualified_name": payload.get("qualified_name", ""),
                    "line": payload.get("line", 0),
                    "file": payload.get("file", ""),
                })

    members["member_count"] = sum(len(v) for v in members.values() if isinstance(v, list))
    return members


def search_bodies(store: BuildSetStore, build_name: str,
                  query: str, limit: int = 20) -> list[dict]:
    """M6 implementation of search_bodies — search body text."""
    results = []
    query_lower = query.lower()
    count = 0

    facts = store.project_build_kind(build_name, "Body")
    for f in facts:
        payload = f.get("payload", {})
        body_text = payload.get("body_text", "").lower()
        if query_lower in body_text:
            results.append({
                "name": payload.get("func_name", ""),
                "file": payload.get("file", ""),
                "line": payload.get("start_line", 0),
                "source": payload.get("body_text", "")[:2000],
            })
            count += 1
            if count >= limit:
                break

    return results


def get_file_map(store: BuildSetStore, build_name: str,
                 file_path: str) -> dict:
    """M6 implementation of get_file_map — symbols in a file."""
    symbols_by_kind: dict[str, list] = {}
    total = 0

    for kind in ("Symbol", "Function", "Variable", "Type"):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            if payload.get("file", "") == file_path:
                sym_kind = payload.get("kind", "unknown")
                if sym_kind not in symbols_by_kind:
                    symbols_by_kind[sym_kind] = {"count": 0, "items": []}
                symbols_by_kind[sym_kind]["count"] += 1
                symbols_by_kind[sym_kind]["items"].append({
                    "name": payload.get("name", ""),
                    "qualified_name": payload.get("qualified_name", ""),
                    "line": payload.get("line", 0),
                    "end_line": payload.get("end_line", 0),
                })
                total += 1

    return {"file": file_path, "total_symbols": total, "symbols": symbols_by_kind}


def get_source(store: BuildSetStore, build_name: str,
               name: str) -> dict:
    """M6 implementation of get_source — function body.

    The body text lives in ``m6_body_facts`` (I4), keyed by ``symbol_usr``.
    Resolve the name to a usr first (via lookup_symbol), then return the
    stored body for that usr.
    """
    hits = lookup_symbol(store, build_name, name, exact=False, limit=10)
    usr = ""
    resolved = None
    name_lower = name.lower()
    for h in hits:
        if name_lower in (h.get("name", "") or "").lower():
            resolved = h
            usr = h.get("usr", "") or ""
            break
    if usr:
        for bf in store.body_facts_for_build(build_name):
            if bf.get("symbol_usr") == usr:
                return {
                    "name": name,
                    "source": bf.get("body_text", ""),
                    "file": (resolved or {}).get("file", ""),
                    "line": bf.get("line_start", 0),
                    "end_line": bf.get("line_end", 0),
                }
    return {"name": name, "source": "", "error": "symbol not found"}


def _format_symbol_result(payload: dict) -> dict:
    """Format an M6 fact payload as a production symbol result."""
    return {
        "name": payload.get("name", ""),
        "qualified_name": payload.get("qualified_name", payload.get("name", "")),
        "kind": payload.get("kind", ""),
        "file": payload.get("file", ""),
        "line": payload.get("line", 0),
        "end_line": payload.get("end_line", 0),
        "signature": payload.get("signature", ""),
        "docstring": payload.get("docstring", ""),
        "is_definition": payload.get("is_definition", False),
        "is_virtual": payload.get("is_virtual", False),
        "is_pure_virtual": payload.get("is_pure_virtual", False),
        "is_template": payload.get("is_template", False),
        "parent_usr": payload.get("parent_usr", ""),
        "template_usr": payload.get("template_usr", ""),
        "usr": payload.get("usr", ""),
    }


# ── M7-FINAL: real M6 query primitives for previously-stubbed adapters ────


def get_method_overrides(store: BuildSetStore, build_name: str,
                         method_name: str) -> dict:
    """M6 implementation of get_method_overrides.

    Scans Function facts for virtual methods matching method_name, then
    uses Inheritance facts to find base/derived classes and their methods
    with the same name (override relationship).
    """
    result = {
        "name": method_name, "qualified_name": method_name,
        "overrides": [], "overridden_by": [],
    }
    name_lower = method_name.lower()

    # Find all functions with this name that are virtual
    target_usrs = set()
    all_funcs = []
    for kind in ("Function",):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            if payload.get("name", "").lower() == name_lower:
                if payload.get("is_virtual"):
                    usr = payload.get("usr", "")
                    target_usrs.add(usr)
                    all_funcs.append(payload)

    if not target_usrs:
        return result

    # Build a USR → (class_usr, name) map for virtual methods
    usr_to_class = {}
    usr_to_name = {}
    for func in all_funcs:
        usr = func.get("usr", "")
        parent_usr = func.get("parent_usr", "")
        usr_to_class[usr] = parent_usr
        usr_to_name[usr] = func.get("name", "")

    # Use Inheritance facts to find base/derived class relationships
    inh_facts = store.project_build_kind(build_name, "Inheritance")
    class_bases: dict[str, list[str]] = {}  # derived_usr → [base_usrs]
    class_derived: dict[str, list[str]] = {}  # base_usr → [derived_usrs]
    for f in inh_facts:
        payload = f.get("payload", {})
        derived = payload.get("derived_usr", "")
        base = payload.get("base_usr", "")
        if derived and base:
            class_bases.setdefault(derived, []).append(base)
            class_derived.setdefault(base, []).append(derived)

    # For each target method, find overrides
    for usr, class_usr in usr_to_class.items():
        if not class_usr:
            continue
        # Find base classes of this method's class
        bases = class_bases.get(class_usr, [])
        for base_usr in bases:
            # Find methods with the same name in the base class
            for other_usr, other_class in usr_to_class.items():
                if other_usr == usr:
                    continue
                if other_class == base_usr and usr_to_name.get(other_usr) == method_name:
                    result["overrides"].append({
                        "usr": other_usr, "name": method_name,
                        "qualified_name": method_name,
                    })
        # Find derived classes
        deriveds = class_derived.get(class_usr, [])
        for derived_usr in deriveds:
            for other_usr, other_class in usr_to_class.items():
                if other_usr == usr:
                    continue
                if other_class == derived_usr and usr_to_name.get(other_usr) == method_name:
                    result["overridden_by"].append({
                        "usr": other_usr, "name": method_name,
                        "qualified_name": method_name,
                    })

    return result


def get_template_instances(store: BuildSetStore, build_name: str,
                          template_name: str, limit: int = 50) -> list[dict]:
    """M6 implementation of get_template_instances.

    Finds symbols where template_usr points to a template with the given name.
    """
    results = []
    name_lower = template_name.lower()

    # First, find the template's USR
    template_usrs = set()
    for kind in ("Function", "Type", "Symbol"):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            if (payload.get("name", "").lower() == name_lower
                    and payload.get("is_template")):
                template_usrs.add(payload.get("usr", ""))

    # Also match by template_usr field directly
    for kind in ("Function", "Type", "Symbol"):
        if len(results) >= limit:
            break
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            if len(results) >= limit:
                break
            payload = f.get("payload", {})
            t_usr = payload.get("template_usr", "")
            if t_usr and (t_usr in template_usrs
                          or template_name.lower() in t_usr.lower()):
                results.append({
                    "usr": payload.get("usr", ""),
                    "name": payload.get("name", ""),
                    "qualified_name": payload.get("qualified_name", ""),
                    "kind": payload.get("kind", ""),
                    "file": payload.get("file", ""),
                    "line": payload.get("line", 0),
                    "signature": payload.get("signature", ""),
                    "template_usr": t_usr,
                    "is_definition": payload.get("is_definition", False),
                })
    return results


def trace_data_flow(store: BuildSetStore, build_name: str,
                    type_name: str, to_symbol: str,
                    max_depth: int = 8, limit: int = 15) -> list[dict]:
    """M6 implementation of trace_data_flow.

    Finds functions whose signature mentions type_name, then traces call
    paths to to_symbol using BFS over the Call facts.
    """
    results = []
    type_lower = type_name.lower()
    to_lower = to_symbol.lower()

    # Step 1: find source functions (signature mentions type_name)
    source_funcs = []
    for kind in ("Function",):
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            sig = payload.get("signature", "").lower()
            if type_lower in sig:
                source_funcs.append({
                    "name": payload.get("name", ""),
                    "usr": payload.get("usr", ""),
                    "file": payload.get("file", ""),
                    "line": payload.get("line", 0),
                })
            if len(source_funcs) >= limit:
                break

    # Step 2: build call graph from Call facts (caller_usr → callee_name)
    call_graph: dict[str, set[str]] = {}  # caller_usr → {callee_name}
    callee_to_usrs: dict[str, set[str]] = {}  # callee_name → {callee_usr}
    call_facts = store.project_build_kind(build_name, "Call")
    for f in call_facts:
        payload = f.get("payload", {})
        caller = payload.get("caller_usr", "")
        callee_name = payload.get("callee_name", "")
        callee_usr = payload.get("callee_usr", "")
        if caller and callee_name:
            call_graph.setdefault(caller, set()).add(callee_name)
        if callee_usr and callee_name:
            callee_to_usrs.setdefault(callee_name, set()).add(callee_usr)

    # Step 3: BFS from each source to find paths to to_symbol
    for src in source_funcs[:limit]:
        src_usr = src["usr"]
        # BFS: does src_usr (or its callees) eventually reach to_symbol?
        # Each queue entry carries its own path (immutable list), so
        # different branches don't corrupt each other's chain.
        visited = {src_usr}
        queue = [(src_usr, 0, [src["name"]])]
        found_path = False
        found_chain = None
        while queue:
            curr, depth, path = queue.pop(0)
            if depth >= max_depth:
                continue
            callees = call_graph.get(curr, set())
            for callee in callees:
                new_path = path + [callee]
                if callee.lower() == to_lower:
                    found_path = True
                    found_chain = new_path
                    break
                # Find callee USRs to continue BFS
                for callee_usr in callee_to_usrs.get(callee, set()):
                    if callee_usr not in visited:
                        visited.add(callee_usr)
                        queue.append((callee_usr, depth + 1, new_path))
            if found_path:
                break

        results.append({
            "source_name": src["name"],
            "source_file": src["file"],
            "source_line": src["line"],
            "reachable": found_path,
            "paths": ([{"depth": len(found_chain) - 1,
                        "chain": " → ".join(found_chain)}]
                      if found_path else []),
        })

    summary = (f"Traced {len(source_funcs)} source functions (signature "
               f"mentions '{type_name}') for paths to '{to_symbol}'")
    return [{"_summary": summary, "_type": type_name, "_target": to_symbol}] + results


def explain_condition(store: BuildSetStore, build_name: str,
                     file_path: str, line: int) -> dict:
    """M6 implementation of explain_condition.

    Returns the chain of enclosing #if/#ifdef groups for a line, using
    ConditionalState facts.
    """
    result = {
        "file": file_path, "line": line, "chain": [],
        "context_id": "", "config_hash": "",
    }
    # Find conditional regions in this file that enclose the line
    facts = store.project_build_kind(build_name, "ConditionalState")
    enclosing = []
    for f in facts:
        payload = f.get("payload", {})
        if payload.get("file", "") != file_path:
            continue
        start = payload.get("line", 0)
        end = payload.get("end_line", 0)
        if start <= line <= end:
            enclosing.append({
                "file": file_path,
                "line": start,
                "directive": payload.get("directive", "#if"),
                "condition": payload.get("condition", ""),
                "end_line": end,
                "taken": payload.get("taken", -1),
                "branch_states": payload.get("branch_states", []),
                "macro_inputs": payload.get("macro_inputs", []),
            })
    # Sort outermost first
    enclosing.sort(key=lambda r: r["line"])
    result["chain"] = enclosing
    return result


def compare_variants(store: BuildSetStore, build_name: str,
                    a: str, b: str, symbol_or_file: str = None) -> dict:
    """M6 implementation of compare_variants.

    Compares two builds (a, b). When symbol_or_file is given, restricts the
    comparison to facts matching that symbol name or file path; otherwise
    compares all fact kinds at the build level.
    """
    result = {
        "kind": "build", "a": a, "b": b,
        "method": "text",
        "a_provenance": {"variant": a},
        "b_provenance": {"variant": b},
        "dimensions": {},
    }

    if symbol_or_file:
        # Symbol/file mode: compare facts whose payload matches the symbol
        # name or file path, across both builds.
        result["kind"] = "symbol" if not symbol_or_file.endswith(
            (".c", ".cpp", ".h", ".hpp", ".cc", ".cxx")
        ) else "file"
        result["target"] = symbol_or_file
        for kind in ("Function", "Type", "Variable", "Symbol", "Call", "Reference"):
            facts_a = store.project_build_kind(a, kind)
            facts_b = store.project_build_kind(b, kind)
            # Filter by symbol name or file path
            def _matches(f):
                p = f.get("payload", {}) or {}
                return (symbol_or_file in (p.get("name", ""), p.get("file", ""),
                                           p.get("qualified_name", ""))
                        or symbol_or_file.lower() in p.get("name", "").lower())
            facts_a = [f for f in facts_a if _matches(f)]
            facts_b = [f for f in facts_b if _matches(f)]
            canons_a = {f.get("fact_canon", "") for f in facts_a}
            canons_b = {f.get("fact_canon", "") for f in facts_b}
            result["dimensions"][kind] = {
                "a_count": len(canons_a), "b_count": len(canons_b),
                "only_in_a": list(canons_a - canons_b)[:20],
                "only_in_b": list(canons_b - canons_a)[:20],
                "equal_count": len(canons_a & canons_b),
            }
        return result

    # Build-level comparison (no symbol_or_file)
    for kind in ("Function", "Type", "Variable", "Symbol", "Call", "Reference"):
        facts_a = store.project_build_kind(a, kind)
        facts_b = store.project_build_kind(b, kind)
        canons_a = {f.get("fact_canon", "") for f in facts_a}
        canons_b = {f.get("fact_canon", "") for f in facts_b}
        only_in_a = canons_a - canons_b
        only_in_b = canons_b - canons_a
        equal = canons_a & canons_b
        result["dimensions"][kind] = {
            "a_count": len(canons_a),
            "b_count": len(canons_b),
            "only_in_a": list(only_in_a)[:20],
            "only_in_b": list(only_in_b)[:20],
            "equal_count": len(equal),
        }

    return result


def get_vector_table(store: BuildSetStore, build_name: str,
                    limit: int = 400) -> list[dict]:
    """M6 implementation of get_vector_table.

    Scans Function facts for interrupt handler patterns (functions ending
    in _IRQHandler or named in vector-table-like patterns).
    """
    results = []
    irq_suffixes = ("_irqhandler", "_isr", "_handler")
    facts = store.project_build_kind(build_name, "Function")
    slot = 0
    for f in facts[:limit]:
        payload = f.get("payload", {})
        name = payload.get("name", "").lower()
        if any(name.endswith(suffix) for suffix in irq_suffixes):
            results.append({
                "slot": slot,
                "name": payload.get("name", ""),
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "source": "c",
                "status": "c",
            })
            slot += 1
    if not results:
        return [{"info": "no interrupt handlers found in M6 store for this build"}]
    return results


def group_equivalent_variants(store: BuildSetStore, build_name: str,
                              symbol: str) -> dict:
    """M6 implementation of group_equivalent_variants.

    Groups builds by their fingerprint for a given symbol. Builds whose
    facts for this symbol agree land in the same group.
    """
    all_builds = list_builds(store)
    groups: dict[str, list[str]] = {}

    for bname in all_builds:
        # Find the symbol in this build
        fingerprint = None
        for kind in ("Function", "Type", "Variable", "Symbol"):
            facts = store.project_build_kind(bname, kind)
            for f in facts:
                payload = f.get("payload", {})
                if payload.get("name", "") == symbol:
                    # Simple fingerprint: kind + signature + file + line
                    fingerprint = (
                        f"{kind}:{payload.get('signature', '')}:"
                        f"{payload.get('file', '')}:{payload.get('line', 0)}"
                    )
                    break
            if fingerprint:
                break
        if fingerprint:
            groups.setdefault(fingerprint, []).append(bname)
        else:
            groups.setdefault("__unobserved__", []).append(bname)

    result_groups = []
    unobserved = []
    for fp, builds in groups.items():
        if fp == "__unobserved__":
            unobserved.extend(builds)
        else:
            result_groups.append({
                "fingerprint": fp,
                "variants": builds,
                "config_hashes": [],
                "builds": [{"variant": b, "image": ""} for b in builds],
            })

    return {
        "symbol": symbol,
        "build_count": len(all_builds),
        "groups": result_groups,
        "unobserved": unobserved,
    }


def find_call_path(store: BuildSetStore, build_name: str,
                   from_name: str, to_name: str,
                   max_depth: int = 10) -> list[dict]:
    """M6 implementation of find_call_path — real BFS, not fabricated.

    Performs a real BFS over the Call fact graph from from_name to
    to_name. Returns up to 5 shortest paths.
    """
    from_lower = from_name.lower()
    to_lower = to_name.lower()

    # Build call graph: caller_name → {callee_name}
    call_graph: dict[str, set[str]] = {}
    facts = store.project_build_kind(build_name, "Call")
    for f in facts:
        payload = f.get("payload", {})
        caller = payload.get("caller_name", "")
        callee = payload.get("callee_name", "")
        if caller and callee:
            call_graph.setdefault(caller, set()).add(callee)

    # BFS from from_name to to_name
    paths = []
    queue = [(from_name, [from_name])]
    visited = {from_name}
    while queue and len(paths) < 5:
        curr, path = queue.pop(0)
        if len(path) - 1 >= max_depth:
            continue
        for callee in call_graph.get(curr, set()):
            if callee.lower() == to_lower:
                paths.append({
                    "depth": len(path),
                    "chain": " → ".join(path + [callee]),
                })
                if len(paths) >= 5:
                    break
            if callee not in visited:
                visited.add(callee)
                queue.append((callee, path + [callee]))

    if not paths:
        return [{"info": f"no path found from {from_name} to {to_name} within depth {max_depth}"}]
    return paths


def find_indirect_targets(store: BuildSetStore, build_name: str,
                          name: str, limit: int = 50) -> list[dict]:
    """M6 implementation of find_indirect_targets.

    Finds functions assigned to a function pointer field/variable named
    'name'. Looks for Reference facts with ref_kind='indirect' where the
    target is the field, and returns the assigned functions.
    """
    results = []
    name_lower = name.lower()
    facts = store.project_build_kind(build_name, "Reference")
    for f in facts[:limit]:
        payload = f.get("payload", {})
        ref_kind = payload.get("ref_kind", "")
        if ref_kind in ("indirect", "member"):
            target = payload.get("to_usr", "").lower()
            from_usr = payload.get("from_usr", "")
            if name_lower in target:
                results.append({
                    "rhs_name": payload.get("callee_name", ""),
                    "rhs_qname": payload.get("callee_name", ""),
                    "fn_ptr_type": "",
                    "method": "assignment",
                    "assign_file": payload.get("file", ""),
                    "assign_line": payload.get("line", 0),
                    "call_file": None,
                    "call_line": None,
                })
    return results
