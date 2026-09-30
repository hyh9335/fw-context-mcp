"""Conditional provenance MCP tools (T03 / ADR-011 / ADR-013).

Two read-only tools over the ``conditionals`` /
``conditional_branches`` / ``conditional_macros`` tables:

- ``explain_condition`` — the chain of enclosing conditional groups for
  one line, outermost first, each entry carrying the evaluated branch
  states of its context and the macros the evaluation depends on, with
  where each macro was defined (command line, header file, compiler
  builtin).
- ``list_conditional_regions`` — every conditional group of a file (or
  of the whole index), with its extent and evaluated branches.

The handlers follow the shared resolution pattern: ``project_root`` →
config → db → executor → config_hash, one query under the executor
lock.  Rows are keyed per context (ADR-013); the answer reports the
first context that holds rows for the file and names it.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from pydantic import Field

from ..shared.context import _normalize_file_path_query
from ._base import BaseHandler


def explain_condition(
    file_path: Annotated[str, Field(description="File that holds the line, "
        "relative to the project root (e.g. 'src/main.c').")],
    line: Annotated[int, Field(description="1-based source line to explain.")],
    project_root: Annotated[str | None, Field(description="Project root. "
        "Auto-detected if omitted.")] = None,
) -> dict[str, Any]:
    """Explain which conditional branches decide one source line.

    Returns the chain of enclosing ``#if``/``#ifdef`` groups, outermost
    first.  Each entry carries the evaluated branch states for the
    active build context — ``taken`` 1 (TAKEN), 0 (FALSE) or -1
    (NOT_EVALUATED) per branch — and ``macro_inputs``, the macros the
    evaluated conditions depend on, each with its ``def_source``
    (``file`` | ``command_line`` | ``builtin`` | ``''`` when undefined)
    and definition location.  Use it to answer "why did this branch
    compile?" and "which -D flag flips it?".

    Args:
        file_path: File that holds the line, relative to the project
            root (e.g. ``src/main.c``).
        line: 1-based source line to explain.
        project_root: Project root. Auto-detected if omitted.

    Returns:
        dict: {chain (list of dicts — one per enclosing ``#if``/``#ifdef``/
        ``#ifndef`` group, outermost first), each with ``file``, ``line``
        (root line), ``directive``, ``condition`` text, ``end_line`` (the
        closing ``#endif``), ``taken`` (the state of the branch that
        directly contains the line), ``branch_states`` (list of
        ``{line, kind, condition, taken}`` — ``taken``: 1 taken, 0 false,
        -1 not evaluated) and ``macro_inputs`` (the macros the evaluated
        conditions depend on, each with ``name``, ``def_source`` —
        ``file`` | ``command_line`` | ``builtin`` | ``''`` — and
        ``def_file``/``def_line``)}.  The dict also holds ``file``,
        ``line``, ``context_id`` and ``config_hash``.  A file with no
        conditional directives returns ``chain: []``, an empty
        ``context_id`` and a ``note`` instead.

        On failure the dict holds only ``error`` with the reason.  A line
        outside every conditional group gives an empty ``chain``.
    """
    try:
        db = BaseHandler.resolve_db_context(project_root)
    except RuntimeError as exc:
        return {"error": str(exc)}

    normalized = _normalize_file_path_query(file_path)

    def _query(c, config_hash: str) -> dict[str, Any]:
        file_row = c.execute(
            "SELECT id, path FROM files WHERE config_hash=? AND path=? ORDER BY context_id LIMIT 1",
            (config_hash, normalized),
        ).fetchone()
        if file_row is None:
            like = f"%{normalized}"
            file_row = c.execute(
                "SELECT id, path FROM files WHERE config_hash=? AND path LIKE ? ORDER BY context_id LIMIT 1",
                (config_hash, like),
            ).fetchone()
        if file_row is None:
            return {"error": f"file not indexed: {file_path}"}
        file_id, stored_path = file_row["id"], file_row["path"]

        context_row = c.execute(
            """SELECT DISTINCT context_id FROM conditionals
               WHERE config_hash=? AND file_id=? ORDER BY context_id LIMIT 1""",
            (config_hash, file_id),
        ).fetchone()
        if context_row is None:
            return {
                "file": stored_path, "line": line, "chain": [],
                "context_id": "", "note": "no conditional directives in this file",
            }
        context_id = context_row["context_id"]

        rows = c.execute(
            """SELECT id, line, directive, condition, parent_id FROM conditionals
               WHERE config_hash=? AND file_id=? AND context_id=? ORDER BY line""",
            (config_hash, file_id, context_id),
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        # Group roots and their closing #endif (parent_id → group root).
        roots = [r for r in rows if r["directive"] in ("if", "ifdef", "ifndef")]
        end_line_by_root: dict[int, int] = {}
        for r in rows:
            if r["directive"] == "endif" and r["parent_id"] in by_id:
                end_line_by_root[r["parent_id"]] = r["line"]

        chain: list[dict[str, Any]] = []
        for root in roots:
            end_line = end_line_by_root.get(root["id"], root["line"])
            if not (root["line"] <= line <= end_line):
                continue
            branches = [
                {
                    "line": b["line"], "kind": b["branch_kind"],
                    "condition": b["condition"], "taken": b["taken"],
                }
                for b in c.execute(
                    """SELECT branch_kind, line, condition, taken FROM conditional_branches
                       WHERE conditional_id=? ORDER BY line""",
                    (root["id"],),
                )
            ]
            # The state of the branch that directly contains *line*.
            taken_here = None
            for index, branch in enumerate(branches):
                stop = branches[index + 1]["line"] - 1 if index + 1 < len(branches) else end_line
                if branch["line"] <= line <= stop:
                    taken_here = branch["taken"]
                    break
            macro_inputs = [
                {
                    "name": m["macro_name"], "def_source": m["def_source"],
                    "def_file": m["def_file"], "def_line": m["def_line"],
                }
                for m in c.execute(
                    """SELECT macro_name, def_source, def_file, def_line
                       FROM conditional_macros WHERE conditional_id=? ORDER BY macro_name""",
                    (root["id"],),
                )
            ]
            chain.append({
                "file": stored_path, "line": root["line"],
                "directive": root["directive"], "condition": root["condition"],
                "end_line": end_line, "taken": taken_here,
                "branch_states": branches, "macro_inputs": macro_inputs,
            })
        return {
            "file": stored_path, "line": line,
            "context_id": context_id, "config_hash": config_hash, "chain": chain,
        }

    try:
        return db.executor.execute_sync(_query, db.config_hash)
    except Exception as exc:  # noqa: BLE001 — the error boundary of the tool
        return {"error": f"{type(exc).__name__}: {exc}"}


def list_conditional_regions(
    file_path: Annotated[str | None, Field(description="Restrict to one file "
        "(relative to the project root). Omit for every indexed file.")] = None,
    project_root: Annotated[str | None, Field(description="Project root. "
        "Auto-detected if omitted.")] = None,
    limit: Annotated[int, Field(description="Maximum regions (default 200, max 1000).")] = 200,
) -> dict[str, Any]:
    """List the conditional regions of the index with their evaluated branches.

    Each region is one ``#if``/``#ifdef``/``#ifndef`` group: its file,
    root and ``#endif`` lines, condition text, and the evaluated branch
    states of the active build context (``taken``: 1 taken, 0 false,
    -1 not evaluated).  Use it to see every conditional a build
    configuration activates at a glance.

    Args:
        file_path: Restrict to one file (relative to the project root).
            Omit for every indexed file.
        project_root: Project root. Auto-detected if omitted.
        limit: Maximum regions (default 200, max 1000).

    Returns:
        dict: {regions (list of dicts — one per conditional root group,
        each with ``file``, ``line`` (the group root), ``directive``,
        ``condition`` text, ``end_line`` (the closing ``#endif``) and
        ``taken`` — the evaluated state, 1 taken / 0 false / -1 not
        evaluated), plus ``count`` (int, the number of regions)}.  A file
        that is not indexed gives an empty ``regions`` list; on failure
        the dict holds only ``error`` with the reason.
    """
    try:
        db = BaseHandler.resolve_db_context(project_root)
    except RuntimeError as exc:
        return {"error": str(exc)}
    limit = max(1, min(int(limit), 1000))
    normalized = _normalize_file_path_query(file_path) if file_path else None

    def _query(c, config_hash: str) -> dict[str, Any]:
        if normalized is not None:
            file_row = c.execute(
                "SELECT id, path FROM files WHERE config_hash=? AND path=? ORDER BY context_id LIMIT 1",
                (config_hash, normalized),
            ).fetchone()
            if file_row is None:
                file_row = c.execute(
                    "SELECT id, path FROM files WHERE config_hash=? AND path LIKE ? ORDER BY context_id LIMIT 1",
                    (config_hash, f"%{normalized}"),
                ).fetchone()
            if file_row is None:
                return {"file": file_path, "regions": []}
            file_filter = "AND c.file_id = ?"
            params: list[Any] = [config_hash, file_row["id"]]
        else:
            file_filter = ""
            params = [config_hash]

        regions: list[dict[str, Any]] = []
        root_rows = c.execute(
            f"""SELECT c.id, c.line, c.directive, c.condition, c.context_id, f.path
                FROM conditionals c JOIN files f ON f.id = c.file_id
                WHERE c.config_hash=? {file_filter}
                  AND c.directive IN ('if', 'ifdef', 'ifndef')
                ORDER BY f.path, c.line LIMIT ?""",  # noqa: S608 — fixed filter text
            (*params, limit),
        ).fetchall()
        for root in root_rows:
            end_row = c.execute(
                """SELECT line FROM conditionals
                   WHERE parent_id=? AND directive='endif' LIMIT 1""",
                (root["id"],),
            ).fetchone()
            branches = [
                {
                    "line": b["line"], "kind": b["branch_kind"],
                    "condition": b["condition"], "taken": b["taken"],
                }
                for b in c.execute(
                    """SELECT branch_kind, line, condition, taken
                       FROM conditional_branches WHERE conditional_id=? ORDER BY line""",
                    (root["id"],),
                )
            ]
            regions.append({
                "file": root["path"], "root_line": root["line"],
                "directive": root["directive"], "condition": root["condition"],
                "end_line": end_row["line"] if end_row is not None else None,
                "context_id": root["context_id"], "branches": branches,
            })
        return {"regions": regions, "count": len(regions)}

    try:
        return db.executor.execute_sync(_query, db.config_hash)
    except Exception as exc:  # noqa: BLE001 — the error boundary of the tool
        return {"error": f"{type(exc).__name__}: {exc}"}
