"""File-record storage layer for fw-context-mcp index.

File tracking
─────────────
Every translation unit and header seen by libclang has a row in the
``files`` table, keyed on ``(config_hash, path)``.  The table stores:

  • ``language`` — ``"c"`` or ``"cpp"``, set during compilation-database
    parsing.  Determines which parser flags libclang uses on re-parse.
  • ``mtime`` — last-modified time; used for fast staleness checks.
  • ``content_hash`` — SHA-256 hash of source + compiler flags +
    manifest entry.  Enables content-addressable staleness: even when
    mtime differs, a matching hash means the TU can be skipped.
  • ``source_hash`` — SHA-256 of the raw source file (without flags).
    Used to detect purely cosmetic changes (whitespace, comment edits)
    that do not affect symbols.
  • ``flags_hash`` — SHA-256 of normalized compiler flags.  Detects
    when a TU's include-path or define set changes without touching
    the source file itself.

Content storage
───────────────
The ``content`` column holds ifdef-filtered file text — only code that
actually compiles for the active build config.  Inactive ``#ifdef``
branches are replaced with blank lines to preserve line numbers.  This
is the source of truth for ``search_content`` and the ``files_fts``
FTS5 index.  Content is loaded lazily during indexing and is not
updated on every re-parse of a symbol-only change.

is_project marking
──────────────────
Each file row carries an ``is_project`` flag (0 or 1).  Project files
are those under user-defined project paths (``src/``, ``lib/``,
``app/``, ``include/`` by default) or custom paths from
``config.toml``.  Vendor/SDK paths (e.g. ``mbed-os/``, ``zephyr/``,
``.pio/``) are marked ``is_project=0``.  This flag propagates to the
``symbols.is_project`` column during symbol insertion and enables the
``project_only`` filter in all search, callgraph, and maintenance tools.

Deletion order on file purge
─────────────────────────────
When a source file is removed from the project, all index records
must be cleaned across 8+ tables.  The order is not arbitrary:

  1. **Inheritance/overrides** — deleted first because they reference
     USRs that will be removed.
  2. **Dangling incoming refs** — edges from surviving files that
     pointed at USRs in the purged file.  Cleaned before those USRs
     disappear, using the collected USR list.
  3. **Vector embeddings** — vec_symbols rows referencing symbol IDs.
  4. **Macros** — macros for the file (FTS triggers clean the index).
  5. **Symbols** — the core symbol rows (FTS triggers clean the index).
  6. **References, indirect call sites, fp_assignments** — use
     file_path, not USR, so they are cleaned last (the USRs they
     referenced are already gone).
  7. **File record** — the ``files`` row itself, deleted last so all
     ``file_id`` foreign-key references are already cleared.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import NamedTuple

from ...utils import escape_like as _escape_like
from ._chunking import chunked

__all__ = [
    "FileHashRecord",
    "delete_orphan_files",
    "delete_symbols_for_file",
    "get_file_directives",
    "get_file_hashes",
    "get_file_map",
    "get_file_mtime_indexed",
    "get_file_mtimes",
    "list_config_file_paths",
    "purge_file_records",
    "purge_missing_files_batch",
    "replace_file_directives",
    "search_directives_fts",
    "upsert_file",
]


def upsert_file(
    conn: sqlite3.Connection,
    config_hash: str,
    path: str,
    language: str,
    generated: bool = False,
    mtime: float = 0.0,
    content_hash: str = "",
    source_hash: str = "",
    flags_hash: str = "",
    context_id: str = "",
) -> int:
    """Insert or update a file record, returning its row id.

    ``generated`` is converted from ``bool`` to ``int`` (0/1) for storage.
    ``language`` must be ``"c"`` or ``"cpp"``.

    Uses ``ON CONFLICT`` so re-indexing the same ``(config_hash, path,
    context_id)`` updates language and mtime without duplication.

    *context_id* is the compile context observing this file (ADR-001/002).
    The default ``""`` is the canonical context: pre-context callers and
    non-TU writers keep today's behavior.  One file under two ``-D`` sets of
    one build gets two rows — the per-context observation that makes
    ``files.content`` and the hashes per-context facts instead of
    last-writer-wins (T02 RC2 at the storage layer).

    ``generated`` takes MAX for two reasons, one in each direction.

    It could not be RAISED at all: the column was written on INSERT and the
    ON CONFLICT clause never mentioned it, so a row that another caller
    created first kept 0 whatever a later caller knew.  Five callers reach
    this function and only one of them has the build-output patterns; the
    other four pass the default False.  Measured on the ESP32 project once the
    column was first filled: the manifest said 56 generated headers and the
    database said 49, and the seven that differed were exactly the rows some
    other caller had inserted first.

    It must not be CLEARED either, which is what a plain
    ``generated=excluded.generated`` would do the moment one of those four
    callers touched the row again.  ``generated`` is a property of the PATH,
    not of one caller's knowledge.  Same rule, and the same reason, as
    merge_header_records() in manifest.py.

    MAX therefore holds WITHIN a run and cannot heal ACROSS two.  An earlier
    version of this text claimed the case could not arise, because a change
    to build_dir_patterns mints a new config_hash.  Measured, that is false:
    narrowing PlatformIO from ``.pio/`` to ``.pio/build/`` left the
    config_hash of the ESP32 project and of the STM32 project identical, because the path pass drops
    in-project paths anyway.  _step_reconcile_generated() is what makes the
    column right again, and it is the authoritative write.

    The three hash columns follow the same rule for the same reason: an
    EMPTY hash never overwrites a stored one.  Four of the five callers pass
    no hashes at all, and reindex_file is one of them — it re-parses one
    translation unit through store_symbols_for_unit() without them, and that
    used to erase content_hash, source_hash and flags_hash for that row.

    The cost of erasing them was NOT a wrong answer.  Tier 1 compares the
    mtime, which a re-parse does not move, so the next run never even read
    the cleared value.  It surfaced only later: once something moved the
    mtime without changing the text, Tier 2 found an empty content_hash,
    could not take its shortcut, and paid one libclang parse to rebuild what
    was already known.  files.content_hash has exactly one reader,
    _check_and_parse_unit.  source_hash has two: the staleness checks in
    mcp/shared/stale.py compare it against the file to tell a real change
    from one where git only rewrote the mtime.  flags_hash still has none.

    That second reader is why source_hash may no longer ride on this rule
    alone.  A caller that MOVES mtime and knows the content must also pass
    source_hash, or the row ends with the hash of the old text beside the
    stamp of the new one, and the staleness checks then call the file
    changed for as long as the pair disagrees.  store_symbols_for_unit()
    computes it for exactly that reason.  The rule here stays as it is: it
    protects a caller that knows NOTHING about the content, which is a
    different case.

    Args:
        conn: Open database connection.
        config_hash: Build config hash the file belongs to.
        path: Relative or absolute file path.
        language: ``"c"`` or ``"cpp"`` — set during compilation database parsing.
        generated: Whether the file is auto-generated (default False).
        mtime: Last-modified timestamp (float, seconds since epoch).
        content_hash: SHA-256 hash of source + flags + manifest_entry content (default "").
        source_hash: SHA-256 hash of the source file content.
        flags_hash: SHA-256 hash of normalized compiler flags for this TU.

    Returns:
        int: The ``files.id`` of the inserted or updated row.
    """
    cur = conn.execute(
        """INSERT INTO files(config_hash, path, language, generated, mtime,
                             content_hash, source_hash, flags_hash, context_id)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(config_hash, path, context_id) DO UPDATE SET
               language=excluded.language,
               mtime=excluded.mtime,
               content_hash=CASE WHEN excluded.content_hash = ''
                            THEN files.content_hash ELSE excluded.content_hash END,
               source_hash=CASE WHEN excluded.source_hash = ''
                           THEN files.source_hash ELSE excluded.source_hash END,
               flags_hash=CASE WHEN excluded.flags_hash = ''
                          THEN files.flags_hash ELSE excluded.flags_hash END,
               generated=MAX(files.generated, excluded.generated)
           RETURNING id""",
        (
            config_hash,
            path,
            language,
            int(generated),
            mtime,
            content_hash,
            source_hash,
            flags_hash,
            context_id,
        ),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]
    # Fallback for SQLite < 3.35 without RETURNING support.
    # Use SELECT instead of last_insert_rowid() — the latter returns
    # the rowid of the last INSERT, which is wrong when ON CONFLICT
    # triggered an UPDATE (rowid stays unchanged or is zero).
    #
    # The SELECT fallback is always correct: it asks for the id of the
    # row that now exists at (config_hash, path), regardless of whether
    # it was created by INSERT or matched by ON CONFLICT.
    return conn.execute(
        "SELECT id FROM files WHERE config_hash=? AND path=? AND context_id=?",
        (config_hash, path, context_id),
    ).fetchone()[0]


def get_file_mtimes(conn: sqlite3.Connection, config_hash: str) -> dict[str, tuple[int, float]]:
    """Return {path: (file_id, mtime)} for all files under config_hash.

    .. deprecated::
        Use :func:`get_file_hashes` instead — it returns content hashes
        needed for content-addressable staleness detection.
    """
    rows = conn.execute("SELECT id, path, mtime FROM files WHERE config_hash=?", (config_hash,)).fetchall()
    return {r["path"]: (r["id"], r["mtime"]) for r in rows}


class FileHashRecord(NamedTuple):
    """Stored file metadata for content-addressable staleness detection."""
    file_id: int
    mtime: float
    content_hash: str
    source_hash: str
    flags_hash: str


# What a caller may pass where only ``[0]`` (the file_id) is read.
#
# The runner hands get_file_hashes() output — dict[str, FileHashRecord] — to
# helpers annotated dict[str, tuple[int, float]].  It works because
# FileHashRecord is a NamedTuple with file_id at index 0, and mypy did not
# object because those helpers take the dict positionally through several
# layers.  The annotation was a statement about the data that was false.
FileIdLookup = Mapping[str, "tuple[int, float] | FileHashRecord"]


def get_file_hashes(conn: sqlite3.Connection, config_hash: str) -> dict[str, FileHashRecord]:
    """Return ``{path: FileHashRecord}`` for content-addressable staleness detection.

    Used by the runner for content-addressable staleness detection — even
    when mtime differs, a matching content_hash means the TU can be skipped.

    The old ``deps_hash`` and ``deps_exist`` columns are deprecated and no
    longer populated — staleness is now determined from ``manifest.json``.
    """
    rows = conn.execute(
        """SELECT id, path, mtime, content_hash, source_hash, flags_hash
           FROM files WHERE config_hash=?""",
        (config_hash,),
    ).fetchall()
    return {
        r["path"]: FileHashRecord(
            r["id"],
            r["mtime"],
            r["content_hash"],
            r["source_hash"],
            r["flags_hash"],
        )
        for r in rows
    }


def get_file_hashes_by_context(
    conn: sqlite3.Connection, config_hash: str
) -> dict[tuple[str, str], FileHashRecord]:
    """Return ``{(path, context_id): FileHashRecord}`` for staleness checks.

    The per-context view the runner needs once one build holds several
    observations of one path (F02): a TU's Tier-1/2 comparison must read the
    row of ITS context, or a first-indexed context's record would falsely
    skip a second context's never-indexed command (T02 RC2 incremental
    false-claim).  ``get_file_hashes`` stays for callers that want the
    legacy path-keyed view.
    """
    rows = conn.execute(
        """SELECT id, path, context_id, mtime, content_hash, source_hash, flags_hash
           FROM files WHERE config_hash=?""",
        (config_hash,),
    ).fetchall()
    return {
        (r["path"], r["context_id"]): FileHashRecord(
            r["id"],
            r["mtime"],
            r["content_hash"],
            r["source_hash"],
            r["flags_hash"],
        )
        for r in rows
    }


def delete_symbols_for_file(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete all symbols for a file (FTS ad trigger cleans up FTS index)."""
    conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))



def delete_orphan_files(
    conn: sqlite3.Connection,
    config_hash: str,
    project_root: Path | None = None,
) -> int:
    """Delete file records that have no symbols, no macros, and no ifdef-filtered
    content for *config_hash*.

    Only deletes truly empty file entries — files with filled ``content``
    are preserved even when they have no symbols (forward-declaration
    headers are still useful for ``search_content``).

    A row whose FILE is genuinely empty on disk is NOT such an entry, and it
    is kept.  Zephyr compiles ``misc/empty_file.c`` — a 0-byte placeholder —
    three times per build, and a 0-byte source yields no symbols, no macros
    and no content, so it matched all three conditions.  Its row went away
    on every run, Tier 1 needs a row to skip a translation unit, and the
    next run parsed it again: measured on the Zephyr project, all 9 builds
    reported "3 updated" for ever and never reached "0 updated".

    *project_root* resolves a relative stored path.  Without it a relative
    path cannot be checked, and the row is deleted as before — the callers
    that have a root pass it.

    Called after indexing or reindex to clean up stale entries from
    removed source files.  Returns the number of rows deleted.
    """
    candidates = conn.execute(
        """SELECT id, path FROM files WHERE config_hash = ?
           AND id NOT IN (SELECT DISTINCT file_id FROM symbols WHERE config_hash = ?)
           AND id NOT IN (SELECT DISTINCT file_id FROM macros WHERE config_hash = ?)
           AND content = ''""",
        (config_hash, config_hash, config_hash),
    ).fetchall()
    if not candidates:
        return 0

    doomed: list[int] = []
    for row in candidates:
        p = Path(row["path"])
        if not p.is_absolute():
            if project_root is None:
                doomed.append(row["id"])
                continue
            p = project_root / p
        try:
            # A file that is gone, or that has text this index did not
            # capture, is a stale row.  A file that really is 0 bytes is not.
            if p.stat().st_size != 0:
                doomed.append(row["id"])
        except OSError:
            doomed.append(row["id"])

    deleted = 0
    for batch in chunked(doomed):
        placeholders = ",".join("?" * len(batch))
        cur = conn.execute(
            f"DELETE FROM files WHERE id IN ({placeholders})",  # noqa: S608 — placeholders only
            batch,
        )
        deleted += cur.rowcount
    return deleted


def get_file_mtime_indexed(conn: sqlite3.Connection, config_hash: str, path: str) -> float | None:
    """Return the stored mtime for a file, or None if not in the index."""
    row = conn.execute(
        "SELECT mtime FROM files WHERE config_hash=? AND path=?",
        (config_hash, path),
    ).fetchone()
    return row["mtime"] if row else None


def get_file_map(
    conn: sqlite3.Connection,
    config_hash: str,
    file_path: str,
    signatures: bool = False,
    max_per_kind: int = 30,
) -> dict:
    """Return all symbols in a file grouped by kind — fast structural overview.

    *file_path* is relative to the project root (e.g. ``src/net_msg.cpp``),
    matching the ``symbols.file_path`` column.  Exact match first, then
    LIKE-based path match so both ``src/main.cpp`` and ``main.cpp`` work.

    *signatures* adds full signatures (off by default to keep output compact).
    *max_per_kind* limits items per kind; the ``count`` field always shows the
    real total.  Set to 0 for no limit.
    """
    rows = conn.execute(
        """SELECT name, qualified_name, kind, line, col, end_line,
                  is_definition, signature, enum_value
           FROM symbols
           WHERE config_hash = ? AND file_path = ?
           ORDER BY kind, line""",
        (config_hash, file_path),
    ).fetchall()

    # Two-phase path matching — the symbols table stores the relative
    # path as libclang sees it (e.g. "src/net_msg.cpp"), but callers
    # may pass just the filename ("net_msg.cpp") or a partial path.
    #
    # Phase 1: exact file_path match.  Uses the index on (config_hash,
    # file_path), fast for the common case.
    #
    # Phase 2: LIKE-based fallback with escaped wildcards.  The
    # _escape_like call prevents "_" and "%" in the filename itself
    # from matching as wildcards (important for filenames like
    # "hw_config_v2.cpp" where "v2" must be literal).  The prefix "%"
    # allows "net_msg.cpp" to match "src/net/net_msg.cpp".
    if not rows:
        rows = conn.execute(
            """SELECT name, qualified_name, kind, line, col, end_line,
                      is_definition, signature, enum_value
               FROM symbols
               WHERE config_hash = ? AND (file_path = ? OR file_path LIKE ? ESCAPE '\\')
               ORDER BY kind, line""",
            (config_hash, file_path, f"%{_escape_like(file_path)}"),
        ).fetchall()

    groups: dict[str, dict] = {}
    for r in rows:
        kind = r["kind"]
        if kind not in groups:
            groups[kind] = {"count": 0, "items": []}
        # Count is ALWAYS incremented — it reflects the real total.
        # Items are conditionally appended — bounded by max_per_kind.
        # This separation lets the caller see "methods: 45 (showing 30)"
        # instead of silently truncating to 30 with no indication.
        groups[kind]["count"] += 1

        if kind == "enum_constant":
            # Enum constants are grouped by parent enum to avoid a flat
            # list of hundreds of unrelated constants.  Each parent enum
            # forms a subgroup with its own name, count, and constants.
            #
            # Parent extraction uses the qualified name: everything before
            # the last "::" is the parent enum.  An unqualified constant
            # (C-style enum — no "::") gets "(anonymous)" as the parent
            # label, keeping the grouping uniform.
            # Group by parent enum: extract everything before last "::"
            qn = r["qualified_name"] or ""
            if "::" in qn:
                parent_enum = qn.rsplit("::", 1)[0]
            else:
                parent_enum = "(anonymous)"
            # Initialize subgroup dict — stored in a special key on the kind group
            subgroups = groups[kind].setdefault("_subgroups", {})
            if parent_enum not in subgroups:
                subgroups[parent_enum] = {"name": parent_enum, "count": 0, "constants": []}
            subgroups[parent_enum]["count"] += 1
            if max_per_kind == 0 or subgroups[parent_enum]["count"] <= max_per_kind:
                entry: dict = {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "line": r["line"],
                }
                if r["enum_value"] is not None:
                    entry["enum_value"] = r["enum_value"]
                subgroups[parent_enum]["constants"].append(entry)
        else:
            if max_per_kind == 0 or len(groups[kind]["items"]) < max_per_kind:
                entry = {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "line": r["line"],
                }
                # The SELECT above has always read `end_line`, and nothing
                # here gave it to the caller.  A map that gives the start
                # and not the end makes the reader open the file to find
                # where a symbol stops.  0 means a declaration, which has
                # no extent, thus the key appears only when it answers.
                if r["end_line"]:
                    entry["end_line"] = r["end_line"]
                if signatures and r["signature"]:
                    entry["signature"] = r["signature"]
                groups[kind]["items"].append(entry)

    # Convert enum_constant subgroups from dict to sorted list
    for group in groups.values():
        if "_subgroups" in group:
            group["subgroups"] = sorted(group.pop("_subgroups").values(), key=lambda g: g["name"])

    return {
        "file": file_path,
        "total_symbols": len(rows),
        "symbols": groups,
    }


def _delete_dangling_incoming_refs(conn: sqlite3.Connection, config_hash: str, purged_usrs: list[str]) -> None:
    """Delete edges from surviving files that point at now-purged symbols.

    Only called from the file-gone-forever purge path. Do NOT fold this
    into delete_inheritance_for_file/delete_overrides_for_file — those
    two are also called from the reindex/reuse path where the file is being
    RE-PARSED, not deleted.
    """
    # 400, not the default 500: the fp_assignments statement below binds
    # each item TWICE (lhs_usr and rhs_usr), so 1 + 2 * 500 would be 1001 and
    # exceed the limit on SQLite before 3.32.  See chunked().
    for batch in chunked(purged_usrs, 400):
        ph = ",".join("?" * len(batch))
        conn.execute(
            f"DELETE FROM inheritance WHERE config_hash = ? AND base_usr IN ({ph})",
            (config_hash, *batch),
        )
        conn.execute(
            f"DELETE FROM overrides WHERE config_hash = ? AND base_usr IN ({ph})",
            (config_hash, *batch),
        )
        conn.execute(
            f"DELETE FROM refs WHERE config_hash = ? AND to_usr IN ({ph})",
            (config_hash, *batch),
        )
        conn.execute(
            f"DELETE FROM indirect_call_sites WHERE config_hash = ? AND target_usr IN ({ph})",
            (config_hash, *batch),
        )
        conn.execute(
            f"DELETE FROM fp_assignments WHERE config_hash = ? AND (lhs_usr IN ({ph}) OR rhs_usr IN ({ph}))",
            (config_hash, *batch, *batch),
        )


def _delete_rows_owned_by(
    conn: sqlite3.Connection,
    config_hash: str,
    files: list[tuple[int, str]],
) -> list[str]:
    """Delete every row OWNED by *files*; return the USRs that were removed.

    "Owned" means the row's own file is one of *files*: its symbols, macros,
    inheritance edges, embeddings, LLM analysis, vectors, and the references /
    call sites / function pointer assignments that ORIGINATE in it.

    Deliberately NOT touched, because the two callers need different things:

    - **Incoming references.** Rows in OTHER files that point at these USRs.
      A re-parse must keep them — the symbols are about to be re-inserted. A
      file that left the build must lose them, so ``purge_files`` and friends
      call :func:`_delete_dangling_incoming_refs` afterwards with the returned
      USRs.
    - **The ``files`` rows themselves.** A re-parse reuses them.
    - **``overrides``.** They key on ``derived_usr``, not on a symbol id, so
      they stay valid across a re-parse.  Deleting them here would lose them
      for good when ``analyze_overrides`` is off and nothing rebuilds them.

    Order is forced: inheritance resolves USR through ``symbols``, and the
    embedding / analysis deletes select by ``symbol_id``, so both must run
    before the symbols go.  ``vec_symbols`` is a sqlite-vec virtual table and
    may not exist at all.
    """
    from ._inheritance import delete_inheritance_for_file
    from ._call_sites import delete_call_sites_for_files
    from ._refs import (
        delete_fp_assignments_for_files,
        delete_indirect_call_sites_for_files,
        delete_refs_for_files,
    )
    from ._symbols import delete_macros_for_files

    purged_usrs: list[str] = []
    for file_id, _ in files:
        purged_usrs.extend(
            r[0] for r in conn.execute(
                "SELECT usr FROM symbols WHERE config_hash = ? AND file_id = ?",
                (config_hash, file_id),
            ).fetchall()
        )
        delete_inheritance_for_file(conn, config_hash, file_id)
        selector = "(SELECT id FROM symbols WHERE file_id = ?)"
        # Explicit rather than relying on ON DELETE CASCADE, so the order
        # holds even if a future schema defers the constraint.
        conn.execute(f"DELETE FROM embeddings WHERE symbol_id IN {selector}", (file_id,))
        conn.execute(f"DELETE FROM llm_analysis WHERE symbol_id IN {selector}", (file_id,))
        try:
            conn.execute(f"DELETE FROM vec_symbols WHERE symbol_id IN {selector}", (file_id,))
        except sqlite3.OperationalError:
            pass  # sqlite-vec not loaded — the virtual table does not exist
        delete_symbols_for_file(conn, file_id)

    delete_macros_for_files(conn, [fid for fid, _ in files])
    # Conditional provenance rows (T03) hang off the file ids too: a
    # re-parse must drop the old groups/branches/macro inputs of every
    # owned file or a moved directive would leave stale rows behind.
    from ._conditionals import delete_conditionals_for_files

    delete_conditionals_for_files(conn, [fid for fid, _ in files])
    # Reference-table rows are keyed by (config_hash, from_file) but carry a
    # context_id (ADR-002): one path under two -D sets of one build holds two
    # observation sets.  Scope each delete to the context of the file rows
    # being cleared, or a re-parse of one context would erase another
    # context's refs for the same path (T02 RC2 at the storage layer).  A
    # file_id no longer resolves (row already gone) falls back to the
    # unscoped delete.
    ctx_by_id: dict[int, str] = {}
    ids = [fid for fid, _ in files]
    for chunk in chunked(ids):
        placeholders = ",".join("?" * len(chunk))
        ctx_by_id.update(
            (r["id"], r["context_id"])
            for r in conn.execute(
                f"SELECT id, context_id FROM files WHERE id IN ({placeholders})",  # noqa: S608
                chunk,
            ).fetchall()
        )
    paths_by_ctx: dict[str, list[str]] = {}
    for fid, path in files:
        if not path:
            continue
        ctx = ctx_by_id.get(fid)
        if ctx is None:
            paths_by_ctx.setdefault("", []).append(path)
        else:
            paths_by_ctx.setdefault(ctx, []).append(path)
    for ctx, paths in paths_by_ctx.items():
        delete_refs_for_files(conn, config_hash, paths, context_id=ctx)
        delete_indirect_call_sites_for_files(conn, config_hash, paths, context_id=ctx)
        delete_fp_assignments_for_files(conn, config_hash, paths, context_id=ctx)
        # Call-site rows (T05) share the same (config_hash, from_file,
        # context_id) keying as the reference tables — replaced in the
        # same transaction, or a re-parse would double every row.
        delete_call_sites_for_files(conn, config_hash, paths, context_id=ctx)
    return purged_usrs


def replace_file_data(
    conn: sqlite3.Connection,
    config_hash: str,
    file_ids: list[int],
) -> None:
    """Clear the rows of *file_ids* so a fresh parse can re-insert them.

    This is the ONE entry point for the re-parse case.  It used to be three
    separate cleanups keyed three different ways — symbols and inheritance by
    file_id, refs and call sites by path, macros by file_id again — and every
    one of them had to be told which files the parse owned.  Each derived that
    answer itself, and each got it wrong for rows owned by a header.

    Paths for the reference tables are read from ``files.path`` rather than
    supplied, so the string used to delete is by construction the string that
    was stored.  Callers need only the file ids.

    The ``files`` rows survive: the parse is about to refresh them.
    """
    ids = list(dict.fromkeys(file_ids))
    if not ids:
        return
    rows: list[tuple[int, str]] = []
    for chunk in chunked(ids):
        placeholders = ",".join("?" * len(chunk))
        rows.extend(
            (r["id"], r["path"])
            for r in conn.execute(
                f"SELECT id, path FROM files WHERE id IN ({placeholders})",  # noqa: S608
                chunk,
            ).fetchall()
        )
    if rows:
        _delete_rows_owned_by(conn, config_hash, rows)


def purge_file_records(
    conn: sqlite3.Connection,
    config_hash: str,
    file_id: int,
    file_path: str,
    *,
    db_dir: Path | None = None,
    write_lock_held: bool = False,
    transaction_held: bool = False,
) -> int:
    """Delete all index records for a single file across all tables.

    Returns the number of symbols removed.
    """
    from ._connection import transaction
    from ._inheritance import delete_overrides_for_file
    from ._locking import write_lock

    lock = (
        write_lock(db_dir, timeout=60.0)
        if not write_lock_held and db_dir is not None
        else nullcontext()
    )
    tx = transaction(conn) if not transaction_held else nullcontext()
    with lock, tx:
        purged_usrs = _delete_rows_owned_by(conn, config_hash, [(file_id, file_path)])
        # The file is gone for good, so three things the re-parse path keeps
        # must also go: overrides recorded for its methods, edges from
        # SURVIVING files that point at its USRs, and the files row.
        delete_overrides_for_file(conn, config_hash, file_id)
        if purged_usrs:
            _delete_dangling_incoming_refs(conn, config_hash, purged_usrs)
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return len(purged_usrs)


def purge_missing_files_batch(
    conn: sqlite3.Connection,
    config_hash: str,
    files: list[tuple[int, str]],
    *,
    db_dir: Path | None = None,
    write_lock_held: bool = False,
    transaction_held: bool = False,
) -> int:
    """Delete all index records for multiple files in one lock/transaction.

    Same table order as :func:`purge_file_records`, one lock acquisition
    and one transaction for the whole batch.  Dangling incoming references
    are cleaned in a single pass over the union of all purged USRs.

    Returns the total number of symbols removed.
    """
    from ._connection import transaction
    from ._inheritance import delete_overrides_for_file
    from ._locking import write_lock

    lock = (
        write_lock(db_dir, timeout=60.0)
        if not write_lock_held and db_dir is not None
        else nullcontext()
    )
    tx = transaction(conn) if not transaction_held else nullcontext()
    with lock, tx:
        purged_usrs = _delete_rows_owned_by(conn, config_hash, list(files))
        for file_id, _ in files:
            delete_overrides_for_file(conn, config_hash, file_id)
        # One pass over the union of all purged USRs, so an edge between two
        # files that both left the build is not looked up twice.
        if purged_usrs:
            _delete_dangling_incoming_refs(conn, config_hash, purged_usrs)
        for file_id, _ in files:
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return len(purged_usrs)


# ── file_directives: the T12/ADR-012 directive channel ────────────────────
#
# The table itself is SLICE-B schema (``_schema.py``): one row per
# directive line of a project file, keyed ``UNIQUE(config_hash, path,
# line, kind)``, with an eager FTS5 mirror (``file_directives_fts``) kept
# current by triggers.  These are the read/write helpers the modes ride on.


def replace_file_directives(
    conn: sqlite3.Connection,
    config_hash: str,
    path: str,
    directives: list[tuple[int, str, str]],
) -> int:
    """Write *directives* as the whole directive set of one file.

    *directives* holds ``(line, kind, text)`` tuples.  **Replace per
    file** — the old rows of ``(config_hash, path)`` go before the fresh
    ones land, the same writer shape the conditionals tables use.  A
    line a re-parse no longer lexes as a directive must leave no stale
    row behind, so delete-then-insert is the contract; the FTS triggers
    keep ``file_directives_fts`` in step.

    Directive text is a fact of the FILE, not of one compile context, so
    the table carries no ``context_id`` — the same build's contexts write
    identical rows and the replace is idempotent.

    Returns the number of rows written.
    """
    conn.execute(
        "DELETE FROM file_directives WHERE config_hash=? AND path=?",
        (config_hash, path),
    )
    if not directives:
        return 0
    conn.executemany(
        "INSERT INTO file_directives (config_hash, path, line, kind, text) "
        "VALUES (?, ?, ?, ?, ?)",
        [(config_hash, path, line, kind, text) for line, kind, text in directives],
    )
    return len(directives)


def get_file_directives(
    conn: sqlite3.Connection,
    config_hash: str,
    path: str,
    kinds: frozenset[str] | None = None,
) -> list[dict]:
    """The directive rows of one file, in line order.

    Each row is ``{"line", "kind", "text"}``.  *kinds* optionally
    restricts the result to one set of directive names — the conditional
    skeleton uses ``{"if", "ifdef", "ifndef", "elif", "else", "endif"}``.
    """
    sql = (
        "SELECT line, kind, text FROM file_directives "
        "WHERE config_hash=? AND path=?"
    )
    params: list[object] = [config_hash, path]
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        sql += f" AND kind IN ({placeholders})"  # noqa: S608
        params.extend(sorted(kinds))
    sql += " ORDER BY line"
    return [dict(r) for r in conn.execute(sql, params)]


def search_directives_fts(
    conn: sqlite3.Connection,
    config_hash: str,
    match_expr: str,
    limit: int,
    project_only: bool = False,
) -> list[sqlite3.Row]:
    """FTS5 search over the directive channel (``file_directives_fts``).

    Returns up to *limit* rows of ``(path, line, kind, text, language,
    mtime, is_project, _match_snippet)``, best match first.  ``language``
    and ``mtime`` come from any one ``files`` row of the path — the
    directive rows are per build, the file metadata is per observation.

    Raises ``sqlite3.OperationalError`` when the FTS5 *match_expr* is
    malformed — the caller owns the LIKE fallback and the rejection
    notice, like every other FTS5 search.
    """
    project_filter = ""
    if project_only:
        project_filter = (
            "AND EXISTS (SELECT 1 FROM files pf WHERE pf.config_hash=d.config_hash "
            "AND pf.path=d.path AND pf.is_project=1)"
        )
    return conn.execute(
        f"""SELECT d.path, d.line, d.kind, d.text,
                   COALESCE((SELECT f.language FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), '') AS language,
                   COALESCE((SELECT f.mtime FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), 0.0) AS mtime,
                   COALESCE((SELECT f.is_project FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), 1) AS is_project,
                   snippet(file_directives_fts, 1, '<b>', '</b>', '…', 80) AS _match_snippet
            FROM file_directives_fts
            JOIN file_directives d ON d.id = file_directives_fts.rowid
            WHERE file_directives_fts MATCH ? AND d.config_hash = ?
              {project_filter}
            ORDER BY rank
           LIMIT ?""",  # noqa: S608
        (match_expr, config_hash, limit),
    ).fetchall()


def search_directives_like(
    conn: sqlite3.Connection,
    config_hash: str,
    terms: list[str],
    limit: int,
    project_only: bool = False,
) -> list[sqlite3.Row]:
    """LIKE fallback over ``file_directives`` — every term must appear.

    Same row shape as :func:`search_directives_fts`, no rank: results come
    in line order per path.  Used when the index predates the FTS table or
    FTS5 refuses the query.
    """
    project_filter = ""
    if project_only:
        project_filter = (
            "AND EXISTS (SELECT 1 FROM files pf WHERE pf.config_hash=d.config_hash "
            "AND pf.path=d.path AND pf.is_project=1)"
        )
    clauses = []
    params: list[object] = [config_hash]
    for term in terms:
        clauses.append("d.text LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(term)}%")
    where = " AND ".join(clauses) if clauses else "1=1"
    return conn.execute(
        f"""SELECT d.path, d.line, d.kind, d.text,
                   COALESCE((SELECT f.language FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), '') AS language,
                   COALESCE((SELECT f.mtime FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), 0.0) AS mtime,
                   COALESCE((SELECT f.is_project FROM files f
                             WHERE f.config_hash=d.config_hash AND f.path=d.path
                             LIMIT 1), 1) AS is_project,
                   substr(d.text, 1, 200) AS _match_snippet
            FROM file_directives d
            WHERE d.config_hash = ? AND ({where})
              {project_filter}
            ORDER BY d.path, d.line
           LIMIT ?""",  # noqa: S608
        (*params, limit),
    ).fetchall()


def list_config_file_paths(
    conn: sqlite3.Connection,
    config_hash: str,
    project_only: bool = False,
) -> list[sqlite3.Row]:
    """Every indexed path of one build, project code first.

    Candidate order for the raw-mode disk scan: project files first, then
    vendor files, each group in insertion order.  One row per path (a
    per-context build would otherwise list the same TU once per context).
    """
    project_filter = "WHERE config_hash=? AND is_project=1" if project_only else (
        "WHERE config_hash=?"
    )
    return conn.execute(
        f"""SELECT path, language, mtime FROM files
            {project_filter}
            GROUP BY path
            ORDER BY MAX(is_project) DESC, MIN(rowid)""",  # noqa: S608
        (config_hash,),
    ).fetchall()
