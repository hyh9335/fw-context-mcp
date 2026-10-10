"""Production M6 indexing adapter — M7.2.

Populates the M6 shared-fact store from the production legacy SQLite DB
(the same DB the Clang indexer writes to). This is the M6-native indexing
entrypoint: after the legacy indexer finishes, this adapter reads the
extracted symbol/call/reference data and writes it as canonical M6 facts
with ENRICHED payloads (file, line, qualified_name, signature, docstring).

Architecture (§8 anti-false-migration):
  - The M6 store is populated from the SAME Clang extraction that
    populates the legacy DB — no separate extraction path.
  - The M6 store becomes the PRODUCTION READ AUTHORITY (M7.2).
  - The legacy DB remains as the WRITE PATH (M7.3 will migrate writes).
  - M6 query adapters read from the M6 store; the legacy DB is NOT
    consulted during production reads.

Schema reference (legacy DB, see indexer/db/_schema.py):
  - symbols: usr, name, qualified_name, kind, file_path, line, end_line,
    is_definition, signature, docstring, is_virtual, is_pure_virtual,
    is_template, parent_usr, template_usr
  - call_sites: from_usr, target_usr, from_file, from_line, certainty,
    resolution_kind
  - refs: to_usr, from_file, from_line, from_usr, ref_kind, source
  - overrides: derived_usr, base_usr
  - conditionals: file_id, line, directive, condition (via files join)
"""
from __future__ import annotations
import json
import sqlite3
import sys
import logging
from pathlib import Path
from typing import Any

from fw_context_mcp.store_v2.m6_store import BuildSetStore, create_m6_shadow_db
from fw_context_mcp.store_v2.m6_schema import init_m6_tables

# Re-use the canon functions from the prototype (single source of truth).
# Path: src/fw_context_mcp/store_v2/m6_production_indexing.py
# parents[0]=store_v2, [1]=fw_context_mcp, [2]=src, [3]=repo root
_M6_RESEARCH = Path(__file__).resolve().parents[3] / ".dsh-control" / "m6_research"
if str(_M6_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_M6_RESEARCH))

from prototype_a import (
    canon_symbol, canon_function, canon_call, canon_variable,
    canon_type, canon_field, canon_enum_const, canon_inheritance,
)

log = logging.getLogger(__name__)


def _m6_db_path(project_root: Path) -> Path:
    """Resolve the M6 store DB path for a project."""
    fw_ctx = project_root / ".fw-context"
    fw_ctx.mkdir(parents=True, exist_ok=True)
    return fw_ctx / "index-m6.db"


def open_m6_store(project_root: Path) -> BuildSetStore:
    """Open (or create) the M6 store for a project."""
    db_path = _m6_db_path(project_root)
    conn = create_m6_shadow_db(db_path)
    return BuildSetStore(conn)


def index_legacy_db_into_m6(legacy_db_path: Path, project_root: Path,
                             build_name: str = "default",
                             world_id: int | None = None) -> dict:
    """Populate the M6 store from a legacy SQLite DB.

    Reads symbol/call/reference data from the legacy DB and writes
    canonical M6 facts with enriched payloads.

    Args:
        legacy_db_path: Path to the legacy index.db
        project_root: Project root (for M6 store path)
        build_name: Build identifier for the M6 BuildSet
        world_id: Real World identity (from snapshot_builds.world_id or
                  build_identity()). If None, derives a deterministic
                  world_id from the build_name hash (NOT hardcoded 1).

    Returns:
        Stats dict: {facts_indexed, facts_reused, kinds}
    """
    store = open_m6_store(project_root)
    # Derive real World identity — NOT hardcoded 1.
    # Prefer the caller-provided world_id (from v2 snapshot_builds).
    # Fall back to a deterministic hash of build_name (stable across restarts).
    if world_id is None:
        import hashlib
        world_id = (int(hashlib.sha256(
            build_name.encode()).hexdigest()[:8], 16) % 1000000) + 1
    store.register_world(world_id, {build_name})
    builds = frozenset([build_name])

    conn = sqlite3.connect(str(legacy_db_path))
    conn.row_factory = sqlite3.Row
    stats = {"facts_indexed": 0, "facts_reused": 0, "kinds": {}}

    # Get the latest config_hash
    row = conn.execute(
        "SELECT config_hash FROM build_configs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        stats["facts_total"] = 0
        return stats
    config_hash = row["config_hash"]

    # Build usr → name map for call/reference resolution
    usr_to_name: dict[str, str] = {}
    for r in conn.execute(
        "SELECT usr, name FROM symbols WHERE config_hash = ?", (config_hash,)
    ).fetchall():
        usr_to_name[r["usr"]] = r["name"]

    # Index symbols (enriched)
    _index_symbols(conn, store, builds, config_hash, stats)
    # Index call sites (the real call graph)
    _index_call_sites(conn, store, builds, config_hash, usr_to_name, stats)
    # Index references
    _index_refs(conn, store, builds, config_hash, usr_to_name, stats)
    # Index overrides (inheritance/virtual)
    _index_overrides(conn, store, builds, config_hash, stats)

    conn.close()
    stats["facts_total"] = stats["facts_indexed"] + stats["facts_reused"]
    return stats


def _index_symbols(conn, store, builds, config_hash, stats):
    """Index symbols from the legacy DB into M6 with enriched payloads."""
    rows = conn.execute(
        """SELECT usr, name, qualified_name, kind, file_path, line,
                  end_line, is_definition, signature, docstring,
                  is_virtual, is_pure_virtual, is_template, parent_usr,
                  template_usr
           FROM symbols WHERE config_hash = ?""",
        (config_hash,)
    ).fetchall()

    for r in rows:
        kind = r["kind"]
        usr = r["usr"]
        name = r["name"]

        # Determine M6 fact kind based on the symbol kind
        if kind in ("function", "method", "constructor", "destructor"):
            canon = canon_function(name, usr, r["signature"], None, "none")
            payload = {
                "name": name,
                "usr": usr,
                "kind": kind,
                "file": r["file_path"],
                "line": r["line"],
                "end_line": r["end_line"],
                "qualified_name": r["qualified_name"],
                "signature": r["signature"],
                "docstring": r["docstring"],
                "is_definition": bool(r["is_definition"]),
                "is_virtual": bool(r["is_virtual"]),
                "is_pure_virtual": bool(r["is_pure_virtual"]),
                "is_template": bool(r["is_template"]),
                "parent_usr": r["parent_usr"],
                "template_usr": r["template_usr"],
            }
            fact_kind = "Function"
        elif kind in ("class", "struct", "union", "enum", "typedef"):
            canon = canon_type(name, kind, usr, 0, "", "default")
            payload = {
                "name": name,
                "usr": usr,
                "kind": kind,
                "file": r["file_path"],
                "line": r["line"],
                "end_line": r["end_line"],
                "qualified_name": r["qualified_name"],
                "is_definition": bool(r["is_definition"]),
            }
            fact_kind = "Type"
        elif kind in ("varglobal", "varlocal", "field", "variable"):
            canon = canon_variable(name, usr, "", False, "", "none", None)
            payload = {
                "name": name,
                "usr": usr,
                "kind": kind,
                "file": r["file_path"],
                "line": r["line"],
                "end_line": r["end_line"],
                "qualified_name": r["qualified_name"],
                "is_definition": bool(r["is_definition"]),
                "parent_usr": r["parent_usr"],
            }
            fact_kind = "Variable"
        else:
            # Generic Symbol fact (enum_constant, namespace, macro, etc.)
            canon = canon_symbol(name, kind, usr)
            payload = {
                "name": name,
                "usr": usr,
                "kind": kind,
                "file": r["file_path"],
                "line": r["line"],
                "end_line": r["end_line"],
                "qualified_name": r["qualified_name"],
                "signature": r["signature"],
                "docstring": r["docstring"],
                "is_definition": bool(r["is_definition"]),
                "is_virtual": bool(r["is_virtual"]),
                "is_pure_virtual": bool(r["is_pure_virtual"]),
                "is_template": bool(r["is_template"]),
                "parent_usr": r["parent_usr"],
                "template_usr": r["template_usr"],
            }
            fact_kind = "Symbol"

        existing = store._fact_cache.get(canon)
        store.add_fact(canon, fact_kind, builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"][fact_kind] = stats["kinds"].get(fact_kind, 0) + 1


def _index_call_sites(conn, store, builds, config_hash, usr_to_name, stats):
    """Index call edges from the legacy call_sites table into M6."""
    try:
        rows = conn.execute(
            """SELECT from_usr, target_usr, from_file, from_line,
                      certainty, resolution_kind
               FROM call_sites WHERE config_hash = ?""",
            (config_hash,)
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for r in rows:
        caller_usr = r["from_usr"] or ""
        callee_usr = r["target_usr"] or ""
        callee_name = usr_to_name.get(callee_usr, "")
        caller_name = usr_to_name.get(caller_usr, "")
        line = r["from_line"] or 0
        canon = canon_call(caller_usr, callee_name, line, callee_usr, "", False)
        payload = {
            "caller_usr": caller_usr,
            "caller_name": caller_name,
            "callee_name": callee_name,
            "callee_usr": callee_usr,
            "line": line,
            "file": r["from_file"] or "",
            "certainty": r["certainty"] or "exact",
            "resolution_kind": r["resolution_kind"] or "",
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Call", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Call"] = stats["kinds"].get("Call", 0) + 1


def _index_refs(conn, store, builds, config_hash, usr_to_name, stats):
    """Index references from the legacy refs table into M6."""
    try:
        rows = conn.execute(
            """SELECT to_usr, from_usr, from_file, from_line, ref_kind, source
               FROM refs WHERE config_hash = ?""",
            (config_hash,)
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for r in rows:
        target_usr = r["to_usr"] or ""
        src_usr = r["from_usr"] or ""
        target_name = usr_to_name.get(target_usr, "")
        src_name = usr_to_name.get(src_usr, "")
        ref_kind = r["ref_kind"] or "ref"
        line = r["from_line"] or 0
        canon = f"Reference({src_usr},{ref_kind},{target_usr},{line})"
        payload = {
            "src_usr": src_usr,
            "src_name": src_name,
            "ref_kind": ref_kind,
            "target_usr": target_usr,
            "target_name": target_name,
            "line": line,
            "file": r["from_file"] or "",
            "source": r["source"] or "",
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Reference", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Reference"] = stats["kinds"].get("Reference", 0) + 1


def _index_overrides(conn, store, builds, config_hash, stats):
    """Index virtual method overrides from the overrides table."""
    try:
        rows = conn.execute(
            """SELECT derived_usr, base_usr
               FROM overrides WHERE config_hash = ?""",
            (config_hash,)
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for r in rows:
        derived_usr = r["derived_usr"] or ""
        base_usr = r["base_usr"] or ""
        canon = canon_inheritance(derived_usr, base_usr, "public", False)
        payload = {
            "derived_usr": derived_usr,
            "base_usr": base_usr,
            "access": "public",
            "is_virtual": True,
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Inherits", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Inherits"] = stats["kinds"].get("Inherits", 0) + 1
