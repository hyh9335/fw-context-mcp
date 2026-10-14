"""M6 Direct Materialization — extract canonical facts DIRECTLY from the
libclang frontend, without requiring a completed legacy semantic DB.

This is the production semantic-materialization authority path (Issue A
remediation). It reuses the SAME libclang ExtractionResult that the legacy
indexer produces, but writes canonical M6 facts DIRECTLY — no legacy DB
intermediate.

Intended model (§8):
    Clang/frontend (extract_all)
      → canonical semantic extraction (this module)
      → dependency certificate (future: skip unchanged TUs)
      → safe reuse (BuildSet dedup via add_fact)
      → only changed/new fact materialization
      → BuildSet ownership
      → M6 World finalization

The legacy DB conversion path (index_legacy_db_into_m6) remains as an
explicit compat/benchmark mode (§41), NOT a normal production prerequisite.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Reuse the canon_* functions from the prototype (already proven correct
# in M7.1 DUAL_READ parity against the ground truth).
_REPO_SRC = Path(__file__).resolve().parents[2]
_PROTOTYPE_PATH = _REPO_SRC.parent / ".dsh-control" / "m6_research"
if str(_PROTOTYPE_PATH) not in sys.path:
    sys.path.insert(0, str(_PROTOTYPE_PATH))

from prototype_a import (  # noqa: E402
    canon_symbol, canon_function, canon_call, canon_variable,
    canon_type, canon_field, canon_enum_const, canon_inheritance,
)

from fw_context_mcp.store_v2.m6_store import BuildSetStore  # noqa: E402
from fw_context_mcp.store_v2.m6_query import open_m6_store  # noqa: E402


def materialize_unit_into_m6(
    *,
    store: BuildSetStore,
    parsed,
    project_root: Path,
    build_name: str,
    world_id: int,
    config_hash: str = "",
    context_id: str = "",
) -> dict:
    """Materialize canonical M6 facts DIRECTLY from a libclang ExtractionResult.

    This is the per-TU direct materialization hook (analogous to
    ``store_v2.certify.certify_and_publish_for_unit`` but for M6). It runs
    AFTER extract_all() and writes M6 facts without touching the legacy DB.

    Args:
        store: an open BuildSetStore (caller-managed; one per project)
        parsed: the ExtractionResult from extract_all()
        project_root: project root (for path normalization)
        build_name: build identifier for the M6 BuildSet
        world_id: real World identity (from snapshot_builds.world_id or
                  build_identity(); MUST NOT be hardcoded to 1)
        config_hash: build config hash (for traceability)
        context_id: per-TU context id (for traceability)

    Returns:
        Stats dict: {facts_indexed, facts_reused, kinds, build_name, world_id}
    """
    stats = {
        "facts_indexed": 0, "facts_reused": 0, "kinds": {},
        "build_name": build_name, "world_id": world_id,
    }
    if parsed is None:
        return stats

    builds = frozenset([build_name])

    # Register the build in the given World (NOT hardcoded 1).
    store.register_world(world_id, {build_name})

    # ── Symbols → Function / Type / Variable / Symbol facts ──
    symbols = getattr(parsed, "symbols", None) or []

    # Build usr → name map for call/reference resolution (same pattern as
    # index_legacy_db_into_m6, but from the in-memory ExtractionResult).
    usr_to_name: dict[str, str] = {}
    for sym in symbols:
        usr = getattr(sym, "usr", "") or ""
        name = getattr(sym, "name", "") or ""
        if usr and name:
            usr_to_name[usr] = name
    for sym in symbols:
        kind = getattr(sym, "kind", "") or ""
        usr = getattr(sym, "usr", "") or ""
        name = getattr(sym, "name", "") or ""
        if not usr or not name:
            continue

        file_path = str(getattr(sym, "file", "") or "")
        line = getattr(sym, "line", 0) or 0
        end_line = getattr(sym, "end_line", 0) or 0
        qualified_name = getattr(sym, "qualified_name", "") or ""
        signature = getattr(sym, "signature", "") or ""
        docstring = getattr(sym, "docstring", "") or ""
        is_definition = bool(getattr(sym, "is_definition", False))
        is_virtual = bool(getattr(sym, "is_virtual", False))
        is_pure_virtual = bool(getattr(sym, "is_pure_virtual", False))
        is_template = bool(getattr(sym, "is_template", False))
        parent_usr = getattr(sym, "parent_usr", "") or ""
        template_usr = getattr(sym, "template_usr", "") or ""

        if kind in ("function", "method", "constructor", "destructor"):
            canon = canon_function(name, usr, signature, None, "none")
            payload = {
                "name": name, "usr": usr, "kind": kind,
                "file": file_path, "line": line, "end_line": end_line,
                "qualified_name": qualified_name, "signature": signature,
                "docstring": docstring, "is_definition": is_definition,
                "is_virtual": is_virtual, "is_pure_virtual": is_pure_virtual,
                "is_template": is_template, "parent_usr": parent_usr,
                "template_usr": template_usr,
                "source": "direct_frontend",
            }
            fact_kind = "Function"
        elif kind in ("class", "struct", "union", "enum", "typedef"):
            canon = canon_type(name, kind, usr, 0, "", "default")
            payload = {
                "name": name, "usr": usr, "kind": kind,
                "file": file_path, "line": line, "end_line": end_line,
                "qualified_name": qualified_name,
                "is_definition": is_definition,
                "source": "direct_frontend",
            }
            fact_kind = "Type"
        elif kind in ("varglobal", "varlocal", "field", "variable"):
            canon = canon_variable(name, usr, "", False, "", "none", None)
            payload = {
                "name": name, "usr": usr, "kind": kind,
                "file": file_path, "line": line, "end_line": end_line,
                "qualified_name": qualified_name,
                "is_definition": is_definition, "parent_usr": parent_usr,
                "source": "direct_frontend",
            }
            fact_kind = "Variable"
        else:
            # enum_constant, namespace, macro, etc.
            enum_value = getattr(sym, "enum_value", None)
            canon = canon_symbol(name, kind, usr)
            payload = {
                "name": name, "usr": usr, "kind": kind,
                "file": file_path, "line": line, "end_line": end_line,
                "qualified_name": qualified_name, "signature": signature,
                "docstring": docstring, "is_definition": is_definition,
                "is_virtual": is_virtual, "is_pure_virtual": is_pure_virtual,
                "is_template": is_template, "parent_usr": parent_usr,
                "template_usr": template_usr,
                "enum_value": enum_value,
                "source": "direct_frontend",
            }
            fact_kind = "Symbol"

        existing = store._fact_cache.get(canon)
        store.add_fact(canon, fact_kind, builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"][fact_kind] = stats["kinds"].get(fact_kind, 0) + 1

    # ── Call sites → Call facts ──
    call_sites = getattr(parsed, "call_sites", None) or []
    for cs in call_sites:
        caller_usr = getattr(cs, "from_usr", "") or ""
        callee_usr = getattr(cs, "target_usr", "") or ""
        # Resolve names from the USR→name map (CallSite has no name fields).
        caller_name = usr_to_name.get(caller_usr, "") if caller_usr else ""
        callee_name = usr_to_name.get(callee_usr, "") if callee_usr else ""
        line = getattr(cs, "from_line", 0) or 0
        from_file = getattr(cs, "from_file", "") or ""
        certainty = getattr(cs, "certainty", "") or ""
        resolution_kind = getattr(cs, "resolution_kind", "") or ""

        canon = canon_call(caller_usr, callee_name, line, callee_usr, "", False)
        payload = {
            "caller_usr": caller_usr, "caller_name": caller_name,
            "callee_name": callee_name, "callee_usr": callee_usr,
            "line": line, "file": from_file,
            "certainty": certainty, "resolution_kind": resolution_kind,
            "source": "direct_frontend",
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Call", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Call"] = stats["kinds"].get("Call", 0) + 1

    # ── References → Reference facts ──
    references = getattr(parsed, "references", None) or []
    for ref in references:
        to_usr = getattr(ref, "to_usr", "") or getattr(ref, "target_usr", "") or ""
        from_usr = getattr(ref, "from_usr", "") or getattr(ref, "caller_usr", "") or ""
        from_file = getattr(ref, "from_file", "") or getattr(ref, "file", "") or ""
        from_line = getattr(ref, "from_line", 0) or getattr(ref, "line", 0) or 0
        ref_kind = getattr(ref, "ref_kind", "") or getattr(ref, "kind", "") or ""
        source_attr = getattr(ref, "source", "") or ""

        # Reference canon: (to_usr, from_file, from_line) is unique enough
        # for dedup; caller/callee name resolution is best-effort.
        canon = f"ref:{to_usr}:{from_file}:{from_line}:{ref_kind}"
        payload = {
            "to_usr": to_usr, "from_usr": from_usr,
            "file": from_file, "line": from_line,
            "ref_kind": ref_kind, "source": source_attr or "direct_frontend",
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Reference", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Reference"] = stats["kinds"].get("Reference", 0) + 1

    # ── Inheritance → Inheritance facts ──
    inheritance = getattr(parsed, "inheritance", None) or []
    for inh in inheritance:
        derived_usr = getattr(inh, "derived_usr", "") or getattr(inh, "child_usr", "") or ""
        base_usr = getattr(inh, "base_usr", "") or getattr(inh, "parent_usr", "") or ""
        access = getattr(inh, "access", "public") or "public"
        is_virtual = bool(getattr(inh, "is_virtual", False))

        canon = canon_inheritance(derived_usr, base_usr, access, is_virtual)
        payload = {
            "derived_usr": derived_usr, "base_usr": base_usr,
            "access": access, "is_virtual": is_virtual,
            "source": "direct_frontend",
        }
        existing = store._fact_cache.get(canon)
        store.add_fact(canon, "Inheritance", builds, payload)
        if existing is not None:
            stats["facts_reused"] += 1
        else:
            stats["facts_indexed"] += 1
        stats["kinds"]["Inheritance"] = stats["kinds"].get("Inheritance", 0) + 1

    # ── I4: Body + FileContent facts ────────────────────────────────────────
    # The M6 schema reserves m6_body_facts / m6_file_content (I4) and the
    # store exposes add_body_fact / add_file_content_fact, but nothing wired
    # them in — get_source / get_symbol_context / read_file read "Body" and
    # "FileContent" and so always came back empty under v2-only indexing.
    # This fixes the materialization side: every defined function in this TU
    # contributes one body fact (read from disk via its libclang extent), and
    # every file the TU touches contributes one file-content fact.
    try:
        _body_files: dict[str, list[str]] = {}
        for sym in symbols:
            if not (getattr(sym, "is_definition", False) or getattr(sym, "is_definition", None)):
                continue
            if getattr(sym, "kind", "") not in ("function", "method", "constructor", "destructor"):
                continue
            fpath = str(getattr(sym, "file", "") or "")
            start = getattr(sym, "line", 0) or 0
            end = getattr(sym, "end_line", 0) or 0
            usr = getattr(sym, "usr", "") or ""
            if not (fpath and usr and start and end > start):
                continue
            if fpath not in _body_files:
                try:
                    p = Path(fpath)
                    _body_files[fpath] = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    _body_files[fpath] = []
            lines = _body_files[fpath]
            if not (end <= len(lines)):
                continue
            body_text = "".join(
                lines[i - 1] + ("\n" if i < end else "") for i in range(start, end + 1)
            ).rstrip("\n")
            if not body_text.strip():
                continue
            store.add_body_fact(usr, builds, body_text, start, end)
            stats["kinds"]["Body"] = stats["kinds"].get("Body", 0) + 1
    except Exception:  # noqa: BLE001 — body capture is best-effort, never breaks indexing
        log.debug("M6 body capture skipped for %s", build_name)

    # ── I4: FileContent facts ───────────────────────────────────────────────
    # The read path (search_content / read_file) reads m6_file_content, and
    # the store's add_file_content_fact was never wired in — m6_file_content
    # stayed empty under v2-only indexing, so search_content returned 0 on
    # every real index even after the read side was fixed.  Materialize one
    # file-content fact per source file this TU touches (the TU's own file,
    # plus any file that contributes a symbol).  Best-effort: never breaks
    # indexing.
    try:
        _fc_files: dict[str, str] = {}
        for sym in symbols:
            fpath = str(getattr(sym, "file", "") or "")
            if not fpath or fpath in _fc_files:
                continue
            try:
                _fc_files[fpath] = Path(fpath).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
        # The TU's own compile unit, when known and distinct.
        tu_main = str(getattr(symbols[0], "file", "") or "") if symbols else ""
        if tu_main and tu_main not in _fc_files:
            try:
                _fc_files[tu_main] = Path(tu_main).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                pass
        for fpath, content_text in _fc_files.items():
            if not content_text.strip():
                continue
            store.add_file_content_fact(fpath, builds, content_text)
            stats["kinds"]["FileContent"] = stats["kinds"].get("FileContent", 0) + 1
    except Exception:  # noqa: BLE001 — file-content capture is best-effort
        log.debug("M6 file-content capture skipped for %s", build_name)

    stats["facts_indexed"] += stats["kinds"].get("Body", 0)
    stats["facts_total"] = stats["facts_indexed"] + stats["facts_reused"]
    return stats


def finalize_m6_world(
    *,
    store: BuildSetStore,
    world_id: int,
    build_name: str,
    config_hash: str = "",
) -> dict:
    """Finalize an M6 World after all TUs are materialized.

    This is the M6 World finalization (analogous to v2 finalize_world).
    The M6 store is append-only across TUs (each TU calls add_fact which
    merges into BuildSets), so finalization is mainly a record-keeping
    operation: count facts visible in the build and return status.

    Returns:
        {ok: bool, world_id: int, build_name: str, facts_total: int}
    """
    try:
        # Count facts visible in this build via projection.
        proj = store.project_build(build_name)
        facts_total = sum(len(v) for v in proj.values())
        log.info(
            "M6 world finalized: world_id=%d build=%s facts=%d",
            world_id, build_name, facts_total,
        )
        return {
            "ok": True, "world_id": world_id, "build_name": build_name,
            "facts_total": facts_total, "config_hash": config_hash,
        }
    except Exception as exc:
        log.error("M6 world finalization failed: %s", exc)
        return {
            "ok": False, "world_id": world_id, "build_name": build_name,
            "error": str(exc), "facts_total": 0,
        }
