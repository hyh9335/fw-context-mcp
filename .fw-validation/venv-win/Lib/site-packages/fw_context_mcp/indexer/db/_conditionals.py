"""SQLite storage for conditional compilation provenance (T03 / ADR-013).

Three tables, one writer shape: **replace per file** — a parse owns its
file's rows, so the old rows go before the fresh ones land, exactly like
``replace_file_data`` does for symbols and macros.  A delete keyed on
the TU's file_id alone would leave a header's rows behind, so every
delete is keyed on the file ids the parse owns (see
``_delete_rows_owned_by``).

The command-line macro rows (``-D`` flags, ``file_id IS NULL``) are the
one exception: they belong to no file, so they are replaced per
``(config_hash, context_id)`` — the context that produced the flags is
the only writer of its own ``-D`` rows (ADR-002).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence

from ._chunking import chunked

log = logging.getLogger(__name__)


def delete_conditionals_for_files(conn: sqlite3.Connection, file_ids: Iterable[int]) -> None:
    """Delete the conditional rows OWNED by *file_ids*, children first.

    ``conditional_branches`` and ``conditional_macros`` hang off
    ``conditionals.id``, so their rows go before the group rows do.  No
    ``config_hash`` filter: a file_id already identifies one config.
    """
    ids = list(dict.fromkeys(file_ids))
    if not ids:
        return
    for chunk in chunked(ids):
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"""DELETE FROM conditional_branches WHERE conditional_id IN (
                    SELECT id FROM conditionals WHERE file_id IN ({placeholders}))""",  # noqa: S608
            chunk,
        )
        conn.execute(
            f"""DELETE FROM conditional_macros WHERE conditional_id IN (
                    SELECT id FROM conditionals WHERE file_id IN ({placeholders}))""",  # noqa: S608
            chunk,
        )
        conn.execute(
            f"DELETE FROM conditionals WHERE file_id IN ({placeholders})",  # noqa: S608
            chunk,
        )


def replace_conditionals_for_file(
    conn: sqlite3.Connection,
    config_hash: str,
    context_id: str,
    file_id: int,
    conditionals: Sequence[tuple],
    branches: Sequence[tuple],
    macros: Sequence[tuple],
) -> int:
    """Replace one file's conditional rows; return the number of groups.

    Args:
        conditionals: ``(line, directive, condition, parent_line)``
            tuples — *parent_line* is the enclosing group's line within
            the same file, or ``None`` at the top level.
        branches: ``(root_line, branch_kind, line, condition, taken)``.
        macros: ``(root_line, macro_name, def_source, def_file, def_line)``.

    Rows are inserted in line order so a parent group is always in
    place before its branches reference it.
    """
    conn.execute(
        """DELETE FROM conditional_branches WHERE conditional_id IN (
               SELECT id FROM conditionals
               WHERE config_hash=? AND context_id=? AND file_id=?)""",
        (config_hash, context_id, file_id),
    )
    conn.execute(
        """DELETE FROM conditional_macros WHERE conditional_id IN (
               SELECT id FROM conditionals
               WHERE config_hash=? AND context_id=? AND file_id=?)""",
        (config_hash, context_id, file_id),
    )
    conn.execute(
        "DELETE FROM conditionals WHERE config_hash=? AND context_id=? AND file_id=?",
        (config_hash, context_id, file_id),
    )

    ids_by_line: dict[int, int] = {}
    for line, directive, condition, parent_line in sorted(conditionals, key=lambda r: r[0]):
        parent_id = ids_by_line.get(parent_line) if parent_line is not None else None
        cursor = conn.execute(
            """INSERT INTO conditionals
               (config_hash, context_id, file_id, line, directive, condition, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (config_hash, context_id, file_id, line, directive, condition, parent_id),
        )
        ids_by_line[line] = int(cursor.lastrowid)

    for root_line, kind, line, condition, taken in branches:
        group_id = ids_by_line.get(root_line)
        if group_id is None:
            log.debug("branch at line %s has no group row — dropped", line)
            continue
        conn.execute(
            """INSERT INTO conditional_branches
               (conditional_id, branch_kind, line, condition, taken)
               VALUES (?, ?, ?, ?, ?)""",
            (group_id, kind, line, condition, int(taken)),
        )

    for root_line, macro_name, def_source, def_file, def_line in macros:
        group_id = ids_by_line.get(root_line)
        if group_id is None:
            continue
        conn.execute(
            """INSERT INTO conditional_macros
               (conditional_id, macro_name, def_source, def_file, def_line)
               VALUES (?, ?, ?, ?, ?)""",
            (group_id, macro_name, def_source, def_file, def_line),
        )
    return len(ids_by_line)


def replace_command_line_macros(
    conn: sqlite3.Connection,
    config_hash: str,
    context_id: str,
    macros: Sequence[tuple],
) -> int:
    """Replace the ``-D`` rows (``file_id IS NULL``) of one context.

    Args:
        macros: ``(name, value, expanded_value, is_function_like)``
            tuples.  The writer dedupes by name — two TUs of one context
            carry the same flags, and the partial UNIQUE index
            ``(config_hash, context_id, name) WHERE file_id IS NULL``
            allows exactly one row per name.

    ``expanded_value`` starts empty; the macros.py driver fills it on
    the postprocessing pass, as it does for file macros.
    """
    conn.execute(
        """DELETE FROM macros
           WHERE config_hash=? AND context_id=? AND file_id IS NULL""",
        (config_hash, context_id),
    )
    rows = [
        (config_hash, None, name, value, expanded, 0, int(is_fn), "command_line", context_id)
        for name, value, expanded, is_fn in {m[0]: m for m in macros}.values()
    ]
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO macros
           (config_hash, file_id, name, value, expanded_value, line,
            is_function_like, def_source, context_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return len(rows)
