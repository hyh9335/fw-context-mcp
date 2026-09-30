"""T14 SLICE-F oracle: conditional compilation provenance (T03, ADR-011/013).

Frozen cases (TEST_PLAN §6, T03::test_spec.yaml).  Fixture: F03
verbatim — conditions.c + config.h under ``src/``.

RED (fail at the base commit, one documented reason each):

- T03::test_conditional_tree_builder_nesting — the directive-line tree
  builder does not exist (``indexer/conditionals.py`` is new).
- T03::test_branch_state_ordering_rule — the P2 ordering rule is not
  implemented (same new module).
- T03::test_f03_branch_states_vs_clang_preprocess — the conditionals
  tables are empty: nothing populates them at parse time.
- T03::test_explain_condition_chain_and_macro_inputs — no
  ``explain_condition`` handler exists and the tables it would read are
  empty.
- T03::test_commandline_vs_builtin_attribution — command-line ``-D``
  macros are dropped by ``_extract_macros`` (loc.file is None), so no
  row carries ``def_source='command_line'`` and builtins are
  unattributed.
- T03::test_incremental_equals_full_for_conditional_tables — the
  conditional tables are empty in BOTH runs; the population assertion
  (part of the O_INCREMENTAL_FULL_EQ oracle on these tables) fails.
- T03::test_new_tools_wire_visible — ``explain_condition`` /
  ``list_conditional_regions`` are not in the server registry.

Added case (implementer may add cases, TEST_PLAN preamble):

- test_taken_elif_directive_kept_in_filtered_content — T12 A1 boundary
  defect proof from the SLICE-F brief: in context B the TAKEN #elif's
  own directive line (10) and the closing #endif (14) are blanked in the
  ifdef-filtered content, while the dead bodies (4, 13) must stay blank.

GREEN-phase cases (TEST_PLAN §6) — added with the fix commit:
- T03::test_empty_body_branch_documented_limitation
- T03::test_extraction_overhead_bounded

Scratch: everything under pytest ``tmp_path`` (%TEMP%).  No repo-root
``.fw-context``, no global registry writes.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

# ── F03 fixture (verbatim from T03 reproduction/fixtures/f03) ────────────

CONFIG_H = (
    "#pragma once\n"
    "\n"
    "#define FROM_HEADER 3\n"
    "\n"
    "#define HEADER_BOOL 1\n"
)

CONDITIONS_C = (
    '#include "config.h"\n'
    "\n"
    "#if defined(CMD_FLAG) && (VALUE >= 2)\n"
    "int branch_a(void) { return 1; }\n"
    "  #if FROM_HEADER == 3 && !defined(DISABLE_NESTED)\n"
    "  int nested_yes(void) { return 31; }\n"
    "  #else\n"
    "  int nested_no(void) { return 32; }\n"
    "  #endif\n"
    "#elif HEADER_BOOL && (VALUE == 1)\n"
    "int branch_b(void) { return 2; }\n"
    "#else\n"
    "int branch_c(void) { return 3; }\n"
    "#endif\n"
)

# Supplementary context: a condition that uses compiler builtin macros,
# so builtin provenance (T03 C4 attribution) is exercisable end to end.
# (pip libclang 18.1.1 does not surface __GNUC__ as a MACRO_DEFINITION
# child — measured — so the frozen example uses __clang__.)
BUILTINS_C = (
    "#if defined(__clang__) && __clang_major__ >= 3\n"
    "int clang_only(void) { return 1; }\n"
    "#endif\n"
    "int always(void) { return 2; }\n"
)

CONTEXT_DEFINES = {
    "A": ["-DCMD_FLAG=1", "-DVALUE=2"],
    "B": ["-DVALUE=1"],
    "C": ["-DVALUE=0", "-DDISABLE_NESTED=1"],
}

# Frozen active sets (T03 test_spec.yaml contexts) — the branches the
# preprocessor keeps per context; the O_COMPILER_PREPROCESS oracle.
EXPECTED_TAKEN = {
    "A": {"branch_a", "nested_yes"},
    "B": {"branch_b"},
    "C": {"branch_c"},
}

# function name -> defining line in conditions.c (frozen fixture)
_F03_FUNC_LINES = {
    "branch_a": 4,
    "nested_yes": 6,
    "nested_no": 8,
    "branch_b": 11,
    "branch_c": 13,
}

CLANG_EXE = Path(r"C:\Program Files\LLVM\bin\clang.exe")


# ── harness ───────────────────────────────────────────────────────────────


def _write_cc(root: Path, entries: list[dict]) -> Path:
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
    cc.write_text(__import__("json").dumps(payload, indent=1), encoding="utf-8")
    return cc


def _f03(root: Path) -> list[dict]:
    """Write the F03 fixture; return the compile_commands entries."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "config.h").write_text(CONFIG_H, encoding="utf-8")
    (src / "conditions.c").write_text(CONDITIONS_C, encoding="utf-8")
    (src / "builtins.c").write_text(BUILTINS_C, encoding="utf-8")
    return [
        {
            "file": "src/conditions.c",
            "args": ["-std=c11", *CONTEXT_DEFINES[ctx], "-Isrc"],
            "output": f"build/conditions_{ctx}.o",
        }
        for ctx in CONTEXT_DEFINES
    ] + [
        {"file": "src/builtins.c", "args": ["-std=c11", "-Isrc"], "output": "build/builtins.o"},
    ]


def _f03_one(root: Path, ctx: str) -> list[dict]:
    """F03 restricted to ONE context (for per-context DBs)."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "config.h").write_text(CONFIG_H, encoding="utf-8")
    (src / "conditions.c").write_text(CONDITIONS_C, encoding="utf-8")
    return [
        {
            "file": "src/conditions.c",
            "args": ["-std=c11", *CONTEXT_DEFINES[ctx], "-Isrc"],
            "output": f"build/conditions_{ctx}.o",
        }
    ]


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


def _isolate_index_db(project_root: Path, tmp_dir: Path) -> Path:
    """Redirect the index dir via local.toml (SLICE-D pattern)."""
    local_dir = project_root / ".fw-context"
    local_dir.mkdir(parents=True, exist_ok=True)
    index_dir = tmp_dir / "index"
    fwd = str(index_dir).replace("\\", "/")
    (local_dir / "local.toml").write_text(f'[index]\ndb_dir = "{fwd}"\n', encoding="utf-8")
    return index_dir


def _indexed_project(tmp_path: Path, name: str, commands) -> tuple[Path, Path, str]:
    """Fixture project + isolated index dir + full index → (root, db, hash)."""
    root = tmp_path / name
    root.mkdir()
    cc = _write_cc(root, commands(root))
    index_dir = _isolate_index_db(root, tmp_path / f"{name}_state")
    import time

    from fw_context_mcp.config.settings import generate_project_id, _write_project_id

    pid = generate_project_id()
    for attempt in range(5):
        try:
            _write_project_id(root, pid)
            break
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    else:
        _write_project_id(root, pid)
    db_path = index_dir / pid / "index.db"
    ch = _run_index(root, cc, db_path, pid)
    return root, db_path, ch


def _clang_preprocess_functions(src: Path, defines: list[str], include: Path) -> set[str] | None:
    """O_COMPILER_PREPROCESS oracle: function names surviving clang -E -P."""
    if not CLANG_EXE.exists():
        return None
    out = subprocess.run(
        [str(CLANG_EXE), *defines, f"-I{include}", "-E", "-P", str(src)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        return None
    return set(re.findall(r"int\s+(\w+)\s*\(", out.stdout))


def _branch_rows(conn: sqlite3.Connection, ch: str, root_line: int) -> list[tuple]:
    """(line, branch_kind, taken) for the group rooted at *root_line*."""
    return [
        (r["line"], r["branch_kind"], r["taken"])
        for r in conn.execute(
            """SELECT b.line, b.branch_kind, b.taken FROM conditional_branches b
               JOIN conditionals c ON c.id = b.conditional_id
               WHERE c.config_hash=? AND c.line=?
               ORDER BY b.line""",
            (ch, root_line),
        )
    ]


def _macro_inputs(conn: sqlite3.Connection, ch: str, root_line: int) -> dict[str, tuple]:
    """{macro_name: (def_source, def_file, def_line)} of the group root."""
    return {
        r["macro_name"]: (r["def_source"], r["def_file"], r["def_line"])
        for r in conn.execute(
            """SELECT m.macro_name, m.def_source, m.def_file, m.def_line
               FROM conditional_macros m
               JOIN conditionals c ON c.id = m.conditional_id
               WHERE c.config_hash=? AND c.line=?""",
            (ch, root_line),
        )
    }


# ── RED: T0 unit cases ────────────────────────────────────────────────────


class TestSliceFRed:
    """The seven frozen RED cases + the boundary-defect proof."""

    def test_conditional_tree_builder_nesting(self):
        """T0_UNIT: stack-built nesting; elif/else are branches; else has no condition."""
        from fw_context_mcp.indexer.conditionals import (
            build_conditional_tree,
            parse_conditional_directives,
        )

        src = [
            "#if A",            # 1  outer root
            "int a1(void);",    # 2
            "#ifdef B",         # 3  inner root
            "int b1(void);",    # 4
            "#ifndef C",        # 5  nested root
            "int c1(void);",    # 6
            "#elif D",          # 7
            "int c2(void);",    # 8
            "#else",            # 9
            "int c3(void);",    # 10
            "#endif",           # 11 closes ifndef C
            "#endif",           # 12 closes ifdef B
            "#elif E",          # 13 outer branch
            "int b2(void);",    # 14
            "#else",            # 15 outer branch
            "int b3(void);",    # 16
            "#endif",           # 17 closes outer
        ]
        directives = parse_conditional_directives(src)
        kinds = {d.line: d.kind for d in directives}
        assert kinds == {
            1: "if", 3: "ifdef", 5: "ifndef", 7: "elif", 9: "else", 11: "endif",
            12: "endif", 13: "elif", 15: "else", 17: "endif",
        }
        assert directives[0].condition == "A"
        # #else stores condition NULL (lineage only, per A03)
        else_directives = [d for d in directives if d.kind == "else"]
        assert all(d.condition is None for d in else_directives)

        groups = build_conditional_tree(directives)
        assert len(groups) == 1, "one top-level group"
        outer = groups[0]
        assert outer.root.line == 1
        assert [b.directive.line for b in outer.branches] == [1, 13, 15]
        assert [b.kind for b in outer.branches] == ["if", "elif", "else"]
        assert len(outer.nested) == 1
        inner = outer.nested[0]
        assert inner.root.line == 3
        assert inner.parent is outer, "parent_id lineage: inner group hangs off outer"
        assert [b.directive.line for b in inner.branches] == [3]
        assert len(inner.nested) == 1
        deepest = inner.nested[0]
        assert deepest.root.line == 5
        assert deepest.parent is inner
        assert [b.directive.line for b in deepest.branches] == [5, 7, 9]
        assert deepest.branches[1].kind == "elif" and deepest.branches[1].directive.condition == "D"

    def test_branch_state_ordering_rule(self):
        """T0_UNIT: the P2 ordering rule over the three measured shapes.

        First branch with a never-skipped body line is TAKEN; branches
        before it FALSE; after it NOT_EVALUATED; a group never reached
        (its parent took another branch) is wholly NOT_EVALUATED.
        """
        from fw_context_mcp.indexer.conditionals import (
            build_conditional_tree,
            evaluate_conditional_tree,
            parse_conditional_directives,
        )

        directives = parse_conditional_directives(CONDITIONS_C.split("\n"))
        groups = build_conditional_tree(directives)

        def states(skipped: set[int], kept: set[int] | None = None) -> dict[int, list[tuple]]:
            evals = evaluate_conditional_tree(groups, skipped=skipped, kept=kept)
            return {
                root: [(s.line, s.taken) for s in ev.states]
                for root, ev in evals.items()
            }

        # Context C shape: ONE merged skipped range [3..12] (clang merges
        # adjacent skipped regions); line 13/14 live.  #if FALSE, #elif
        # FALSE, #else TAKEN, nested NOT_EVALUATED — matching clang -E.
        c_shape = states(set(range(3, 13)), kept={12})
        assert c_shape[3] == [(3, 0), (10, 0), (12, 1)]
        assert c_shape[5] == [(5, -1), (7, -1)], "nested group never reached"

        # Context B shape: ranges [3..10] + [12..14].  #elif TAKEN.
        b_shape = states(set(range(3, 11)) | set(range(12, 15)), kept={10, 14})
        assert b_shape[3] == [(3, 0), (10, 1), (12, -1)]
        assert b_shape[5] == [(5, -1), (7, -1)]

        # Context A shape: range [7..9] (nested untaken #else..#endif) +
        # [10..14] (outer untaken elif/else).  Outer #if TAKEN, nested #if TAKEN.
        a_shape = states(set(range(7, 15)), kept={9, 14})
        assert a_shape[3] == [(3, 1), (10, -1), (12, -1)]
        assert a_shape[5] == [(5, 1), (7, -1)]

    def test_f03_branch_states_vs_clang_preprocess(self, tmp_path: Path):
        """T1: per-context branch states from the index == clang -E survivors."""
        for ctx in ("A", "B", "C"):
            root = tmp_path / f"f03_{ctx}"
            root.mkdir()
            cc = _write_cc(root, _f03_one(root, ctx))
            ch = _run_index(root, cc, root / "index.db", f"slicef-{ctx}")
            conn = open_db(root / "index.db")
            try:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM conditionals WHERE config_hash=?", (ch,)
                ).fetchone()[0]
                assert rows >= 7, (
                    f"context {ctx}: conditionals table is EMPTY ({rows} rows) — "
                    "provenance recording not implemented (documented RED reason)"
                )
                branches = conn.execute(
                    """SELECT COUNT(*) FROM conditional_branches b
                       JOIN conditionals c ON c.id = b.conditional_id
                       WHERE c.config_hash=?""",
                    (ch,),
                ).fetchone()[0]
                assert branches >= 5, f"context {ctx}: conditional_branches empty"

                outer = dict((line, taken) for line, _kind, taken in _branch_rows(conn, ch, 3))
                nested = dict((line, taken) for line, _kind, taken in _branch_rows(conn, ch, 5))
                if ctx == "A":
                    assert outer == {3: 1, 10: -1, 12: -1}
                    assert nested == {5: 1, 7: -1}
                elif ctx == "B":
                    assert outer == {3: 0, 10: 1, 12: -1}
                    assert nested == {5: -1, 7: -1}
                else:
                    assert outer == {3: 0, 10: 0, 12: 1}
                    assert nested == {5: -1, 7: -1}
            finally:
                conn.close()

            # O_COMPILER_PREPROCESS: the TAKEN branches' bodies are exactly
            # the functions clang -E -P keeps.
            survivors = _clang_preprocess_functions(
                root / "src" / "conditions.c", CONTEXT_DEFINES[ctx], root / "src"
            )
            if survivors is not None:
                assert survivors == EXPECTED_TAKEN[ctx], (
                    f"context {ctx}: clang -E keeps {survivors}, frozen oracle says "
                    f"{EXPECTED_TAKEN[ctx]}"
                )
                conn = open_db(root / "index.db")
                try:
                    # A function line belongs to the INNERMOST group whose
                    # [root..endif] extent contains it; the function is live
                    # iff that group's branch holding the line is TAKEN.
                    group_rows = conn.execute(
                        """SELECT c.line AS root_line, c.parent_id IS NOT NULL AS nested,
                                  b.line, b.taken
                           FROM conditional_branches b
                           JOIN conditionals c ON c.id = b.conditional_id
                           WHERE c.config_hash=? AND c.line IN (3, 5)
                           ORDER BY c.line, b.line""",
                        (ch,),
                    ).fetchall()
                    endif_lines = {
                        r["parent_line"]: r["line"]
                        for r in conn.execute(
                            """SELECT c.line, p.line AS parent_line FROM conditionals c
                               JOIN conditionals p ON p.id = c.parent_id
                               WHERE c.config_hash=? AND c.directive='endif' AND p.line IN (3, 5)""",
                            (ch,),
                        )
                    }
                    extents = {3: (3, endif_lines.get(3, 10**9)), 5: (5, endif_lines.get(5, 10**9))}
                    branches_of = {3: [], 5: []}
                    for r in group_rows:
                        branches_of[r["root_line"]].append((r["line"], r["taken"]))
                    taken_funcs: set[str] = set()
                    for fname, fline in _F03_FUNC_LINES.items():
                        innermost = max(
                            (root for root, (start, end) in extents.items() if start <= fline <= end),
                            default=None,
                        )
                        if innermost is None:
                            continue
                        for i, (bline, taken) in enumerate(branches_of[innermost]):
                            nxt = (
                                branches_of[innermost][i + 1][0]
                                if i + 1 < len(branches_of[innermost])
                                else extents[innermost][1]
                            )
                            if bline <= fline < nxt:
                                if taken == 1:
                                    taken_funcs.add(fname)
                                break
                    assert taken_funcs == EXPECTED_TAKEN[ctx], (
                        f"context {ctx}: index-derived taken bodies {taken_funcs} != "
                        f"clang survivors {EXPECTED_TAKEN[ctx]}"
                    )
                finally:
                    conn.close()

    def test_explain_condition_chain_and_macro_inputs(self, tmp_path: Path):
        """T1: explain_condition(context A, conditions.c:6) → chain + inputs."""
        root, _db, _ch = _indexed_project(tmp_path, "explain_a", lambda r: _f03_one(r, "A"))
        from fw_context_mcp.mcp.handlers import conditionals as cond_tools

        result = cond_tools.explain_condition(
            file_path="src/conditions.c", line=6, project_root=str(root)
        )
        assert "error" not in result, result
        chain = result["chain"]
        assert [c["line"] for c in chain] == [3, 5], "outermost-first chain"
        assert chain[0]["taken"] == 1 and chain[1]["taken"] == 1
        inputs0 = {m["name"]: m for m in chain[0]["macro_inputs"]}
        assert set(inputs0) == {"CMD_FLAG", "VALUE"}
        assert inputs0["CMD_FLAG"]["def_source"] == "command_line"
        assert inputs0["VALUE"]["def_source"] == "command_line"
        inputs1 = {m["name"]: m for m in chain[1]["macro_inputs"]}
        assert set(inputs1) == {"FROM_HEADER", "DISABLE_NESTED"}
        assert inputs1["FROM_HEADER"]["def_source"] == "file"
        assert inputs1["FROM_HEADER"]["def_file"].endswith("config.h")
        assert inputs1["FROM_HEADER"]["def_line"] == 3
        assert inputs1["DISABLE_NESTED"]["def_source"] == "", (
            "DISABLE_NESTED is undefined in context A — provenance stays empty, "
            "never fabricated"
        )

    def test_commandline_vs_builtin_attribution(self, tmp_path: Path):
        """T1: -D macros stored with def_source='command_line'; builtins attributed."""
        root, _db, _ch = _indexed_project(tmp_path, "attr", _f03)
        conn = open_db(_db)
        try:
            ctx_rows = conn.execute(
                "SELECT DISTINCT context_id, config_hash FROM files WHERE path LIKE '%conditions.c'"
            ).fetchall()
            ctx_a = [r for r in ctx_rows if r["context_id"]]
            assert ctx_a, "no per-context file rows"
            ch_a = ctx_a[0]["config_hash"]

            row = conn.execute(
                """SELECT file_id, def_source, value, expanded_value FROM macros
                   WHERE config_hash=? AND name='CMD_FLAG' AND context_id != ''""",
                (ch_a,),
            ).fetchone()
            assert row is not None, (
                "CMD_FLAG (command-line -D) has NO macros row — _extract_macros drops "
                "loc.file-None cursors (documented RED reason)"
            )
            file_id, def_source, value, expanded = row
            assert file_id is None, "a -D macro has no file row"
            assert def_source == "command_line"
            assert value == "1"
            if CLANG_EXE.exists():
                assert expanded == "1", "expanded_value must come from clang -dM -E"
            value_row = conn.execute(
                """SELECT value FROM macros WHERE config_hash=? AND name='VALUE'
                   AND context_id != '' AND file_id IS NULL""",
                (ch_a,),
            ).fetchone()
            assert value_row is not None and value_row[0] == "2"

            # builtins: NOT in the ordinary macros table (excluded by default)…
            n_builtin = conn.execute(
                """SELECT COUNT(*) FROM macros WHERE config_hash=? AND def_source='builtin'""",
                (ch_a,),
            ).fetchone()[0]
            assert n_builtin == 0
            # …but attributed where provenance is consumed: builtins.c's
            # condition names __clang__ / __clang_major__ → def_source='builtin'.
            ch_b = conn.execute(
                """SELECT DISTINCT config_hash FROM files WHERE path LIKE '%builtins.c'"""
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT m.macro_name, m.def_source FROM conditional_macros m
                   JOIN conditionals c ON c.id = m.conditional_id
                   WHERE c.config_hash=?""",
                (ch_b,),
            ).fetchall()
            by_name = {r["macro_name"]: r["def_source"] for r in rows}
            assert by_name.get("__clang__") == "builtin", by_name
            assert by_name.get("__clang_major__") == "builtin", by_name
        finally:
            conn.close()

    def test_incremental_equals_full_for_conditional_tables(self, tmp_path: Path):
        """T2: comment appended to conditions.c — incremental == clean rebuild."""
        root, db_path, _ch = _indexed_project(tmp_path, "g1", lambda r: _f03_one(r, "A"))

        # mutate: append a comment line (no directive change)
        (root / "src" / "conditions.c").write_text(
            CONDITIONS_C + "/* tail comment */\n", encoding="utf-8"
        )
        cc = _write_cc(root, _f03_one(root, "A"))
        _run_index(root, cc, db_path, "slicef-g1")

        def snapshot(db: Path) -> dict:
            conn = open_db(db)
            try:
                ch = conn.execute(
                    "SELECT DISTINCT config_hash FROM conditionals"
                ).fetchone()
                assert ch is not None, (
                    "conditionals table EMPTY after refresh — provenance recording "
                    "not implemented (documented RED reason)"
                )
                hashv = ch[0]
                conds = [
                    (r["path"], r["line"], r["directive"], r["condition"],
                     r["parent_line"])
                    for r in conn.execute(
                        """SELECT f.path, c.line, c.directive, c.condition, p.line AS parent_line
                           FROM conditionals c
                           JOIN files f ON f.id = c.file_id
                           LEFT JOIN conditionals p ON p.id = c.parent_id
                           WHERE c.config_hash=? ORDER BY f.path, c.line""",
                        (hashv,),
                    )
                ]
                branches = [
                    (r["root_line"], r["line"], r["branch_kind"], r["taken"])
                    for r in conn.execute(
                        """SELECT c.line AS root_line, b.line, b.branch_kind, b.taken
                           FROM conditional_branches b
                           JOIN conditionals c ON c.id = b.conditional_id
                           WHERE c.config_hash=? ORDER BY c.line, b.line""",
                        (hashv,),
                    )
                ]
                macros = [
                    (r["root_line"], r["macro_name"], r["def_source"], r["def_file"], r["def_line"])
                    for r in conn.execute(
                        """SELECT c.line AS root_line, m.macro_name, m.def_source,
                                  m.def_file, m.def_line
                           FROM conditional_macros m
                           JOIN conditionals c ON c.id = m.conditional_id
                           WHERE c.config_hash=? ORDER BY c.line, m.macro_name""",
                        (hashv,),
                    )
                ]
                assert len(conds) >= 7 and len(branches) >= 5 and len(macros) >= 2, (
                    "conditional tables under-populated — recording not implemented"
                )
                return {"conds": conds, "branches": branches, "macros": macros}
            finally:
                conn.close()

        inc = snapshot(db_path)
        full_db = tmp_path / "g1" / "clean" / "index.db"
        _run_index(root, cc, full_db, "slicef-g1-clean")
        full = snapshot(full_db)
        assert inc == full, "incremental refresh diverges from clean full reindex"

    def test_new_tools_wire_visible(self, tmp_path: Path):
        """INTEGRATION: both seeds in tools/list; call returns the B chain."""
        from fw_context_mcp.mcp import server

        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert "explain_condition" in names, (
            "explain_condition not registered (documented RED reason)"
        )
        assert "list_conditional_regions" in names, (
            "list_conditional_regions not registered (documented RED reason)"
        )
        # No private duplicate of the two new tools may reach the wire.
        # (reindex_file_impl is a pre-existing wire name — not ours.)
        for base in ("explain_condition", "list_conditional_regions"):
            assert not any(
                n == f"{base}_impl" or n == f"_{base}" or n == f"{base}_internal"
                for n in names
            ), f"a private duplicate of {base} reached the wire"

        root, _db, _ch = _indexed_project(tmp_path, "wire_b", lambda r: _f03_one(r, "B"))
        from fw_context_mcp.mcp.handlers import conditionals as cond_tools

        result = cond_tools.explain_condition(
            file_path="src/conditions.c", line=11, project_root=str(root)
        )
        assert "error" not in result, result
        outer = result["chain"][0]
        states = {s["line"]: s["taken"] for s in outer["branch_states"]}
        assert states == {3: 0, 10: 1, 12: -1}, "context-B evaluated states on the wire"

        regions = cond_tools.list_conditional_regions(
            file_path="src/conditions.c", project_root=str(root)
        )
        assert isinstance(regions, dict) and "error" not in regions, regions
        roots = [r["root_line"] for r in regions["regions"]]
        assert roots == [3, 5]

    def test_taken_elif_directive_kept_in_filtered_content(self, tmp_path: Path):
        """Added case (SLICE-F brief item 3): the TAKEN #elif's directive line
        must survive the ifdef filter in context B; dead lines must not."""
        root, _db, ch = _indexed_project(tmp_path, "keep_b", lambda r: _f03_one(r, "B"))
        conn = open_db(_db)
        try:
            row = conn.execute(
                "SELECT content FROM files WHERE config_hash=? AND path LIKE '%conditions.c'",
                (ch,),
            ).fetchone()
            assert row is not None
            lines = row[0].split("\n")
        finally:
            conn.close()

        assert lines[9] == "#elif HEADER_BOOL && (VALUE == 1)", (
            f"line 10 blanked ({lines[9]!r}): the TAKEN #elif's own directive line is "
            "swallowed by the skipped range (documented RED reason)"
        )
        assert lines[13] == "#endif", (
            f"line 14 blanked ({lines[13]!r}): closing #endif of an untaken last "
            "branch is swallowed (same boundary defect)"
        )
        assert lines[10] == "int branch_b(void) { return 2; }", "live body must stay"
        # the guard half of the same predicate: dead lines stay dead
        assert lines[2] == "", "untaken #if directive stays blank"
        assert lines[3] == "", "dead body stays blank"
        assert lines[11] == "", "untaken #else directive stays blank"
        assert lines[12] == "", "dead body stays blank"
        assert lines[0] == '#include "config.h"', "active include stays"
        assert lines[1] == "", "blank line stays blank"


# ── GREEN-phase cases (added with the fix commit) ─────────────────────────


class TestSliceFGreenPhase:
    """GREEN cases of TEST_PLAN §6 — must pass post-fix; added with the fix."""

    def test_empty_body_branch_documented_limitation(self, tmp_path: Path):
        """A taken EMPTY #else derives TAKEN via the directive-line rule; an
        empty #elif inside a MERGED range may stay undecided — the result
        must carry the limitation, never silently claim exactness."""
        pytest.importorskip("fw_context_mcp.indexer.conditionals")
        from fw_context_mcp.indexer.conditionals import (
            build_conditional_tree,
            evaluate_conditional_tree,
            parse_conditional_directives,
        )

        empty_else = [
            "#if X",           # 1
            "int x(void);",    # 2
            "#else",           # 3  taken, empty body
            "#endif",          # 4
        ]
        groups = build_conditional_tree(parse_conditional_directives(empty_else))
        # clang: skipped range [1..3] (#else line is its END), #endif not skipped
        evals = evaluate_conditional_tree(groups, skipped={1, 2, 3}, kept={3})
        ev = evals[1]
        assert [(s.line, s.taken) for s in ev.states] == [(1, 0), (3, 1)], (
            "taken empty #else must be derived via the directive-line rule"
        )

        merged_elif = [
            "#if X",           # 1
            "#elif Y",         # 2  taken, empty body — inside a merged range
            "#else",           # 3
            "#endif",          # 4
        ]
        groups = build_conditional_tree(parse_conditional_directives(merged_elif))
        # clang: one merged range [1..4] (everything after the taken empty
        # #elif is skipped, merging both regions) — kept = {} (line 2 both
        # ends and starts ranges).  The fallback cannot decide → limitation.
        evals = evaluate_conditional_tree(groups, skipped={1, 2, 3, 4}, kept=set())
        ev = evals[1]
        assert ev.exact is False, "an undecided merged-range #elif must not claim exactness"
        assert ev.limitation, "a limitation note must be surfaced"

    def test_extraction_overhead_bounded(self, tmp_path: Path):
        """The conditional extraction pass must stay small next to the token
        pass of the content fill (no per-line Python re-scanning of the DB)."""
        root, _db, _ch = _indexed_project(tmp_path, "overhead", lambda r: _f03_one(r, "A"))
        conn = open_db(_db)
        try:
            n_directives = conn.execute("SELECT COUNT(*) FROM conditionals").fetchone()[0]
        finally:
            conn.close()
        # F03 per context: 7 directives — the extractor is line-bounded, not
        # quadratic; assert the DB holds exactly the directive rows (no fan-out).
        assert n_directives == 7, f"expected 7 directive rows, got {n_directives}"
