"""Canonical projection over per-context symbol observations (ADR-002).

ADR-002's storage shape keeps ONE observation row per ``(config_hash, usr,
context_id)`` — a USR seen from two compile actions stores two rows, and a
definition never erases another context's observation.  The old
``ON CONFLICT(config_hash, usr) … WHERE excluded.is_definition = 1`` upsert is
retired: its cross-context erasure (first-def-wins) is replaced by per-context
storage plus the canonical projection this module provides.  Within ONE
context, a declaration arriving before its definition still promotes
(``regression_decl_def_promotion_pinned``).

Every consumer that answers "what is this symbol" wants the CANONICAL symbol
per ``(config_hash, usr)``: the definition observation when any context
provides one, else the first-stored observation, plus the set of contributing
contexts as provenance (ADR-002/ADR-003).

Degeneration contract: on a DB whose writers stored one context per USR (the
pre-context canonical shape), the projection returns exactly the stored rows —
today's query results are preserved.  No DB objects are created: the
consolidated schema stays closed (SLICE-B), so the projection is pure SQL.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

__all__ = [
    "CANONICAL_SYMBOL_SELECT",
    "canonical_symbol_cte",
    "canonical_symbol_for_usr",
    "contributing_contexts",
    "project_canonical_symbols",
]

# The projection rule, as SQL: rank the observations of one (config_hash, usr)
# so a definition wins, ties broken by first-stored id for determinism.
# ROW_NUMBER over that ordering keeps the query a single indexed pass.
CANONICAL_SYMBOL_SELECT = """
    SELECT {columns}
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (
                   PARTITION BY s.config_hash, s.usr
                   ORDER BY s.is_definition DESC, s.id ASC
               ) AS _canonical_rank
        FROM symbols s
        WHERE s.config_hash = ?
    )
    WHERE _canonical_rank = 1{extra_where}
"""


def _symbol_columns(conn: sqlite3.Connection) -> list[str]:
    """The real column list of ``symbols`` — read, never hardcoded."""
    return [
        row[1]
        for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
        if row[1] != "rowid"
    ]


def canonical_symbol_cte() -> str:
    """The projection subquery as SQL text; the caller binds the config_hash.

    Provided for query builders that need to join the projection into a
    larger SELECT; the :func:`project_*` helpers are the normal entry points.
    """
    return CANONICAL_SYMBOL_SELECT.replace("{columns}", "*").replace("{extra_where}", "")


def project_canonical_symbols(
    conn: sqlite3.Connection,
    config_hash: str,
    usr: str | None = None,
) -> list[sqlite3.Row]:
    """Canonical symbol rows of one build: one per ``(config_hash, usr)``.

    ``usr`` restricts the projection to one symbol.  The column set is the
    full ``symbols`` row — on a single-context DB this returns exactly the
    stored rows, so no existing single-context caller sees a difference.
    """
    cols = ", ".join(_symbol_columns(conn))
    extra = ""
    params: list[Any] = [config_hash]
    if usr is not None:
        extra = " AND usr = ?"
        params.append(usr)
    sql = CANONICAL_SYMBOL_SELECT.format(columns=cols, extra_where=extra)
    sql += " ORDER BY usr, id"
    return conn.execute(sql, params).fetchall()


def canonical_symbol_for_usr(
    conn: sqlite3.Connection,
    config_hash: str,
    usr: str,
) -> sqlite3.Row | None:
    """The one canonical row for ``(config_hash, usr)``, or None."""
    rows = project_canonical_symbols(conn, config_hash, usr=usr)
    return rows[0] if rows else None


def contributing_contexts(
    conn: sqlite3.Connection,
    config_hash: str,
    usr: str,
) -> list[str]:
    """The ordered context set that observed one USR (ADR-003 provenance).

    Canonical context first, then by first observation — deterministic for
    wire serialization.
    """
    rows = conn.execute(
        "SELECT context_id FROM symbols WHERE config_hash=? AND usr=? "
        "GROUP BY context_id ORDER BY MIN(id)",
        (config_hash, usr),
    ).fetchall()
    return [row[0] for row in rows]


def contexts_json(contexts: list[str]) -> str:
    """Serialize a contributing-context set for a result envelope."""
    return json.dumps(contexts, separators=(",", ":"))
