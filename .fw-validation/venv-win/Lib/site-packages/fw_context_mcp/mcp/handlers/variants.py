"""Variant comparison MCP tools (W6 / AUD-R5).

Two read-only tools over facts the index already stores — no schema
change, no new indexing pass:

- ``group_equivalent_variants`` — groups the project's builds by the
  OBSERVED BEHAVIOR of one symbol, wrapping the DB-layer implementation
  (``indexer/db/_variants.py``).  The fingerprint is the SAME helper the
  DB layer hashes with — one truth, no re-implementation.
- ``compare_variants`` — structural comparison of two indexed builds
  (variant, image) for one symbol, one file, or build-level facts.

NAMING HONESTY (W6 design rule 4): every comparison here is computed
from stored TEXT and index records — no compiler, no symbolic execution.
A result computed by textual/structural comparison is labeled
``method: "text"``.  The single fingerprint-based dimension is
``body_fingerprint`` (``method: "fingerprint"``): a SHA-256 over the
normalized signature, the stored IFDEF-FILTERED body tokens and the
sorted confirmed outgoing edges.  The word "semantic" appears only for
that fingerprint's behavior-equivalence role.

Fingerprint semantics worth stating twice: the body channel hashes the
stored body, which is ifdef-filtered PER BUILD.  Two builds that compile
different branches of the same source lines store different bodies and
hash DIFFERENTLY — an equality of fingerprints is an equivalence of
OBSERVED behavior, never a claim that the sources are identical.

Comparison dimensions (accepted design, seed list): existence,
signature, body fingerprint, outgoing edges (targets, kinds, certainty
from ``call_sites``), conditional regions (condition text + evaluated
branch states), and config dependencies (the ``-D`` defines every unit
of the build shares, per ``get_active_build.defines`` semantics).

Provenance (design rule 3): every fact carries the identity of the
build it was observed in — ``variant``, ``image`` and ``config_hash``.
The index identifies a build BY its ``config_hash`` (the primary key of
``build_configs``); there is no separate build id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from ..shared.context import _normalize_file_path_query
from ._base import BaseHandler

# Behavior edges — the same channel the fingerprint's third input uses
# (``_variants._BEHAVIOR_EDGE_KINDS``); derived variant-switch rows are
# excluded by the explicit kind list.
_BEHAVIOR_EDGE_KINDS = ("call", "indirect")

# Root directives that own an evaluated branch set.
_REGION_DIRECTIVES = ("if", "ifdef", "ifndef")

_FINGERPRINT_NOTE = (
    "SHA-256 over the normalized signature, the stored ifdef-filtered body "
    "tokens, and the sorted confirmed outgoing call edges of one "
    "(config_hash, usr) observation.  The body channel is branch-filtered "
    "per build: builds that compile different branches of the same source "
    "hash different bodies."
)


def _error(message: str) -> dict[str, Any]:
    return {"error": message}


def _resolve_build(conn, project_id: str, variant: str, image: str | None,
                   declared: list[str]):
    """One (variant[, image]) → build row, fail-closed (design rule 5).

    Returns ``(row, None)`` or ``(None, error_message)``.  A variant that
    holds several indexed images fails with an error that REQUIRES
    ``image``; an unknown or unindexed variant fails with an error that
    NAMES it and lists what exists.
    """
    rows = conn.execute(
        """SELECT config_hash, variant, image, compile_commands_path
           FROM build_configs
           WHERE project_id=? AND variant=?
             AND (manifest_verification IS NULL OR manifest_verification != 'indexing')
           ORDER BY created_at DESC, config_hash""",
        (project_id, variant),
    ).fetchall()
    if image:
        images = sorted({r["image"] or "(default)" for r in rows})
        rows = [r for r in rows if r["image"] == image]
        if not rows and images:
            return None, (
                f"Variant '{variant}' has no indexed build with image '{image}' "
                f"(has images: {', '.join(images)})."
            )
    if not rows:
        known = sorted(
            {
                r[0] or "(default)"
                for r in conn.execute(
                    "SELECT DISTINCT variant FROM build_configs WHERE project_id=?",
                    (project_id,),
                )
            }
            | set(declared)
        )
        hint = (
            f" Run 'fw-context index --build --variant {variant}' to index it."
            if variant in declared
            else f" Available variants: {', '.join(known)}."
        )
        return None, f"Variant '{variant}' is not indexed in this project.{hint}"
    if len(rows) > 1:
        images = sorted({r["image"] or "(default)" for r in rows})
        return None, (
            f"Variant '{variant}' has {len(rows)} indexed builds "
            f"(images: {', '.join(images)}); pass image to select one."
        )
    return rows[0], None


def _identity(row) -> dict[str, str]:
    return {
        "variant": row["variant"] or "",
        "image": row["image"] or "",
        "config_hash": row["config_hash"],
    }


def _symbol_row(conn, config_hash: str, name: str):
    """Definition-preferred row for a symbol in one build (same ordering
    convention as the fingerprint's signature/body channel)."""
    return conn.execute(
        """SELECT usr, name, kind, signature, is_definition, file_path, line
           FROM symbols WHERE config_hash=? AND name=?
           ORDER BY is_definition DESC, id LIMIT 1""",
        (config_hash, name),
    ).fetchone()


def _edges_of(conn, row, usr: str) -> list[dict[str, Any]]:
    """Confirmed outgoing edges of one symbol in one build, each with its
    target name, kind and the call-site certainty when one exists."""
    ch = row["config_hash"]
    out: list[dict[str, Any]] = []
    kinds = ", ".join("?" * len(_BEHAVIOR_EDGE_KINDS))
    for r in conn.execute(
        f"""SELECT DISTINCT to_usr, ref_kind FROM refs
            WHERE config_hash=? AND from_usr=? AND ref_kind IN ({kinds})
            ORDER BY to_usr, ref_kind""",
        (ch, usr, *_BEHAVIOR_EDGE_KINDS),
    ):
        name_row = conn.execute(
            "SELECT name FROM symbols WHERE config_hash=? AND usr=? LIMIT 1",
            (ch, r["to_usr"]),
        ).fetchone()
        certs = [
            x[0]
            for x in conn.execute(
                """SELECT DISTINCT certainty FROM call_sites
                   WHERE config_hash=? AND from_usr=? AND target_usr=? AND certainty != ''
                   ORDER BY certainty""",
                (ch, usr, r["to_usr"]),
            )
        ]
        out.append(
            {
                "to_usr": r["to_usr"],
                "name": name_row["name"] if name_row else r["to_usr"],
                "ref_kind": r["ref_kind"],
                "certainty": certs,
                **_identity(row),
            }
        )
    return out


def _regions_of(conn, config_hash: str, file_path: str) -> list[dict[str, Any]]:
    """Evaluated conditional regions of one file in one build.

    Rows are keyed per context (ADR-013); identical regions from several
    contexts of the same build collapse so the output is canonical.
    """
    frow = conn.execute(
        "SELECT id, path FROM files WHERE config_hash=? AND path=? LIMIT 1",
        (config_hash, file_path),
    ).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT id, path FROM files WHERE config_hash=? AND path LIKE ? "
            "ORDER BY length(path), path LIMIT 1",
            (config_hash, f"%{file_path}"),
        ).fetchone()
    if frow is None:
        return []
    regions: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    kind_ph = ", ".join("?" * len(_REGION_DIRECTIVES))
    roots = conn.execute(
        f"""SELECT id, line, directive, condition FROM conditionals
            WHERE config_hash=? AND file_id=? AND directive IN ({kind_ph})
            ORDER BY line, id""",
        (config_hash, frow["id"], *_REGION_DIRECTIVES),
    ).fetchall()
    for root in roots:
        branches = [
            {"kind": b["branch_kind"], "condition": b["condition"], "taken": b["taken"]}
            for b in conn.execute(
                """SELECT branch_kind, condition, taken FROM conditional_branches
                   WHERE conditional_id=? ORDER BY line, id""",
                (root["id"],),
            )
        ]
        key = (root["directive"], root["condition"], tuple(sorted(map(str, branches))))
        if key in seen:
            continue
        seen.add(key)
        regions.append(
            {
                "line": root["line"],
                "directive": root["directive"],
                "condition": root["condition"],
                "branches": branches,
            }
        )
    return regions


def _branch_key(region: dict[str, Any]) -> tuple:
    return tuple(
        (b["kind"], b["condition"], b["taken"]) for b in region["branches"]
    )


def _regions_delta(regions_a: list, regions_b: list) -> dict[str, Any]:
    """Condition text + evaluated branch states, per the seed dimension list."""
    by_key_a = {(r["directive"], r["condition"]): r for r in regions_a}
    by_key_b = {(r["directive"], r["condition"]): r for r in regions_b}
    differing: list[dict[str, Any]] = []
    for key in sorted(by_key_a.keys() & by_key_b.keys(), key=lambda k: (str(k[1]), k[0])):
        ra, rb = by_key_a[key], by_key_b[key]
        if _branch_key(ra) != _branch_key(rb):
            differing.append(
                {
                    "directive": ra["directive"],
                    "condition": ra["condition"],
                    "branches_a": ra["branches"],
                    "branches_b": rb["branches"],
                }
            )
    only_a = [r for r in regions_a if (r["directive"], r["condition"]) not in by_key_b]
    only_b = [r for r in regions_b if (r["directive"], r["condition"]) not in by_key_a]
    equal = not differing and not only_a and not only_b
    return {
        "equal": equal,
        "differing": differing,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }


def _defines_of(conn, row) -> dict[str, Any]:
    """The -D defines EVERY unit of the build shares (get_active_build
    .defines semantics — read from the build's own compile_commands.json)."""
    from ...indexer.compile_commands import command_line_defines

    defines, varying = command_line_defines(Path(row["compile_commands_path"]))
    return {"defines": defines, "varying": varying, **_identity(row)}


def _defines_delta(a: dict, b: dict) -> dict[str, Any]:
    da, db = a["defines"], b["defines"]
    differing = {
        name: {"a": da[name], "b": db[name]}
        for name in sorted(set(da) & set(db))
        if da[name] != db[name]
    }
    only_a = {name: da[name] for name in sorted(set(da) - set(db))}
    only_b = {name: db[name] for name in sorted(set(db) - set(da))}
    return {
        "equal": not differing and not only_a and not only_b,
        "differing": differing,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }


def _compare_symbol(conn, row_a, row_b, target: str) -> dict[str, Any]:
    from ...indexer.db import behavior_fingerprint

    ra = _symbol_row(conn, row_a["config_hash"], target)
    rb = _symbol_row(conn, row_b["config_hash"], target)

    def fact(row, extra: dict | None = None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = {**_identity(row_a if row is ra else row_b)}
        if extra:
            out.update(extra)
        return out

    existence = {
        "a": fact(ra, {"present": ra is not None}),
        "b": fact(rb, {"present": rb is not None}),
        "equal": (ra is None) == (rb is None),
    }
    signature = {
        "a": fact(ra, {"signature": ra["signature"] if ra else None}),
        "b": fact(rb, {"signature": rb["signature"] if rb else None}),
        "equal": (
            (ra["signature"] == rb["signature"])
            if (ra and rb)
            else (ra is None and rb is None)
        ),
    }
    if ra and rb:
        fa = behavior_fingerprint(conn, row_a["config_hash"], ra["usr"])
        fb = behavior_fingerprint(conn, row_b["config_hash"], rb["usr"])
        fingerprint = {
            "a": fact(ra, {"fingerprint": fa}),
            "b": fact(rb, {"fingerprint": fb}),
            "equal": fa == fb,
            "method": "fingerprint",
            "note": _FINGERPRINT_NOTE,
        }
        edges = {
            "a": _edges_of(conn, row_a, ra["usr"]),
            "b": _edges_of(conn, row_b, rb["usr"]),
        }
    else:
        fingerprint = {
            "a": fact(ra, {"fingerprint": None}),
            "b": fact(rb, {"fingerprint": None}),
            "equal": ra is None and rb is None,
            "method": "fingerprint",
            "note": _FINGERPRINT_NOTE,
        }
        edges = {"a": [], "b": []}
    keys_a = {(e["to_usr"], e["ref_kind"]) for e in edges["a"]}
    keys_b = {(e["to_usr"], e["ref_kind"]) for e in edges["b"]}
    edges["only_in_a"] = [e for e in edges["a"] if (e["to_usr"], e["ref_kind"]) not in keys_b]
    edges["only_in_b"] = [e for e in edges["b"] if (e["to_usr"], e["ref_kind"]) not in keys_a]
    edges["equal"] = not edges["only_in_a"] and not edges["only_in_b"]

    path_a = ra["file_path"] if ra else (rb["file_path"] if rb else None)
    conditions: dict[str, Any] | None = None
    if path_a:
        regions_a = _regions_of(conn, row_a["config_hash"], path_a)
        regions_b = _regions_of(conn, row_b["config_hash"], path_a)
        conditions = {
            "file": path_a,
            "a": [{**r, **_identity(row_a)} for r in regions_a],
            "b": [{**r, **_identity(row_b)} for r in regions_b],
            **_regions_delta(regions_a, regions_b),
        }

    return {
        "existence": existence,
        "signature": signature,
        "body_fingerprint": fingerprint,
        "edges": edges,
        **({"conditions": conditions} if conditions is not None else {}),
    }


def _compare_file(conn, row_a, row_b, target: str) -> dict[str, Any]:
    from ...indexer.db import behavior_fingerprint

    def resolve(row):
        q = _normalize_file_path_query(target)
        frow = conn.execute(
            "SELECT path FROM files WHERE config_hash=? AND path=? LIMIT 1",
            (row["config_hash"], q),
        ).fetchone()
        if frow is None:
            frow = conn.execute(
                "SELECT path FROM files WHERE config_hash=? AND path LIKE ? "
                "ORDER BY length(path), path LIMIT 1",
                (row["config_hash"], f"%{q}"),
            ).fetchone()
        return frow["path"] if frow else None

    path_a, path_b = resolve(row_a), resolve(row_b)
    if path_a is None and path_b is None:
        return _error(
            f"'{target}' matches no symbol and no indexed file in either build."
        )

    conditions = {
        "a": [{**r, **_identity(row_a)} for r in _regions_of(conn, row_a["config_hash"], path_a or target)],
        "b": [{**r, **_identity(row_b)} for r in _regions_of(conn, row_b["config_hash"], path_b or target)],
    }
    conditions.update(_regions_delta(conditions["a"], conditions["b"]))
    # provenance already merged above — keep the delta records annotated too
    for d in conditions["differing"]:
        d["variant_a"] = row_a["variant"] or ""
        d["variant_b"] = row_b["variant"] or ""

    def symbols_of(row, path):
        if path is None:
            return {}
        out: dict[str, dict] = {}
        for r in conn.execute(
            """SELECT usr, name, kind, signature, is_definition, line FROM symbols
               WHERE config_hash=? AND file_path=?
               ORDER BY name, is_definition DESC, id""",
            (row["config_hash"], path),
        ):
            if r["name"] in out:
                continue  # definition-preferred: first row per name wins
            out[r["name"]] = {
                "name": r["name"],
                "kind": r["kind"],
                "is_definition": bool(r["is_definition"]),
                "line": r["line"],
                "signature": r["signature"],
                "fingerprint": behavior_fingerprint(conn, row["config_hash"], r["usr"]),
                **_identity(row),
            }
        return out

    syms_a = symbols_of(row_a, path_a)
    syms_b = symbols_of(row_b, path_b)
    symbols: dict[str, dict] = {}
    for name in sorted(set(syms_a) | set(syms_b)):
        sa, sb = syms_a.get(name), syms_b.get(name)
        symbols[name] = {
            "a": sa,
            "b": sb,
            "fingerprint_equal": bool(sa and sb and sa["fingerprint"] == sb["fingerprint"]),
        }
    differing = sorted(
        name for name, rec in symbols.items() if rec["a"] and rec["b"] and not rec["fingerprint_equal"]
    )
    summary = {
        "symbols_only_in_a": sorted(set(syms_a) - set(syms_b)),
        "symbols_only_in_b": sorted(set(syms_b) - set(syms_a)),
        "fingerprints_differing": differing,
        "fingerprints_equal_count": sum(1 for rec in symbols.values() if rec["fingerprint_equal"]),
    }
    return {
        "path": {"a": path_a, "b": path_b},
        "conditions": conditions,
        "symbols": symbols,
        "summary": summary,
    }


def _compare_build_level(conn, row_a, row_b) -> dict[str, Any]:
    kind_ph = ", ".join("?" * len(_REGION_DIRECTIVES))

    def counts(row):
        n_symbols = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE config_hash=?", (row["config_hash"],)
        ).fetchone()[0]
        n_files = conn.execute(
            "SELECT COUNT(DISTINCT path) FROM files WHERE config_hash=?", (row["config_hash"],)
        ).fetchone()[0]
        n_regions = conn.execute(
            f"""SELECT COUNT(*) FROM conditionals
                WHERE config_hash=? AND directive IN ({kind_ph})""",
            (row["config_hash"], *_REGION_DIRECTIVES),
        ).fetchone()[0]
        return {"symbols": n_symbols, "files": n_files, "conditional_regions": n_regions, **_identity(row)}

    da, db_ = _defines_of(conn, row_a), _defines_of(conn, row_b)
    return {
        "counts": {"a": counts(row_a), "b": counts(row_b)},
        "defines": {
            "a": da,
            "b": db_,
            **_defines_delta(da, db_),
        },
    }


def compare_variants(
    a: Annotated[str, Field(description="First variant label (see list_variants).")],
    b: Annotated[str, Field(description="Second variant label.")],
    symbol_or_file: Annotated[str | None, Field(description="Symbol name, or a file path "
        "present in the index, to compare. Omitted: build-level comparison "
        "(defines, symbol/file/conditional-region counts) only.")] = None,
    image: Annotated[str | None, Field(description="Image name — required when the variants "
        "have more than one indexed image (see get_active_build.variant_images). "
        "Applies to BOTH sides.")] = None,
    project_root: Annotated[str | None, Field(description="Project root. "
        "Auto-detected if omitted.")] = None,
) -> dict[str, Any]:
    """Compare two indexed builds (variant, image) structurally.

    Structural comparison of two indexed builds for a symbol, a file, or
    build-level facts.  Dimensions (accepted design W6): ``existence``,
    ``signature``, ``body_fingerprint`` (fingerprint-based — see the
    ``note`` in the result), outgoing ``edges`` (targets, kinds, call-site
    certainty), ``conditions`` (condition text + evaluated branch states)
    and ``defines`` (the ``-D`` flags every unit of each build shares, per
    ``get_active_build.defines`` semantics).  Every fact carries the
    provenance of its build: ``variant``, ``image``, ``config_hash``.

    All dimensions are compared TEXTUALLY/structurally over stored index
    records — the result is labeled ``method: "text"``; only
    ``body_fingerprint`` is fingerprint-based (``method: "fingerprint"``).
    No compiler or semantic analysis runs here: two builds whose stored
    bodies differ (e.g. different branches of one ``#ifdef`` compile)
    fingerprint DIFFERENTLY even when the source lines are identical.

    ``symbol_or_file`` resolves as a symbol name first, then as an indexed
    file path (suffix match).  File mode compares the file's conditional
    regions plus a per-symbol rollup (existence, signature, fingerprint).
    Errors name the problem: an unindexed variant, a multi-image variant
    without ``image``, or a target matching nothing.

    Args:
        a: First variant label (see list_variants).
        b: Second variant label.
        symbol_or_file: Symbol name, or a file path present in the index,
            to compare. Omitted: build-level comparison (defines,
            symbol/file/conditional-region counts) only.
        image: Image name — required when the variants have more than one
            indexed image (see get_active_build.variant_images). Applies
            to BOTH sides.
        project_root: Project root. Auto-detected if omitted.

    Returns:
        dict: {kind ("symbol"|"file"|"build"), target, method ("text"),
        a/b (each {variant, image, config_hash}), and mode-dependent keys}.

        Symbol mode adds ``dimensions``: {existence (a/b ``present`` +
        ``equal``), signature (a/b ``signature`` + ``equal``),
        body_fingerprint (a/b ``fingerprint``, ``equal``, ``method``:
        "fingerprint", ``note``), edges (a/b lists of {to_usr, name,
        ref_kind, certainty (list, possibly []), variant, image,
        config_hash}, plus only_in_a/only_in_b/equal), conditions (when a
        source file is known: file, a/b region lists of {line, directive,
        condition, branches [{kind, condition, taken}], variant, image,
        config_hash}, plus differing ({directive, condition, branches_a,
        branches_b})/only_in_a/only_in_b/equal), defines (a/b {defines,
        varying, variant, image, config_hash} plus differing ({name: {a,
        b}})/only_in_a/only_in_b/equal)}.

        File mode adds path ({a, b} resolved paths), conditions (same
        shape, top level), symbols ({name: {a, b (each with kind, line,
        signature, fingerprint + provenance; null when absent),
        fingerprint_equal}}) and summary ({symbols_only_in_a,
        symbols_only_in_b, fingerprints_differing, fingerprints_equal_count}),
        plus defines.

        Build level adds counts (a/b {symbols, files, conditional_regions}
        + provenance) and defines.

        On failure the dict holds only ``error`` with the reason.
    """
    try:
        # variant="*" resolves EVERY build scope — this tool addresses its
        # two builds through the a/b labels itself, so the per-tool variant
        # gate must not pre-select one build (§5.7 stays fail-closed for
        # every tool that scopes by variant instead).
        db = BaseHandler.resolve_db_context(project_root, variant="*")
    except RuntimeError as exc:
        return _error(str(exc))

    def _query(conn, config_hash: str) -> dict[str, Any]:
        del config_hash  # this tool addresses builds BY VARIANT, not by the
        # active config; the executor's per-call hash is irrelevant here.
        declared = [v.name for v in db.cfg.build.variants]
        row_a, err_a = _resolve_build(conn, db.project_id, a, image, declared)
        if err_a:
            return _error(err_a)
        row_b, err_b = _resolve_build(conn, db.project_id, b, image, declared)
        if err_b:
            return _error(err_b)
        result: dict[str, Any] = {
            "kind": "build",
            "target": symbol_or_file,
            "method": "text",
            "a": _identity(row_a),
            "b": _identity(row_b),
        }
        if symbol_or_file:
            if _symbol_row(conn, row_a["config_hash"], symbol_or_file) or _symbol_row(
                conn, row_b["config_hash"], symbol_or_file
            ):
                result["kind"] = "symbol"
                result["target"] = symbol_or_file
                dims = _compare_symbol(conn, row_a, row_b, symbol_or_file)
                # config dependencies belong to the BUILDS, not the symbol —
                # but the design's dimension list applies to every mode, and
                # for A-vs-C-style pairs it is the only differing dimension.
                da, db_ = _defines_of(conn, row_a), _defines_of(conn, row_b)
                dims["defines"] = {"a": da, "b": db_, **_defines_delta(da, db_)}
                result["dimensions"] = dims
            else:
                result["kind"] = "file"
                result["target"] = symbol_or_file
                file_out = _compare_file(conn, row_a, row_b, symbol_or_file)
                if "error" in file_out:
                    result.update(file_out)
                else:
                    da, db_ = _defines_of(conn, row_a), _defines_of(conn, row_b)
                    file_out["defines"] = {
                        "a": da, "b": db_, **_defines_delta(da, db_)
                    }
                    result.update(file_out)
        else:
            result.update(_compare_build_level(conn, row_a, row_b))
        return result

    try:
        return db.executor.execute_sync(_query, db.config_hash)
    except Exception as exc:  # noqa: BLE001 — the error boundary of the tool
        return _error(f"{type(exc).__name__}: {exc}")


def group_equivalent_variants(
    symbol: Annotated[str, Field(description="Symbol name to group the builds by.")],
    image: Annotated[str | None, Field(description="Only group builds of this image. "
        "Omit for all images.")] = None,
    project_root: Annotated[str | None, Field(description="Project root. "
        "Auto-detected if omitted.")] = None,
) -> dict[str, Any]:
    """Group the project's builds by the observed behavior of one symbol.

    Which builds see the same behavior for *symbol*: builds whose stored
    facts for it agree land in the same group, regardless of how many
    variant labels they carry.  The grouping wraps the DB-layer
    implementation (``indexer/db/_variants.py``) — the fingerprint is a
    SHA-256 over the normalized signature, the stored ifdef-filtered body
    tokens and the sorted confirmed outgoing call edges of one
    (config_hash, usr) observation.  It is an EQUALITY KEY ONLY (A04):
    never a certainty claim, and never evidence that any build is correct.

    Groups are ordered by descending size, then smallest config_hash;
    members within a group by config_hash.  Every member build carries its
    provenance tuple ``variant``/``image``/``config_hash`` (the build IS
    its config_hash — the index has no separate build id).  Builds with no
    observation of the symbol are listed in ``unobserved`` so "equivalent"
    stays distinguishable from "not compiled here".

    Args:
        symbol: Symbol name to group the builds by.
        image: Only group builds of this image. Omit for all images.
        project_root: Project root. Auto-detected if omitted.

    Returns:
        dict: {symbol, build_count (the builds the grouping spans),
        groups (list of {fingerprint, variants (list of variant labels),
        config_hashes (list, same order), builds (list of {variant,
        image, config_hash} — the full provenance of each member)}),
        unobserved (sorted variant labels with no observation of the
        symbol)}.  ``variants``/``config_hashes``/``builds`` are parallel
        lists ordered by config_hash.

        On failure the dict holds only ``error`` with the reason.
    """
    try:
        # variant="*": grouping spans EVERY completed build of the project
        # (the DB implementation's own scope) — no per-tool variant gate.
        db = BaseHandler.resolve_db_context(project_root, variant="*")
    except RuntimeError as exc:
        return _error(str(exc))

    def _query(conn, config_hash: str) -> dict[str, Any]:
        from ...indexer.db import group_equivalent_variants as _group_db

        del config_hash  # grouping spans EVERY completed build of the project
        return _group_db(conn, symbol, project_id=db.project_id, image=image or "")

    try:
        return db.executor.execute_sync(_query, db.config_hash)
    except Exception as exc:  # noqa: BLE001 — the error boundary of the tool
        return _error(f"{type(exc).__name__}: {exc}")
