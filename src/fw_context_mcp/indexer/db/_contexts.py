"""build_contexts storage — the compile contexts of one build (ADR-001/005).

One row per ``(config_hash, context_id)``; the identity itself is computed by
:mod:`fw_context_mcp.indexer.context_identity`, the single shared helper.
This module is the write/read surface for the ``build_contexts`` table that
SLICE-B's consolidated schema created — it adds NO schema objects.

The CANONICAL context (``context_id=''``, ``entry_index=0``) is the registered
home of pre-context rows and of write paths that carry no context yet; the
schema seeds it per build.  Real indexing writes one non-canonical row per
compile action, idempotently: re-running the same compile_commands.json must
upsert the same identities, never duplicate or reshuffle.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "get_build_contexts",
    "upsert_build_context",
]


def upsert_build_context(
    conn: sqlite3.Connection,
    config_hash: str,
    context_id: str,
    *,
    entry_index: int = 0,
    main_file: str = "",
    compile_dir: str = "",
    canonical_argv: str = "",
    response_hashes: str = "",
    output_name: str = "",
    defines_json: str = "",
    flags_hash: str = "",
) -> None:
    """Insert or refresh one context row; idempotent per (config, context).

    ``ON CONFLICT ... DO NOTHING`` on the identity columns: a context's
    identity fields are a function of its compile action, so a re-run
    recomputes the same values and the first registration stands.  This keeps
    the runner safe to re-enter on unchanged TUs.
    """
    conn.execute(
        """INSERT INTO build_contexts
               (config_hash, context_id, entry_index, main_file, compile_dir,
                canonical_argv, response_hashes, output_name, defines_json,
                flags_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(config_hash, context_id) DO NOTHING""",
        (
            config_hash,
            context_id,
            entry_index,
            main_file,
            compile_dir,
            canonical_argv,
            response_hashes,
            output_name,
            defines_json,
            flags_hash,
        ),
    )


def get_build_contexts(
    conn: sqlite3.Connection,
    config_hash: str,
    include_canonical: bool = False,
) -> list[sqlite3.Row]:
    """Return the context rows of one build, canonical first, then by entry."""
    sql = (
        "SELECT id, config_hash, context_id, entry_index, main_file, "
        "compile_dir, canonical_argv, response_hashes, output_name, "
        "defines_json, flags_hash FROM build_contexts WHERE config_hash=?"
    )
    if not include_canonical:
        sql += " AND context_id != ''"
    sql += " ORDER BY (context_id = '') DESC, entry_index, context_id"
    return conn.execute(sql, (config_hash,)).fetchall()
