"""Variant behavior grouping (T14 SLICE-E).

Groups a project's build variants by the OBSERVED BEHAVIOR of one symbol:
two builds whose observations of the symbol agree are behavior-equivalent
for that symbol, regardless of how many variant labels they carry.  The
F04 fixture (transport_send with ``#ifdef IMPL_B``) splits into exactly two
groups — {A, C} (the label without the macro and the unrelated-macro label
behave identically) and {B}.

The fingerprint (C8) hashes three evidence channels of one (config_hash,
usr) observation:

1. the symbol's normalized signature;
2. the normalized token stream of its stored (ifdef-filtered) body — empty
   for declarations, which makes two builds that only declare the symbol
   agree on this channel;
3. the sorted set of confirmed outgoing call edges (``ref_kind`` in
   ``call``/``indirect``; derived variant-switch rows excluded).

A04 rule: the fingerprint is an equality key only — never a certainty
claim, never stored on the symbol row.  Grouping answers "which builds see
the same behavior", not "are these builds correct".

Deterministic output: groups are ordered by descending size, then by their
smallest config_hash, so the same DB always yields the same list.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

# Outgoing-edge kinds that count as behavior for the fingerprint.  Derived
# annotation rows (variant_switch*) are excluded by the explicit kind list.
_BEHAVIOR_EDGE_KINDS = ("call", "indirect")

_TOKEN_RE = re.compile(r"\w+")


def behavior_fingerprint(
    conn: sqlite3.Connection,
    config_hash: str,
    usr: str,
) -> str:
    """SHA-256 over the normalized signature, body tokens, and confirmed
    outgoing edges of one (config_hash, usr) observation."""
    row = conn.execute(
        """SELECT signature, source, is_definition FROM symbols
           WHERE config_hash=? AND usr=?
           ORDER BY is_definition DESC, id LIMIT 1""",
        (config_hash, usr),
    ).fetchone()
    signature = row["signature"] if row else ""
    body = row["source"] if (row and row["is_definition"]) else ""
    edges = [
        r[0]
        for r in conn.execute(
            f"""SELECT DISTINCT to_usr FROM refs
                WHERE config_hash=? AND from_usr=?
                  AND ref_kind IN ({', '.join('?' * len(_BEHAVIOR_EDGE_KINDS))})
                ORDER BY to_usr""",
            (config_hash, usr, *_BEHAVIOR_EDGE_KINDS),
        ).fetchall()
    ]
    parts = [
        " ".join(_TOKEN_RE.findall(signature or "")),
        " ".join(_TOKEN_RE.findall(body or "")),
        ",".join(edges),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def group_equivalent_variants(
    conn: sqlite3.Connection,
    symbol: str,
    project_id: str | None = None,
    image: str = "",
) -> dict:
    """Group the in-scope builds by the observed behavior of *symbol*.

    Returns ``{"symbol": ..., "build_count": N, "groups": [...]}`` where
    each group is ``{"fingerprint", "variants", "config_hashes", "builds"}``.
    ``builds`` carries the full provenance tuple of every member build —
    ``{"variant", "image", "config_hash"}``; the index identifies a build BY
    its ``config_hash`` (the primary key of ``build_configs``), there is no
    separate build id.  Group members are ordered by ``config_hash`` so the
    same DB yields byte-identical output on every platform.  Builds without
    any observation of *symbol* are reported in ``unobserved`` so callers
    can tell "equivalent" from "not compiled here".  Completed builds only
    (``manifest_verification != 'indexing'``), every variant label — the
    scope the wildcard MCP surface resolves to.  *image* optionally narrows
    the scope to builds of one image.
    """
    where = "project_id = ?" if project_id else "1=1"
    params: list[object] = [project_id] if project_id else []
    if image:
        where += " AND image = ?"
        params.append(image)
    builds = conn.execute(
        f"""SELECT config_hash, variant, image FROM build_configs
            WHERE {where}
              AND (manifest_verification IS NULL OR manifest_verification != 'indexing')""",
        params,
    ).fetchall()

    groups: dict[str, dict] = {}
    unobserved: list[str] = []
    for b in builds:
        ch = b["config_hash"]
        label = b["variant"] or ""
        row = conn.execute(
            """SELECT usr FROM symbols
               WHERE config_hash=? AND name=?
               ORDER BY is_definition DESC, id LIMIT 1""",
            (ch, symbol),
        ).fetchone()
        if row is None:
            unobserved.append(label)
            continue
        fp = behavior_fingerprint(conn, ch, row["usr"])
        g = groups.setdefault(
            fp,
            {
                "fingerprint": fp,
                "variants": [],
                "config_hashes": [],
                "builds": [],
            },
        )
        g["variants"].append(label)
        g["config_hashes"].append(ch)
        g["builds"].append(
            {"variant": label, "image": b["image"] or "", "config_hash": ch}
        )

    ordered = sorted(
        groups.values(),
        key=lambda g: (-len(g["variants"]), min(g["config_hashes"]), g["fingerprint"]),
    )
    # Canonical member order within each group: by config_hash.  The SQL
    # above has no ORDER BY, so without this the member lists depend on the
    # plan the query planner picks (platform-dependent for the same data).
    for g in ordered:
        members = sorted(
            zip(g["variants"], g["config_hashes"], g["builds"]),
            key=lambda m: m[1],
        )
        g["variants"] = [m[0] for m in members]
        g["config_hashes"] = [m[1] for m in members]
        g["builds"] = [m[2] for m in members]
    return {
        "symbol": symbol,
        "build_count": len(builds),
        "groups": ordered,
        "unobserved": sorted(unobserved),
    }
