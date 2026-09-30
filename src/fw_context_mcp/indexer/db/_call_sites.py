"""Call-site storage — one row per syntactic call expression (T05, ADR-004).

The ``call_sites`` table is the call-site query surface: every call
expression the extractor saw gets a row with ``certainty`` (exact /
possible / unresolved), ``resolution_kind``, the ``candidate_usrs`` JSON
list, and ``origin`` (ast / token_fallback / raw_text_fallback).  ``refs``
stays AST-proven — ADR-004 layer 1 keeps certainty OUT of plain refs, so
a fallback-resolved call lives only here, never silently presented as
proven downstream.

Rows are keyed per compile context (``context_id``, ADR-002): the same
file under two ``-D`` sets of one build keeps both observations.  The
canonical projection of "who calls whom" is a query-layer view over this
table plus refs — NOT a table of its own (ADR-002).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence

from ._chunking import chunked

log = logging.getLogger(__name__)

__all__ = [
    "count_call_sites",
    "count_unresolved_call_sites",
    "delete_call_sites_for_files",
    "find_call_sites",
    "find_possible_callers",
    "insert_call_sites_batch",
]


def insert_call_sites_batch(
    conn: sqlite3.Connection, rows: list[tuple], context_id: str = ""
) -> int:
    """Insert call-site rows for one parse.

    Each row: (config_hash, from_file, from_line, from_col, from_usr,
    certainty, resolution_kind, target_usr, candidate_usrs, origin).
    ``candidate_usrs`` is the pre-serialized JSON list (``"[]"`` when
    empty).  *context_id* attributes the observation to one compile
    context (ADR-002); default ``""`` is the canonical context.

    Idempotency per (context, file) is the replace contract: the stale
    rows of every file a parse owns were deleted by
    ``replace_file_data()`` before these inserts run, in the same
    transaction as the refs replace (T01's affected-context contract).
    The table carries no UNIQUE index — dedup within one parse is the
    extractor's ``seen`` set, dedup across parses is the delete.
    """
    cur = conn.executemany(
        """INSERT INTO call_sites (config_hash, from_file, from_line, from_col,
                                   from_usr, certainty, resolution_kind,
                                   target_usr, candidate_usrs, origin, context_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [row + (context_id,) for row in rows],
    )
    return cur.rowcount


def delete_call_sites_for_files(
    conn: sqlite3.Connection,
    config_hash: str,
    from_files: Sequence[str],
    context_id: str | None = None,
) -> int:
    """Delete the call-site rows that originate in *from_files*.

    Same header-ownership problem as ``delete_refs_for_files``: a re-parse
    of the TU also re-walks every header it includes, so the delete must
    key on the path, not on the TU's file id.  *context_id* scopes the
    delete to one compile context (ADR-002); ``None`` keeps the unscoped
    purge behavior.
    """
    from ._refs import _delete_by_from_file

    return _delete_by_from_file(
        conn, "call_sites", config_hash, from_files, context_id
    )


def count_call_sites(
    conn: sqlite3.Connection, config_hash: str, certainty: str | None = None
) -> int:
    """Number of call-site rows for the build, optionally one certainty."""
    if certainty:
        row = conn.execute(
            "SELECT COUNT(*) FROM call_sites WHERE config_hash=? AND certainty=?",
            (config_hash, certainty),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM call_sites WHERE config_hash=?", (config_hash,)
        ).fetchone()
    return int(row[0]) if row else 0


def count_unresolved_call_sites(conn: sqlite3.Connection, config_hash: str) -> int:
    """How many call sites the index could not resolve at all.

    The A05 default semantics pair every exact-edge answer with this
    number: it is the honest size of the unknown part of the call graph
    (undeclared members, dependent template calls, degraded TUs).
    """
    return count_call_sites(conn, config_hash, certainty="unresolved")


def find_call_sites(
    conn: sqlite3.Connection,
    config_hash: str,
    certainty: str | None = None,
    target_usr: str | None = None,
    from_usr: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Query call-site rows, optionally filtered.

    Returns dicts with the row fields; ``candidate_usrs`` is parsed back
    to a list.  Deterministic order: file, line, column.
    """
    where = ["config_hash=?"]
    params: list = [config_hash]
    if certainty:
        where.append("certainty=?")
        params.append(certainty)
    if target_usr is not None:
        where.append("target_usr=?")
        params.append(target_usr)
    if from_usr is not None:
        where.append("from_usr=?")
        params.append(from_usr)
    sql = (
        "SELECT * FROM call_sites WHERE "
        + " AND ".join(where)
        + " ORDER BY from_file, from_line, from_col"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_possible_callers(
    conn: sqlite3.Connection, config_hash: str, target_usr: str, limit: int = 50
) -> list[dict]:
    """POSSIBLE callers of *target_usr* — the include_possible merge set.

    Two sources, both uncertain by definition:

    1. ``call_sites`` rows with ``certainty='possible'`` whose
       ``target_usr`` or ``candidate_usrs`` names *target_usr* — the
       name-fallback guesses (token / raw-text fallbacks).
    2. Dispatch synthesis (query-time, resolution_kind
       ``dispatch_synthetic``): exact call edges in refs to a BASE method
       that *target_usr* overrides.  A call through the base may dispatch
       to the override, so each such edge is a possible caller — pulled
       from the ``overrides`` table, never fabricated into refs.

    Every row carries ``certainty='possible'``; nothing here is proven.
    """
    seen: dict[tuple, dict] = {}
    for r in conn.execute(
        """SELECT from_file, from_line, from_col, from_usr, target_usr,
                  resolution_kind, candidate_usrs, origin
           FROM call_sites
           WHERE config_hash=? AND certainty='possible' AND from_usr IS NOT NULL
           ORDER BY from_file, from_line, from_col""",
        (config_hash,),
    ).fetchall():
        cands = _parse_candidates(r["candidate_usrs"])
        if r["target_usr"] == target_usr:
            hits = cands or [target_usr]
        elif target_usr in cands:
            hits = [target_usr]
        else:
            continue
        key = (r["from_file"], r["from_line"], r["from_usr"])
        seen.setdefault(
            key,
            {
                "from_file": r["from_file"],
                "from_line": r["from_line"],
                "from_usr": r["from_usr"],
                "certainty": "possible",
                "resolution_kind": r["resolution_kind"] or "name_fallback",
                "candidate_usrs": cands,
                "origin": r["origin"],
                "via": "call_sites",
            },
        )
    try:
        base_rows = conn.execute(
            """SELECT r.from_usr, r.from_file, r.from_line
               FROM refs r
               JOIN overrides o
                 ON o.config_hash = r.config_hash AND o.base_usr = r.to_usr
               WHERE r.config_hash=? AND o.derived_usr=?
                 AND r.ref_kind='call' AND r.from_usr IS NOT NULL""",
            (config_hash, target_usr),
        ).fetchall()
    except sqlite3.Error:
        log.exception("find_possible_callers dispatch synthesis failed")
        base_rows = []
    for r in base_rows:
        key = (r["from_file"], r["from_line"], r["from_usr"])
        seen.setdefault(
            key,
            {
                "from_file": r["from_file"],
                "from_line": r["from_line"],
                "from_usr": r["from_usr"],
                "certainty": "possible",
                "resolution_kind": "dispatch_synthetic",
                "candidate_usrs": [target_usr],
                "origin": "overrides",
                "via": "dispatch",
            },
        )
    out = sorted(seen.values(), key=lambda d: (d["from_file"], d["from_line"]))
    return out[:limit]


def _row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["candidate_usrs"] = _parse_candidates(d.get("candidate_usrs"))
    return d


def _parse_candidates(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []
