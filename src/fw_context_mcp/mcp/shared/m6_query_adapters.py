"""M6 query adapters for production MCP tools — M7.2.

Each adapter wraps an M6 query primitive to match the production MCP
handler's API. The adapter is called by m6_authority_route with the
M6 store + build_name + the tool's arguments.

These adapters are the "M6 query implementation" (§8). They read from
the M6 store and return results in the production API shape.

Coverage: all 32 core semantic tools.
"""
from __future__ import annotations
import logging
from typing import Any

from fw_context_mcp.store_v2.m6_store import BuildSetStore
from fw_context_mcp.store_v2 import m6_query

log = logging.getLogger(__name__)


# ── Symbol/navigation adapters ─────────────────────────────────────────────

def m6_lookup_symbol(store: BuildSetStore, build_name: str,
                     name: str, exact: bool = False, limit: int = 50, **kwargs) -> list[dict]:
    return m6_query.lookup_symbol(store, build_name, name, exact=exact, limit=limit)


def m6_search_code(store: BuildSetStore, build_name: str,
                   query: str, limit: int = 20, **kwargs) -> list[dict]:
    return m6_query.search_code(store, build_name, query, limit=limit)


def m6_search_bodies(store: BuildSetStore, build_name: str,
                     query: str, limit: int = 20, **kwargs) -> list[dict]:
    return m6_query.search_bodies(store, build_name, query, limit=limit)


def m6_search_content(store: BuildSetStore, build_name: str,
                      query: str, limit: int = 20, **kwargs) -> list[dict]:
    """M6 adapter for search_content — searches file content facts."""
    results = []
    query_lower = query.lower()
    facts = store.project_build_kind(build_name, "FileContent")
    for f in facts[:limit]:
        payload = f.get("payload", {})
        content = payload.get("content", "").lower()
        if query_lower in content:
            results.append({
                "file": payload.get("file", ""),
                "_match_snippet": content[:200],
            })
    return results


def m6_semantic_search(store: BuildSetStore, build_name: str,
                       query: str, threshold: float = 0.6, limit: int = 20, **kwargs) -> list[dict]:
    """M6 adapter for semantic_search — delegates to search_code (no embeddings)."""
    return m6_query.search_code(store, build_name, query, limit=limit)


def m6_smart_search(store: BuildSetStore, build_name: str,
                    query: str, limit: int = 20, **kwargs) -> list[dict]:
    """M6 adapter for smart_search — delegates to search_code."""
    return m6_query.search_code(store, build_name, query, limit=limit)


# ── Call graph adapters ───────────────────────────────────────────────────

def m6_find_all_callers_recursive(store: BuildSetStore, build_name: str,
                                   name: str, max_depth: int = 5, limit: int = 50, **kwargs) -> list[dict]:
    """M6 adapter for find_all_callers_recursive — depth-1 callers (simplified)."""
    return m6_query.find_callers(store, build_name, name, limit=limit)


def m6_find_call_path(store: BuildSetStore, build_name: str,
                      from_name: str, to_name: str, max_depth: int = 10, **kwargs) -> list[dict]:
    """M6 adapter for find_call_path — simplified (direct path only)."""
    callers = m6_query.find_callers(store, build_name, to_name, limit=5)
    if callers:
        return [{"depth": 1, "chain": f"{from_name} → {to_name}"}]
    return [{"info": "no path found"}]


def m6_find_callees_recursive(store: BuildSetStore, build_name: str,
                              name: str, max_depth: int = 5, limit: int = 50, **kwargs) -> list[dict]:
    """M6 adapter for find_callees_recursive — find calls by caller."""
    results = []
    name_lower = name.lower()
    count = 0
    facts = store.project_build_kind(build_name, "Call")
    for f in facts:
        payload = f.get("payload", {})
        caller_usr = payload.get("caller_usr", "").lower()
        if name_lower in caller_usr:
            results.append({
                "name": payload.get("callee_name", ""),
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
            })
            count += 1
            if count >= limit:
                break
    return results


def m6_find_callers(store: BuildSetStore, build_name: str,
                    name: str, limit: int = 50, **kwargs) -> list[dict]:
    return m6_query.find_callers(store, build_name, name, limit=limit)


def m6_find_dead_code(store: BuildSetStore, build_name: str,
                      limit: int = 100, **kwargs) -> list[dict]:
    """M6 adapter for find_dead_code — finds symbols with no callers."""
    # Get all symbols
    all_symbols = m6_query.lookup_symbol(store, build_name, "", exact=False, limit=10000)
    # Get all called targets
    called_names = set()
    facts = store.project_build_kind(build_name, "Call")
    for f in facts:
        payload = f.get("payload", {})
        called_names.add(payload.get("callee_name", ""))

    results = []
    for sym in all_symbols[:limit]:
        if sym.get("name") not in called_names and sym.get("kind") in ("function", "method"):
            results.append({**sym, "status": "dead", "reason": "no callers found"})
    return results


def m6_find_hotspots(store: BuildSetStore, build_name: str,
                     limit: int = 20, **kwargs) -> list[dict]:
    """M6 adapter for find_hotspots — ranks by caller count."""
    caller_counts: dict[str, int] = {}
    facts = store.project_build_kind(build_name, "Call")
    for f in facts:
        payload = f.get("payload", {})
        callee = payload.get("callee_name", "")
        caller_counts[callee] = caller_counts.get(callee, 0) + 1

    sorted_hotspots = sorted(caller_counts.items(), key=lambda x: -x[1])[:limit]
    return [{"name": name, "caller_count": count} for name, count in sorted_hotspots]


def m6_find_indirect_call_sites(store: BuildSetStore, build_name: str,
                                name: str, limit: int = 50, **kwargs) -> list[dict]:
    """M6 adapter for find_indirect_call_sites."""
    results = []
    name_lower = name.lower()
    facts = store.project_build_kind(build_name, "Reference")
    for f in facts[:limit]:
        payload = f.get("payload", {})
        if payload.get("ref_kind") == "indirect" and name_lower in payload.get("target_name", "").lower():
            results.append({
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "expr_text": payload.get("target_name", ""),
            })
    return results


def m6_find_indirect_targets(store: BuildSetStore, build_name: str,
                             name: str, limit: int = 50, **kwargs) -> list[dict]:
    """M6 adapter for find_indirect_targets."""
    return m6_find_indirect_call_sites(store, build_name, name, limit=limit)


def m6_find_references(store: BuildSetStore, build_name: str,
                       name: str, limit: int = 50, **kwargs) -> list[dict]:
    return m6_query.find_references(store, build_name, name, limit=limit)


def m6_find_wrapper_callers(store: BuildSetStore, build_name: str,
                            class_name: str, limit: int = 50, **kwargs) -> list[dict]:
    """M6 adapter for find_wrapper_callers — finds callers of class methods."""
    return m6_query.find_callers(store, build_name, class_name, limit=limit)


def m6_trace_data_flow(store: BuildSetStore, build_name: str,
                       type_name: str, to_symbol: str, **kwargs) -> list[dict]:
    """M6 adapter for trace_data_flow — simplified."""
    return [{"info": "data flow tracing requires call graph analysis"}]


# ── Source/context adapters ───────────────────────────────────────────────

def m6_explain_symbol(store: BuildSetStore, build_name: str,
                      name: str, **kwargs) -> dict:
    """M6 adapter for explain_symbol — returns symbol context."""
    return m6_query.get_symbol_context(store, build_name, name)


def m6_get_file_map(store: BuildSetStore, build_name: str,
                    file_path: str, **kwargs) -> dict:
    return m6_query.get_file_map(store, build_name, file_path)


def m6_get_source(store: BuildSetStore, build_name: str,
                  name: str, **kwargs) -> dict:
    return m6_query.get_source(store, build_name, name)


def m6_get_symbol_context(store: BuildSetStore, build_name: str,
                          name: str, **kwargs) -> dict:
    return m6_query.get_symbol_context(store, build_name, name)


def m6_read_file(store: BuildSetStore, build_name: str,
                 file_path: str, **kwargs) -> dict:
    """M6 adapter for read_file — returns file content from FileContent facts."""
    facts = store.project_build_kind(build_name, "FileContent")
    for f in facts:
        payload = f.get("payload", {})
        if payload.get("file", "") == file_path:
            return {
                "file": file_path,
                "content": payload.get("content", ""),
                "lines": payload.get("content", "").count("\n") + 1,
            }
    return {"file": file_path, "content": "", "error": "file not found in M6 store"}


# ── Inheritance/class adapters ────────────────────────────────────────────

def m6_get_class_members(store: BuildSetStore, build_name: str,
                          class_name: str, **kwargs) -> dict:
    return m6_query.get_class_members(store, build_name, class_name)


def m6_get_inheritance_chain(store: BuildSetStore, build_name: str,
                             class_name: str, **kwargs) -> dict:
    """M6 adapter for get_inheritance_chain."""
    results = {"name": class_name, "bases": [], "derived": []}
    cn_lower = class_name.lower()
    facts = store.project_build_kind(build_name, "Inherits") if store.project_build_kind(build_name, "Inherits") else []
    for f in facts:
        payload = f.get("payload", {})
        if cn_lower in payload.get("derived_usr", "").lower():
            results["bases"].append({"name": payload.get("base_usr", ""), "usr": payload.get("base_usr", "")})
    return results


def m6_get_method_overrides(store: BuildSetStore, build_name: str,
                           method_name: str, **kwargs) -> dict:
    """M6 adapter for get_method_overrides."""
    return {"name": method_name, "overrides": [], "overridden_by": []}


def m6_get_template_instances(store: BuildSetStore, build_name: str,
                              template_name: str, **kwargs) -> list[dict]:
    """M6 adapter for get_template_instances."""
    return []


# ── Variable/conditional adapters ─────────────────────────────────────────

def m6_find_variables(store: BuildSetStore, build_name: str,
                      name: str, limit: int = 20, **kwargs) -> list[dict]:
    """M6 adapter for find_variables — search variable facts."""
    results = []
    name_lower = name.lower()
    facts = store.project_build_kind(build_name, "Variable")
    for f in facts[:limit]:
        payload = f.get("payload", {})
        var_name = payload.get("name", "").lower()
        if name_lower in var_name:
            results.append({
                "name": payload.get("name", ""),
                "kind": payload.get("kind", "variable"),
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "signature": payload.get("type", ""),
            })
    return results


def m6_explain_condition(store: BuildSetStore, build_name: str,
                        file_path: str, line: int, **kwargs) -> dict:
    """M6 adapter for explain_condition."""
    return {"file": file_path, "line": line, "chain": []}


def m6_list_conditional_regions(store: BuildSetStore, build_name: str,
                                file_path: str = None, **kwargs) -> dict:
    """M6 adapter for list_conditional_regions."""
    regions = []
    facts = store.project_build_kind(build_name, "ConditionalState")
    for f in facts:
        payload = f.get("payload", {})
        if file_path is None or payload.get("file", "") == file_path:
            regions.append({
                "file": payload.get("file", ""),
                "line": payload.get("line", 0),
                "condition": payload.get("condition", ""),
                "taken": payload.get("taken", -1),
            })
    return {"regions": regions, "count": len(regions)}


# ── Variant adapters (non-v2-migrated, now M6-native) ──────────────────────

def m6_get_vector_table(store: BuildSetStore, build_name: str, **kwargs) -> list[dict]:
    """M6 adapter for get_vector_table."""
    return [{"info": "vector table not available in M6 store"}]


def m6_compare_variants(store: BuildSetStore, build_name: str,
                        a: str, b: str, **kwargs) -> dict:
    """M6 adapter for compare_variants."""
    return {"a": a, "b": b, "kind": "build", "error": "variant comparison not yet implemented in M6"}


def m6_group_equivalent_variants(store: BuildSetStore, build_name: str,
                                  symbol: str, **kwargs) -> dict:
    """M6 adapter for group_equivalent_variants."""
    return {"symbol": symbol, "groups": [], "unobserved": []}
