"""T14 SLICE-C oracle: context-aware storage (T02, ADR-001/002/005).

Frozen cases (TEST_PLAN §3, T02::test_spec.yaml):

RED (fail pre-fix, one documented reason each):
- T02::t0_symbol_def_def_collision_per_context — two definitions of one USR
  under two contexts must both survive; the definition guard must still block
  a second definition WITHIN one context (RC1, ``_symbols.py`` first-def-wins).
- T02::t0_refs_identical_from_two_contexts_keep_provenance — one ref tuple
  observed under two contexts must keep both rows (RC2, ``idx_refs_unique``).
- T02::t0_macros_same_line_two_values_do_not_overwrite_across_contexts — the
  same (file, line) macro under two -D values must not overwrite (RC2).
- T02::t1_f01_multi_tu_header_both_contexts_stored — F01: a header function
  seen from two TUs (-DMODE_VALUE=11 / =22) stores one observation per
  context, each body ifdef-filtered to ITS context.
- T02::t1_f01_refs_callee_body_matches_caller_context — the call edge into
  the header callee carries the caller TU's context.
- T02::t1_f02_same_source_two_commands_both_survive — F02: one source file
  listed twice with different -D stores both commands' symbols (RC2
  last-writer-wins: today the second TU's subtree is skipped and the first
  writer's rows survive on the canonical key).
- T02::t1_ctx_edges_per_context_call_targets_retained — CTX_edges: the
  cross-context call edges (dispatch→mode_a under ctx A, dispatch→mode_b
  under ctx B) are BOTH retained (RC3: today the second TU skips the claimed
  header and its edge never exists).
- T02::t1_command_line_defines_stored_per_context — build_contexts rows carry
  the per-context identity (argv/defines) from ADR-001; today only the
  canonical seed row exists.

GREEN (pass post-fix):
- T02::t2_incremental_full_eq_per_context — F01: mutate common.hpp, the
  incremental re-run's per-context snapshots equal a clean rebuild (G1).
- T02::t2_order_independence_f02 — F02 indexed in both entry orders yields
  the same canonical snapshot sorted by context_id.
- regression_decl_def_promotion_pinned — decl→def promotion inside one
  context still works (the ADR-002 guard, canonical-context form).
- t2_canonical_projection_* — the ADR-002 canonical projection query layer:
  collapses per-(config,usr) observations to the canonical symbol, and
  degenerates to the stored rows on a single-context DB.
- t2_context_identity_helper_contract — the ADR-001 helper: 16-hex identity,
  stable serialization, every formula component moves it.

Scratch policy: every DB and fixture lives under pytest tmp_path (a %TEMP%
subdir, disclosed in the receipt).  The repo-root .fw-context DB is never
touched; no production source is modified by these tests.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import (
    insert_macros_batch,
    insert_refs_batch,
    insert_symbols_batch,
    open_db,
    transaction,
    upsert_build_config,
    upsert_file,
    upsert_project,
)
from fw_context_mcp.indexer.runner import run


# ── shared fixture writers ───────────────────────────────────────────────────


def _write_cc(root: Path, entries: list[dict]) -> Path:
    """Write compile_commands.json; each entry is {file, args, output}."""
    fwd = str(root).replace("\\", "/")
    payload = []
    for i, e in enumerate(entries):
        payload.append(
            {
                "directory": fwd,
                "file": e["file"],
                "arguments": ["gcc", *e["args"], "-c", e["file"], "-o", e.get("output", f"build/o{i}.o")],
            }
        )
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cc


def _run_index(root: Path, cc: Path, db_path: Path, pid: str) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return run(
        cc,
        db_path,
        project_root=root,
        project_id=pid,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
    )


def _f01(root: Path, order: tuple[int, int] = (0, 1)) -> Path:
    """F01_multi_tu_header: master.c + slave.c both include common.hpp.

    role_value() is defined in the shared header; its body is ifdef-filtered
    per context: -DMODE_VALUE=11 stores "return 11;", =22 stores "return 22;".
    """
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "common.hpp").write_text(
        "#ifndef COMMON_HPP\n"
        "#define COMMON_HPP\n"
        "int role_value(void) {\n"
        "#if MODE_VALUE == 11\n"
        "    return 11;\n"
        "#else\n"
        "    return 22;\n"
        "#endif\n"
        "}\n"
        "int side_helper(void);\n"
        "#endif\n",
        encoding="utf-8",
    )
    (src / "master.c").write_text(
        '#include "common.hpp"\n'
        "int master_entry(void) {\n"
        "    return role_value();\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "slave.c").write_text(
        '#include "common.hpp"\n'
        "int slave_entry(void) {\n"
        "    return role_value();\n"
        "}\n",
        encoding="utf-8",
    )
    tu = [("src/master.c", ["-std=c11", "-DMODE_VALUE=11", "-Isrc"], "build/master.o"),
          ("src/slave.c", ["-std=c11", "-DMODE_VALUE=22", "-Isrc"], "build/slave.o")]
    ordered = [tu[i] for i in order]
    return _write_cc(root, [
        {"file": f, "args": a, "output": o} for f, a, o in ordered
    ])


def _f02(root: Path, order: tuple[int, int] = (0, 1)) -> Path:
    """F02_same_source_multiple_commands: same.c listed twice, -D differs."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "same.c").write_text(
        "int mode_fn(void) {\n"
        "#if MODE_VALUE == 1\n"
        "    return 1;\n"
        "#else\n"
        "    return 2;\n"
        "#endif\n"
        "}\n",
        encoding="utf-8",
    )
    # Output names derive from the compile ACTION, not the entry position:
    # ADR-001 makes output_name an identity component, so a positional
    # output name would mint different context ids under entry reordering.
    tu = [("src/same.c", ["-std=c11", "-DMODE_VALUE=1", "-Isrc"], "build/mode1.o"),
          ("src/same.c", ["-std=c11", "-DMODE_VALUE=2", "-Isrc"], "build/mode2.o")]
    ordered = [tu[i] for i in order]
    return _write_cc(root, [
        {"file": f, "args": a, "output": o} for f, a, o in ordered
    ])


def _ctx(root: Path) -> Path:
    """CTX_edges: shared header dispatch() calls mode_a/mode_b per context."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "ctx.hpp").write_text(
        "#ifndef CTX_HPP\n"
        "#define CTX_HPP\n"
        "void mode_a(void);\n"
        "void mode_b(void);\n"
        "void dispatch(void) {\n"
        "#if MODE == 1\n"
        "    mode_a();\n"
        "#else\n"
        "    mode_b();\n"
        "#endif\n"
        "}\n"
        "#endif\n",
        encoding="utf-8",
    )
    (src / "tu_a.c").write_text(
        '#include "ctx.hpp"\n'
        "int run_a(void) {\n"
        "    dispatch();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "tu_b.c").write_text(
        '#include "ctx.hpp"\n'
        "int run_b(void) {\n"
        "    dispatch();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    return _write_cc(root, [
        {"file": "src/tu_a.c", "args": ["-std=c11", "-DMODE=1", "-Isrc"], "output": "build/tu_a.o"},
        {"file": "src/tu_b.c", "args": ["-std=c11", "-DMODE=2", "-Isrc"], "output": "build/tu_b.o"},
    ])


# ── shared DB helpers ────────────────────────────────────────────────────────


def _scratch_db(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = open_db(tmp_path / name)
    with transaction(conn):
        upsert_project(conn, "pid-slicec", "slicec", str(tmp_path))
        upsert_build_config(conn, "ch-slicec", "pid-slicec", str(tmp_path / "cc.json"))
    return conn


def _sym_row(config_hash: str, file_id: int, usr: str, source: str,
             is_definition: int = 1, line: int = 4) -> tuple:
    """A 24-column symbol row in the current writer shape (no context)."""
    return (
        config_hash, file_id, "a.c", "u s r", usr, "f", "f", "function",
        line, 1, line + 2, is_definition, "int f(void)", "", None, 0, 0,
        "", 0, "", 0, 0.0, source, 0,
    )


def _usr_of(conn: sqlite3.Connection, config_hash: str, name: str) -> str:
    row = conn.execute(
        "SELECT DISTINCT usr FROM symbols WHERE config_hash=? AND name=?",
        (config_hash, name),
    ).fetchone()
    assert row is not None, f"symbol {name!r} not indexed"
    return row[0]


# ── RED: T0 storage primitives ───────────────────────────────────────────────


class TestContextAwareStorageRed:
    """The eight frozen RED cases (TEST_PLAN §3)."""

    def test_t0_symbol_def_def_collision_per_context(self, tmp_path: Path):
        """RC1: same USR defined in two contexts keeps both bodies.

        expected_pre: rows=1, surviving body belongs to the FIRST insert
        (rowcount of the second insert == 0) — the writer cannot carry a
        context, so both rows collide on (config_hash, usr, '').
        expected_post: rows=2 with per-context bodies; a second definition
        WITHIN one context stays blocked by the ADR-002 guard.
        """
        conn = _scratch_db(tmp_path, "t0_sym.db")
        try:
            fid_a = upsert_file(conn, "ch-slicec", "a.c", "c")
            fid_b = upsert_file(conn, "ch-slicec", "b.c", "c")
            with transaction(conn):
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_a, "u:f", "return 11;")],
                    context_id="ctxAAA",
                )
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_b, "u:f", "return 22;")],
                    context_id="ctxBBB",
                )
            rows = conn.execute(
                "SELECT source, context_id, file_id FROM symbols "
                "WHERE config_hash='ch-slicec' AND usr='u:f' ORDER BY context_id"
            ).fetchall()
            assert [(r["source"], r["context_id"]) for r in rows] == [
                ("return 11;", "ctxAAA"),
                ("return 22;", "ctxBBB"),
            ], (
                "two definitions of one USR must survive, one per context "
                "(T02::t0_symbol_def_def_collision_per_context POST oracle)"
            )
            # the ADR-002 definition guard still blocks WITHIN one context
            with transaction(conn):
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_a, "u:f", "return 33;")],
                    context_id="ctxAAA",
                )
            rows_a = conn.execute(
                "SELECT source FROM symbols WHERE config_hash='ch-slicec' "
                "AND usr='u:f' AND context_id='ctxAAA'"
            ).fetchall()
            assert [r["source"] for r in rows_a] == ["return 11;"], (
                "a second definition inside the SAME context must stay blocked "
                "by the definition guard"
            )
        finally:
            conn.close()

    def test_t0_refs_identical_from_two_contexts_keep_provenance(self, tmp_path: Path):
        """RC2: one ref tuple observed under two contexts keeps both rows.

        expected_pre: 1 row — the context dimension does not exist, so
        idx_refs_unique collapses the second observation.
        expected_post: 2 rows, identical provenance tuple, distinct contexts.
        """
        conn = _scratch_db(tmp_path, "t0_refs.db")
        try:
            base = ("ch-slicec", "u:callee", "a.c", 9, "u:caller", "call", None)
            with transaction(conn):
                insert_refs_batch(conn, [base], context_id="ctxAAA")
                insert_refs_batch(conn, [base], context_id="ctxBBB")
            rows = conn.execute(
                "SELECT context_id FROM refs WHERE config_hash='ch-slicec' "
                "AND to_usr='u:callee' ORDER BY context_id"
            ).fetchall()
            assert [r["context_id"] for r in rows] == ["ctxAAA", "ctxBBB"], (
                "identical refs from two contexts must both survive "
                "(idx_refs_unique includes context_id)"
            )
        finally:
            conn.close()

    def test_t0_macros_same_line_two_values_do_not_overwrite_across_contexts(
        self, tmp_path: Path
    ):
        """RC2: the same macro line under two -D values keeps both values."""
        conn = _scratch_db(tmp_path, "t0_mac.db")
        try:
            fid = upsert_file(conn, "ch-slicec", "a.c", "c")
            row = ("ch-slicec", fid, "MODE_VALUE", "11", "11", 3, 0)
            with transaction(conn):
                insert_macros_batch(conn, [row], context_id="ctxAAA")
                insert_macros_batch(
                    conn,
                    [("ch-slicec", fid, "MODE_VALUE", "22", "22", 3, 0)],
                    context_id="ctxBBB",
                )
            rows = conn.execute(
                "SELECT value, context_id FROM macros WHERE config_hash='ch-slicec' "
                "AND name='MODE_VALUE' ORDER BY context_id"
            ).fetchall()
            assert [(r["value"], r["context_id"]) for r in rows] == [
                ("11", "ctxAAA"),
                ("22", "ctxBBB"),
            ], "the same (file, line) macro under two contexts must not overwrite"
        finally:
            conn.close()

    # ── RED: T1 synthetic-compiler cases ────────────────────────────────

    def test_t1_f01_multi_tu_header_both_contexts_stored(self, tmp_path: Path):
        """F01: header function observed from two TUs keeps both bodies."""
        root = tmp_path / "f01"
        root.mkdir()
        cc = _f01(root)
        config_hash = _run_index(root, cc, tmp_path / "f01" / "index.db", "slicec-f01")
        conn = open_db(tmp_path / "f01" / "index.db")
        try:
            usr = _usr_of(conn, config_hash, "role_value")
            rows = conn.execute(
                "SELECT source, context_id FROM symbols "
                "WHERE config_hash=? AND usr=? ORDER BY context_id",
                (config_hash, usr),
            ).fetchall()
            assert len(rows) == 2, (
                f"role_value must be stored once per context, got {len(rows)} row(s)"
            )
            bodies = {r["context_id"]: r["source"] for r in rows}
            assert len(bodies) == 2 and all(bodies), (
                "observations must carry two distinct, real (non-canonical) context_ids"
            )
            assert any(
                "return 11;" in s and "return 22;" not in s for s in bodies.values()
            ) and any(
                "return 22;" in s and "return 11;" not in s for s in bodies.values()
            ), (
                "each context keeps its own ifdef-filtered body: the branch the "
                "other context took is blanked in this context's stored text"
            )
            assert all(r["context_id"] for r in rows), (
                "observations must carry a real (non-canonical) context_id"
            )
            n_ctx = conn.execute(
                "SELECT COUNT(*) FROM build_contexts WHERE config_hash=? AND context_id != ''",
                (config_hash,),
            ).fetchone()[0]
            assert n_ctx >= 2, "both compile actions must register in build_contexts"
        finally:
            conn.close()

    def test_t1_f01_refs_callee_body_matches_caller_context(self, tmp_path: Path):
        """F01: the call edge into the header callee carries the caller's context."""
        root = tmp_path / "f01r"
        root.mkdir()
        cc = _f01(root)
        config_hash = _run_index(root, cc, tmp_path / "f01r" / "index.db", "slicec-f01r")
        conn = open_db(tmp_path / "f01r" / "index.db")
        try:
            usr = _usr_of(conn, config_hash, "role_value")
            rows = conn.execute(
                "SELECT r.context_id AS ctx, r.from_file AS ff "
                "FROM refs r WHERE r.config_hash=? AND r.to_usr=? AND r.ref_kind='call' "
                "ORDER BY r.context_id",
                (config_hash, usr),
            ).fetchall()
            by_file = {r["ff"].replace("\\", "/"): r["ctx"] for r in rows}
            assert len(rows) == 2, (
                f"both TUs' call edges into role_value must exist, got {len(rows)}"
            )
            ctxs = sorted(r["ctx"] for r in rows)
            assert all(ctxs) and len(set(ctxs)) == 2, (
                "each edge must carry a distinct, real context (RC3 provenance)"
            )
            # caller-context pairing: master's edge and slave's edge differ
            assert by_file.get("src/master.c") != by_file.get("src/slave.c"), (
                "the edge from master.c and the edge from slave.c belong to "
                "different compile contexts"
            )
        finally:
            conn.close()

    def test_t1_f02_same_source_two_commands_both_survive(self, tmp_path: Path):
        """F02: one source file, two commands — both survive (RC2 last-writer-wins)."""
        root = tmp_path / "f02"
        root.mkdir()
        cc = _f02(root)
        config_hash = _run_index(root, cc, tmp_path / "f02" / "index.db", "slicec-f02")
        conn = open_db(tmp_path / "f02" / "index.db")
        try:
            usr = _usr_of(conn, config_hash, "mode_fn")
            rows = conn.execute(
                "SELECT source, context_id FROM symbols "
                "WHERE config_hash=? AND usr=? ORDER BY context_id",
                (config_hash, usr),
            ).fetchall()
            assert [r["source"] for r in rows] and len(rows) == 2, (
                "both commands' observations of mode_fn must survive; today the "
                "second TU's subtree is skipped (skip_files) and the first "
                "writer's rows win on the canonical key"
            )
            bodies = {r["context_id"]: r["source"] for r in rows}
            assert len(bodies) == 2 and all(bodies)
            assert any(
                "return 1;" in s and "return 2;" not in s for s in bodies.values()
            ) and any(
                "return 2;" in s and "return 1;" not in s for s in bodies.values()
            ), "each context keeps its own ifdef-filtered body of mode_fn"
            file_rows = conn.execute(
                "SELECT path, flags_hash, context_id FROM files "
                "WHERE config_hash=? ORDER BY context_id",
                (config_hash,),
            ).fetchall()
            # one row per (path, context) for the twice-compiled source
            # (path spelling normalized — the indexer stores OS separators)
            same_rows = [
                r for r in file_rows
                if r["path"].replace("\\", "/") == "src/same.c"
            ]
            assert len(same_rows) == 2, (
                f"files keeps one row per (path, context), got {len(same_rows)}"
            )
            flags = [r["flags_hash"] for r in same_rows]
            assert len(set(flags)) == 2, (
                "each context records its own flags"
            )
        finally:
            conn.close()

    def test_t1_ctx_edges_per_context_call_targets_retained(self, tmp_path: Path):
        """CTX_edges: cross-context call edges are both retained (RC3)."""
        root = tmp_path / "ctx"
        root.mkdir()
        cc = _ctx(root)
        config_hash = _run_index(root, cc, tmp_path / "ctx" / "index.db", "slicec-ctx")
        conn = open_db(tmp_path / "ctx" / "index.db")
        try:
            usr_a = _usr_of(conn, config_hash, "mode_a")
            usr_b = _usr_of(conn, config_hash, "mode_b")
            edge_a = conn.execute(
                "SELECT context_id FROM refs WHERE config_hash=? AND to_usr=? "
                "AND ref_kind='call'",
                (config_hash, usr_a),
            ).fetchall()
            edge_b = conn.execute(
                "SELECT context_id FROM refs WHERE config_hash=? AND to_usr=? "
                "AND ref_kind='call'",
                (config_hash, usr_b),
            ).fetchall()
            assert edge_a, "dispatch→mode_a edge (ctx MODE=1) must exist"
            assert edge_b, (
                "dispatch→mode_b edge (ctx MODE=2) must exist; today tu_b skips "
                "the header claimed by tu_a and the edge is never walked (RC3)"
            )
            ctx_a = {r["context_id"] for r in edge_a}
            ctx_b = {r["context_id"] for r in edge_b}
            assert ctx_a and ctx_b and not (ctx_a & ctx_b), (
                "the two edges belong to different contexts"
            )
        finally:
            conn.close()

    def test_t1_command_line_defines_stored_per_context(self, tmp_path: Path):
        """build_contexts carries the ADR-001 identity of every compile action."""
        root = tmp_path / "defc"
        root.mkdir()
        cc = _f02(root)
        config_hash = _run_index(root, cc, tmp_path / "defc" / "index.db", "slicec-defc")
        conn = open_db(tmp_path / "defc" / "index.db")
        try:
            rows = conn.execute(
                "SELECT context_id, entry_index, main_file, canonical_argv, "
                "defines_json, flags_hash, output_name "
                "FROM build_contexts WHERE config_hash=? AND context_id != '' "
                "ORDER BY entry_index",
                (config_hash,),
            ).fetchall()
            assert len(rows) == 2, (
                f"two compile actions must register two contexts, got {len(rows)}"
            )
            by_entry = {r["entry_index"]: r for r in rows}
            assert any("-DMODE_VALUE=1" in (r["defines_json"] or "") for r in rows), (
                "defines_json must record the command-line defines of the context"
            )
            assert any("-DMODE_VALUE=2" in (r["defines_json"] or "") for r in rows)
            assert all(r["main_file"].endswith("same.c") for r in rows)
            assert all(r["flags_hash"] for r in rows), "flags_hash recorded per context"
            assert all(len(r["context_id"]) == 16 for r in rows), (
                "ADR-001: context_id is the truncated 16-hex identity hash"
            )
            assert by_entry[0]["context_id"] != by_entry[1]["context_id"], (
                "two entries with different -D are two contexts"
            )
        finally:
            conn.close()


# ── GREEN: differential + regression cases ───────────────────────────────────


def _snap(conn: sqlite3.Connection, config_hash: str) -> dict[str, list[tuple]]:
    """Deterministic per-context snapshot of every storage surface."""
    symbols = conn.execute(
        "SELECT usr, context_id, is_definition, source, line, file_path "
        "FROM symbols WHERE config_hash=? ORDER BY usr, context_id, line, file_path",
        (config_hash,),
    ).fetchall()
    refs = conn.execute(
        "SELECT to_usr, from_file, from_line, from_usr, ref_kind, context_id "
        "FROM refs WHERE config_hash=? "
        "ORDER BY to_usr, from_file, from_line, ref_kind, context_id",
        (config_hash,),
    ).fetchall()
    files = conn.execute(
        "SELECT path, context_id, source_hash, flags_hash, content "
        "FROM files WHERE config_hash=? ORDER BY path, context_id",
        (config_hash,),
    ).fetchall()
    contexts = conn.execute(
        "SELECT context_id, main_file, canonical_argv, "
        "response_hashes, output_name, defines_json, flags_hash "
        "FROM build_contexts WHERE config_hash=? ORDER BY context_id, entry_index",
        (config_hash,),
    ).fetchall()
    # entry_index is deliberately NOT part of the snapshot: it records the
    # position the context held in THIS run's entry list — bookkeeping, not
    # an identity component (ADR-001 has no positional term).  Reordering
    # compile_commands.json legitimately swaps it while every observation,
    # identity and body stays identical.
    macros = conn.execute(
        "SELECT name, value, line, context_id FROM macros WHERE config_hash=? "
        "ORDER BY name, line, context_id",
        (config_hash,),
    ).fetchall()
    keys = ("symbols", "refs", "files", "contexts", "macros")
    vals = (symbols, refs, files, contexts, macros)
    return {k: [tuple(r) for r in v] for k, v in zip(keys, vals)}


class TestContextAwareStorageGreen:
    """GREEN + regression cases (TEST_PLAN §3)."""

    def test_t2_incremental_full_eq_per_context(self, tmp_path: Path):
        """G1 per-context: mutate common.hpp — incremental == clean rebuild."""
        root = tmp_path / "g1"
        root.mkdir()
        cc = _f01(root)
        inc_db = tmp_path / "g1" / "inc" / "index.db"
        config_hash = _run_index(root, cc, inc_db, "slicec-g1")

        # mutate the shared header (both contexts see the change)
        (root / "src" / "common.hpp").write_text(
            "#ifndef COMMON_HPP\n"
            "#define COMMON_HPP\n"
            "int role_value(void) {\n"
            "#if MODE_VALUE == 11\n"
            "    return 11;\n"
            "#else\n"
            "    return 22;\n"
            "#endif\n"
            "}\n"
            "int side_helper(void);\n"
            "int added_later(void) { return 3; }\n"
            "#endif\n",
            encoding="utf-8",
        )
        _run_index(root, cc, inc_db, "slicec-g1")  # incremental re-run

        full_db = tmp_path / "g1" / "full" / "index.db"
        _run_index(root, cc, full_db, "slicec-g1-full")  # clean rebuild

        conn_inc = open_db(inc_db)
        conn_full = open_db(full_db)
        try:
            snap_inc = _snap(conn_inc, config_hash)
            snap_full = _snap(conn_full, config_hash)
            for surface in snap_inc:
                assert snap_inc[surface] == snap_full[surface], (
                    f"incremental {surface} must equal a clean rebuild after the "
                    "header edit, per context (G1 O_INCREMENTAL_FULL_EQ)"
                )
            # files.content_hash is excluded by design: it embeds the manifest
            # entry hash, which legitimately carries the pre-edit header
            # evidence on an incremental run (T01 refresh contract, SLICE-D).
            # The canonical answer surfaces above must match exactly.
            # Per-context oracle presence on the clean rebuild:
            usr = _usr_of(conn_full, config_hash, "role_value")
            rows = conn_full.execute(
                "SELECT source, context_id FROM symbols "
                "WHERE config_hash=? AND usr=? ORDER BY context_id",
                (config_hash, usr),
            ).fetchall()
            assert len(rows) == 2 and any(
                "return 11;" in r["source"] for r in rows
            ) and any("return 22;" in r["source"] for r in rows), (
                "the rebuilt index must hold one role_value observation per context"
            )
        finally:
            conn_inc.close()
            conn_full.close()

    @pytest.mark.parametrize("first", [0, 1])
    def test_t2_order_independence_f02(self, tmp_path: Path, first: int):
        """F02 indexed in both entry orders yields the same canonical snapshot.

        Both runs share ONE project root (config_hash covers project_root);
        only the entry order of compile_commands.json differs, and each run
        lands in its own fresh DB directory.
        """
        root = tmp_path / "ord"
        root.mkdir()
        h = {}
        snap = {}
        for tag, order in (("a", (first, 1 - first)), ("b", (1 - first, first))):
            cc = _f02(root, order=order)
            db = tmp_path / f"ord-{tag}" / "index.db"
            h[tag] = _run_index(root, cc, db, "slicec-ord")
            conn = open_db(db)
            try:
                snap[tag] = _snap(conn, h[tag])
            finally:
                conn.close()
        assert h["a"] == h["b"], "entry order must not change the build identity"
        for surface in snap["a"]:
            assert snap["a"][surface] == snap["b"][surface], (
                f"{surface} must be order-independent once sorted by context_id"
            )

    def test_regression_decl_def_promotion_pinned(self, tmp_path: Path):
        """decl→def promotion inside the canonical context still works."""
        conn = _scratch_db(tmp_path, "prom.db")
        try:
            fid = upsert_file(conn, "ch-slicec", "a.c", "c")
            decl = _sym_row("ch-slicec", fid, "u:decl", "", is_definition=0)
            defn = _sym_row("ch-slicec", fid, "u:decl", "int f(void) { }", is_definition=1)
            with transaction(conn):
                insert_symbols_batch(conn, [decl])
                insert_symbols_batch(conn, [defn])
            row = conn.execute(
                "SELECT is_definition, source FROM symbols WHERE usr='u:decl'"
            ).fetchone()
            assert row["is_definition"] == 1, "decl→def promotion must survive"
            assert row["source"] == "int f(void) { }"
        finally:
            conn.close()

    def test_t2_canonical_projection_collapses_observations(self, tmp_path: Path):
        """ADR-002: the projection picks the definition observation, keeps provenance."""
        from fw_context_mcp.indexer.db import project_canonical_symbols

        conn = _scratch_db(tmp_path, "proj.db")
        try:
            fid_a = upsert_file(conn, "ch-slicec", "a.c", "c")
            fid_b = upsert_file(conn, "ch-slicec", "b.c", "c")
            with transaction(conn):
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_a, "u:f", "", is_definition=0)],
                    context_id="ctxAAA",
                )
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_b, "u:f", "return 22;", is_definition=1)],
                    context_id="ctxBBB",
                )
                # a pure declaration-only USR: first observation wins
                insert_symbols_batch(
                    conn, [_sym_row("ch-slicec", fid_a, "u:d", "", is_definition=0, line=8)],
                    context_id="ctxAAA",
                )
            rows = project_canonical_symbols(conn, "ch-slicec", usr="u:f")
            assert len(rows) == 1, "one canonical symbol per (config, usr)"
            assert rows[0]["context_id"] == "ctxBBB", (
                "the definition observation is canonical when any context has one"
            )
            all_rows = project_canonical_symbols(conn, "ch-slicec")
            assert len(all_rows) == 2, "one canonical row per (config, usr)"
        finally:
            conn.close()

    def test_t2_canonical_projection_single_context_degenerates(self, tmp_path: Path):
        """Single-context DBs: the projection returns exactly the stored rows."""
        from fw_context_mcp.indexer.db import project_canonical_symbols

        conn = _scratch_db(tmp_path, "degen.db")
        try:
            fid = upsert_file(conn, "ch-slicec", "a.c", "c")
            with transaction(conn):
                insert_symbols_batch(conn, [
                    _sym_row("ch-slicec", fid, "u:x", "int x(void) { }"),
                    _sym_row("ch-slicec", fid, "u:y", "", is_definition=0, line=9),
                ])
            rows = project_canonical_symbols(conn, "ch-slicec")
            assert {(r["usr"], r["is_definition"]) for r in rows} == {
                ("u:x", 1), ("u:y", 0),
            }, "the projection degenerates to the stored rows on one context"
        finally:
            conn.close()

    def test_t2_context_identity_helper_contract(self):
        """ADR-001: 16-hex identity, stable, every formula component moves it."""
        from fw_context_mcp.indexer.context_identity import compute_context_id

        base = dict(
            config_hash="ch",
            main_file="/p/src/a.c",
            project_root="/p",
            compile_dir="/p",
            argv=["gcc", "-DFOO=1", "-c", "a.c"],
            response_hashes=[],
            output_name="a.o",
        )
        cid = compute_context_id(**base)
        assert len(cid) == 16
        int(cid, 16)  # hex
        assert compute_context_id(**base) == cid, "stable serialization"
        # each ADR-001 component moves the identity
        for field, value in [
            ("main_file", "/p/src/b.c"),
            ("compile_dir", "/p/build"),
            ("argv", ["gcc", "-DFOO=2", "-c", "a.c"]),
            ("response_hashes", ["ab" * 16]),
            ("output_name", "b.o"),
        ]:
            changed = dict(base)
            changed[field] = value
            assert compute_context_id(**changed) != cid, (
                f"changing {field} must change the context identity (ADR-001)"
            )
        assert compute_context_id(**{**base, "config_hash": "other"}) != cid, (
            "the build identity is a context-identity component"
        )
