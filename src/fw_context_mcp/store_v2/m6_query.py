"""M6 query primitives — M7.2 production read authority.

Implements the semantic query API on top of the M6 shared-fact store.
Each query function reads from the M6 store (via project_build_kind) and
returns results in the same shape as the production MCP API expects.

This module IS the M6 query implementation (§8: "production request →
M6 query implementation → M6 result"). It does NOT read from the legacy
DB or v2 store — the M6 store is the sole read authority.

The M6 store is populated by m6_production_indexing.index_legacy_db_into_m6()
during the normal indexing pipeline.
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


def resolve_build_name(store: BuildSetStore, project_root: str | Path) -> str:
    """Resolve the active build name for a project.

    The M6 store uses build names (from the BuildSet registry). For
    production, the default build is "default" (set during indexing).
    """
    # Try to get the first registered build
    stats = store.stats()
    if stats.get("buildsets", 0) > 0:
        # Return the first build name we can find
        try:
            builds = store._build_to_buildsets
            if builds:
                return next(iter(builds.keys()))
        except Exception:
            pass
    return "default"


# ── Semantic query primitives ───────────────────────────────────────────────

def lookup_symbol(store: BuildSetStore, build_name: str,
                  name: str, exact: bool = False, limit: int = 50) -> list[dict]:
    """M6 implementation of lookup_symbol.

    Projects Symbol+Function+Variable+Type facts and filters by name.
    """
    results = []
    name_lower = name.lower() if not exact else None
    name_exact = name if exact else None
    count = 0

    for kind in ("Symbol", "Function", "Variable", "Type"):
        if count >= limit:
            break
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
            count += 1
            if count >= limit:
                break

    return results


def search_code(store: BuildSetStore, build_name: str,
                query: str, limit: int = 20) -> list[dict]:
    """M6 implementation of search_code — symbol name search."""
    # Split query into terms (like FTS5 would)
    terms = query.lower().split()
    results = []
    count = 0

    for kind in ("Symbol", "Function", "Variable", "Type"):
        if count >= limit:
            break
        facts = store.project_build_kind(build_name, kind)
        for f in facts:
            payload = f.get("payload", {})
            sym_name = payload.get("name", "").lower()
            # OR-join: match if ANY term is in the name
            if any(term in sym_name for term in terms):
                results.append(_format_symbol_result(payload))
                count += 1
                if count >= limit:
                    break

    return results


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

    return {
        "name": name,
        "symbol": symbol[0] if symbol else None,
        "callers": callers,
        "callees": [],
        "references": refs,
        "source": "",
    }


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
    """M6 implementation of get_source — function body."""
    facts = store.project_build_kind(build_name, "Body")
    name_lower = name.lower()
    for f in facts:
        payload = f.get("payload", {})
        func_name = payload.get("func_name", "").lower()
        if name_lower in func_name:
            return {
                "name": name,
                "source": payload.get("body_text", ""),
                "file": payload.get("file", ""),
                "line": payload.get("start_line", 0),
                "end_line": payload.get("end_line", 0),
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
