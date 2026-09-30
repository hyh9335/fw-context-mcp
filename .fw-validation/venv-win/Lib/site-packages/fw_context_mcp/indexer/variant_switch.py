"""Provisional variant-switch records (T14 SLICE-E).

When the same caller's target set differs across two build contexts that
differ ONLY in variant-defining defines (a *variant pair*: different
``build_configs.variant`` labels, same ``main_file``), the index records a
per-context IN/OUT decision for every context of the project family:

- ``ref_kind='variant_switch_in'``  — the call edge IS present in this context.
- ``ref_kind='variant_switch_out'`` — the call edge is ABSENT in this context.
- ``source`` carries the confirmation status of the switch the row belongs
  to: ``variant_switch_confirmed`` when at least one differing variant pair
  has BOTH contexts observing the caller (the absence is a real branch
  decision, backed on both sides), ``variant_switch_provisional`` otherwise.

Why refs: ADR-004 keeps refs AST-proven and never stores certainty on a
refs row — these rows are annotation records, not call edges.  They use the
free-text ``ref_kind``/``source`` columns (no schema change; rows/3 frozen)
and are EXCLUDED from every production ref query that did not ask for them
(``db._refs._build_kind_filter``), so ghost-caller guards and call-graph
consumers never see them.  They stay visible in the DB for comparisons
(``ops.ctx_calls``-style joins and the explicit ``ref_kind`` request path).

Family and confirmation rules (frozen case: 3-in → 3-out on the F04
fixture, transport_send → {send_uart | send_usb}):

- Family ``F`` = every non-canonical context (``context_id != ''``) of the
  in-scope builds.  A context can only be claimed to "not take" an edge if
  it belongs to the compared family at all; a context that compiles a
  different TU never enters a variant pair (the ``main_file`` equality in
  the pair gate).
- An edge is switched when presence is MIXED across ``F`` AND at least one
  differing variant pair exists (gate).  Uniform presence records nothing.
- Confirmed iff some qualifying pair has both contexts observing the caller
  in ``symbols``; every row of that edge carries the edge's status.
- Idempotent: the pass deletes the in-scope builds' previous switch rows
  and rewrites them from the current state, so a re-index converges and a
  fresh-vs-incremental index produces equal rows.

Fast path: fewer than two distinct variant labels among the in-scope builds
(single-variant projects, and every pre-SLICE-E fixture) — no rows are ever
written; stale rows (a variant was removed) are cleaned.

Cost: one pass over the in-scope builds' call edges and one DISTINCT scan
over their caller observations, once per index run, after cross-TU backfill
(the call graph must be complete before presence is compared).
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict

from ..platform import fs as _fs
from .db._refs import VARIANT_SWITCH_REF_KIND_PREFIX

log = logging.getLogger(__name__)

REF_KIND_IN = "variant_switch_in"
REF_KIND_OUT = "variant_switch_out"

SOURCE_CONFIRMED = "variant_switch_confirmed"
SOURCE_PROVISIONAL = "variant_switch_provisional"

_CALL_EDGE_CHUNK = 400


def _norm_path(p: str) -> str:
    """Loose path key for the same-file comparisons inside one DB.

    ``build_contexts.main_file`` is an absolute resolved path and
    ``refs.from_file`` is project-relative, but the pair gate compares two
    ``main_file`` values with each other and edge grouping compares
    ``from_file`` values with each other — never one against the other.
    The normalization only evens out separators, redundant ``./`` segments
    and (on Windows) case.
    """
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    # Case-folding is a filesystem property, not an os.name branch (W1):
    # the platform adapter folds on case-insensitive filesystems (win32)
    # and passes through on case-sensitive ones (POSIX).
    return _fs.fold_path_case(p)


def record_variant_switches(conn: sqlite3.Connection, project_id: str | None = None) -> int:
    """Record per-context IN/OUT rows for variant-switched call edges.

    Scope: all builds of ``project_id`` (or every project when ``None``).
    Returns the number of rows written (0 on the fast path).  Idempotent;
    safe to call directly (tests, maintenance) or as a postprocess step.
    """
    builds = conn.execute(
        "SELECT config_hash, variant FROM build_configs"
        + (" WHERE project_id=?" if project_id else ""),
        (project_id,) if project_id else (),
    ).fetchall()
    scope_hashes = [r["config_hash"] for r in builds]
    if not scope_hashes:
        return 0

    # Fast path: fewer than two distinct variant labels — nothing can be
    # variant-attributable.  Still clean stale rows from a removed variant.
    distinct_labels = {r["variant"] or "" for r in builds}
    deleted = _delete_switch_rows(conn, scope_hashes)
    if len(distinct_labels) < 2:
        if deleted:
            conn.commit()
        return 0

    hash_set = set(scope_hashes)
    hash_params = list(hash_set)

    # Family F: every non-canonical context of the in-scope builds.
    contexts = [
        {"config_hash": r["config_hash"], "context_id": r["context_id"], "main_file": _norm_path(r["main_file"])}
        for r in conn.execute(
            f"SELECT config_hash, context_id, main_file FROM build_contexts "
            f"WHERE context_id != '' AND config_hash IN ({_qs(len(hash_params))})",
            hash_params,
        ).fetchall()
    ]
    if len(contexts) < 2:
        return 0

    variant_of = {r["config_hash"]: (r["variant"] or "") for r in builds}

    # Caller observations: (config_hash, context_id, usr) — decl or def.
    observed: set[tuple[str, str, str]] = set()
    for r in conn.execute(
        f"SELECT DISTINCT config_hash, context_id, usr FROM symbols "
        f"WHERE config_hash IN ({_qs(len(hash_params))})",
        hash_params,
    ).fetchall():
        observed.add((r["config_hash"], r["context_id"], r["usr"]))

    # Switched edges: group the in-scope call edges by edge identity.
    edges: dict[tuple[str, int, str, str], set[tuple[str, str]]] = defaultdict(set)
    for chunk in _chunks(scope_hashes, _CALL_EDGE_CHUNK):
        for r in conn.execute(
            f"SELECT DISTINCT config_hash, context_id, from_file, from_line, from_usr, to_usr "
            f"FROM refs WHERE ref_kind='call' AND config_hash IN ({_qs(len(chunk))})",
            chunk,
        ).fetchall():
            key = (r["from_file"], r["from_line"], r["from_usr"], r["to_usr"])
            edges[key].add((r["config_hash"], r["context_id"]))

    rows: list[tuple] = []
    for (from_file, from_line, from_usr, to_usr), present in edges.items():
        family = [(c["config_hash"], c["context_id"]) for c in contexts]
        outs = [(h, cid) for (h, cid) in family if (h, cid) not in present]
        if not present or not outs:
            continue  # uniform presence — no switch
        # Variant-pair gate: some differing pair must share the caller's TU.
        # Confirmation: that pair's both contexts observe the caller.
        confirmed = False
        for (h_in, cid_in) in present:
            for (h_out, cid_out) in outs:
                if variant_of.get(h_in) == variant_of.get(h_out):
                    continue
                main_in = _main_file_of(contexts, h_in, cid_in)
                main_out = _main_file_of(contexts, h_out, cid_out)
                if main_in is None or main_in != main_out:
                    continue
                confirmed = (
                    (h_in, cid_in, from_usr) in observed
                    and (h_out, cid_out, from_usr) in observed
                )
                break
            if confirmed:
                break
        source = SOURCE_CONFIRMED if confirmed else SOURCE_PROVISIONAL
        ctx_main = {(c["config_hash"], c["context_id"]): c["main_file"] for c in contexts}
        edge_key = (from_file, from_line, from_usr, to_usr)
        for (h, cid) in family:
            main_file = ctx_main[(h, cid)]
            # A context of a different TU cannot claim this edge either way.
            if not _same_tu_edge(main_file, edge_key):
                continue
            kind = REF_KIND_IN if (h, cid) in present else REF_KIND_OUT
            rows.append((h, to_usr, from_file, from_line, from_usr, kind, None, source, cid))

    deleted = _delete_switch_rows(conn, scope_hashes)
    written = 0
    for row in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO refs
                   (config_hash, to_usr, from_file, from_line, from_usr,
                    ref_kind, slot_index, source, context_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        written += cur.rowcount if cur.rowcount > 0 else 0
    conn.commit()
    if written or deleted:
        log.info(
            "variant switches: %d records (%d in / %d out), %d stale removed",
            written,
            sum(1 for r in rows if r[5] == REF_KIND_IN),
            sum(1 for r in rows if r[5] == REF_KIND_OUT),
            deleted,
        )
    return written


def _same_tu_edge(main_file: str, edge_key: tuple[str, int, str, str]) -> bool:
    """True when the edge's caller file IS the context's main file.

    ``refs.from_file`` is project-relative while ``main_file`` is absolute;
    the absolute path of the TU ends with ``/`` + the project-relative
    path of itself, so a full-path suffix match identifies the same file
    without needing the project root.  A same-basename TU in another
    directory does not match.  Header edges (caller inside an included
    header) match no TU — conservative by design.
    """
    norm = _norm_path(edge_key[0])
    return main_file == norm or main_file.endswith("/" + norm)


def _main_file_of(contexts: list[dict], config_hash: str, context_id: str) -> str | None:
    for c in contexts:
        if c["config_hash"] == config_hash and c["context_id"] == context_id:
            return c["main_file"]
    return None


def _delete_switch_rows(conn: sqlite3.Connection, scope_hashes: list[str]) -> int:
    deleted = 0
    for chunk in _chunks(scope_hashes, _CALL_EDGE_CHUNK):
        cur = conn.execute(
            f"DELETE FROM refs WHERE ref_kind LIKE '{VARIANT_SWITCH_REF_KIND_PREFIX}%' "
            f"AND config_hash IN ({_qs(len(chunk))})",
            chunk,
        )
        deleted += cur.rowcount if cur.rowcount > 0 else 0
    return deleted


def _qs(n: int) -> str:
    return ", ".join("?" * n)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
