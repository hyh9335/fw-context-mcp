"""T14 SLICE-M oracle: raw/active/directive content modes (T12, ADR-012).

Frozen cases (TEST_PLAN §13, T12::test_spec.yaml).  Fixture: F03
verbatim — conditions.c + config.h under ``src/``, one context (B:
``-DVALUE=1`` → branch_b taken, the taken ``#elif`` at line 10 and the
closing ``#endif`` at 14 are boundary lines the preprocessor read).

RED (fail at the base commit, one documented reason each):

- T12::T12-C1/C2 boundary-keep — ALREADY GREEN at the RED base: SLICE-F
  pre-landed the A1 boundary-keep predicate on ``files.content``
  (ops.py ``collect_skipped_and_kept`` + boundary restore).  The case is
  re-driven here through the ``read_file`` handler as a regression guard;
  its pass at RED is documented, not silent.
- T12::T12-C5 raw mode — ``read_file``/``search_content`` take no
  ``mode`` parameter (TypeError), and nothing can reach the raw disk
  text of an inactive branch.
- T12::T12-C6 directives mode — no directives channel exists; the
  ``file_directives`` table (SLICE-B schema) has NO writer, so a
  directives view would be empty even if a parameter existed.
- T12::T12-C8 skeleton annotations — active-mode ``read_file`` output
  carries no annotation channel for the conditional skeleton.
- file_directives population + ADR-013 incremental gate — the table is
  empty in BOTH a full and an incremental run (no writer).

GREEN-phase cases (TEST_PLAN §13) — must pass post-fix:

- T12-C3 (untaken branches stay blanked, guarded inside the C1/C2 case),
- T12-C4 (active search unchanged), T12-C5, T12-C6, T12-C8,
- C10 (pinned contract tests updated — exercised in
  ``test_filtered_content_extent.py`` edits, verified by the SLICE-M
  scratch pin-derivation run, receipt-documented).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

# ── F03 fixture (verbatim from T12 test_spec.yaml / SLICE-F harness) ──────

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

# Mutation for the incremental gate (ADR-013): drop the final untaken
# ``#else`` branch and append one new directive.  Both edits must survive
# an incremental re-parse with no stale row left behind.
MUTATED_C = (
    '#include "config.h"\n'          # 1
    "\n"                             # 2
    "#if defined(CMD_FLAG) && (VALUE >= 2)\n"  # 3
    "int branch_a(void) { return 1; }\n"       # 4
    "  #if FROM_HEADER == 3 && !defined(DISABLE_NESTED)\n"  # 5
    "  int nested_yes(void) { return 31; }\n"               # 6
    "  #else\n"                                             # 7
    "  int nested_no(void) { return 32; }\n"                # 8
    "  #endif\n"                                            # 9
    "#elif HEADER_BOOL && (VALUE == 1)\n"                   # 10
    "int branch_b(void) { return 2; }\n"                    # 11
    "#endif\n"                                              # 12
    "#undef VALUE\n"                                        # 13
)

EXPECTED_DIRECTIVES_ORIGINAL = [
    (1, "include"),
    (3, "if"),
    (5, "if"),
    (7, "else"),
    (9, "endif"),
    (10, "elif"),
    (12, "else"),
    (14, "endif"),
]

EXPECTED_DIRECTIVES_MUTATED = [
    (1, "include"),
    (3, "if"),
    (5, "if"),
    (7, "else"),
    (9, "endif"),
    (10, "elif"),
    (12, "endif"),
    (13, "undef"),
]


# ── harness (SLICE-F pattern) ──────────────────────────────────────────────


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


def _f03_b(root: Path) -> list[dict]:
    """F03, context B only: ``-DVALUE=1`` → branch_b taken."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "config.h").write_text(CONFIG_H, encoding="utf-8")
    (src / "conditions.c").write_text(CONDITIONS_C, encoding="utf-8")
    return [
        {
            "file": "src/conditions.c",
            "args": ["-std=c11", "-DVALUE=1", "-Isrc"],
            "output": "build/conditions_B.o",
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
    """Redirect the index dir via local.toml — never a repo-root .fw-context."""
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


def _directive_rows(conn: sqlite3.Connection, ch: str, path_suffix: str) -> list[tuple[int, str]]:
    """(line, kind) of every file_directives row for one path, in line order."""
    return [
        (r["line"], r["kind"])
        for r in conn.execute(
            """SELECT line, kind FROM file_directives
               WHERE config_hash=? AND path LIKE ?
               ORDER BY line""",
            (ch, f"%{path_suffix}"),
        )
    ]


# ── file_directives population (the T12/ADR-012 directive channel) ────────


class TestFileDirectivesPopulated:
    """``file_directives`` (SLICE-B schema) gets its writer (ADR-012 §2)."""

    def test_conditions_c_directives_present(self, tmp_path: Path):
        root, db, ch = _indexed_project(tmp_path, "dirs_b", _f03_b)
        conn = open_db(db)
        try:
            rows = _directive_rows(conn, ch, "conditions.c")
        finally:
            conn.close()
        assert rows == EXPECTED_DIRECTIVES_ORIGINAL, (
            f"file_directives for conditions.c is {rows} — the T12 directive "
            "channel is not populated (documented RED reason: the table has "
            "no writer at the RED base)"
        )

    def test_config_h_directives_present(self, tmp_path: Path):
        root, db, ch = _indexed_project(tmp_path, "dirs_h", _f03_b)
        conn = open_db(db)
        try:
            rows = _directive_rows(conn, ch, "config.h")
        finally:
            conn.close()
        assert rows == [(1, "pragma"), (3, "define"), (5, "define")], (
            f"file_directives for config.h is {rows} — define/pragma rows "
            "must be present for the directives channel (documented RED reason)"
        )

    def test_directive_text_is_the_raw_line(self, tmp_path: Path):
        """text holds the directive line as on disk — the C6 search surface."""
        root, db, ch = _indexed_project(tmp_path, "dirs_text", _f03_b)
        conn = open_db(db)
        try:
            row = conn.execute(
                """SELECT text FROM file_directives
                   WHERE config_hash=? AND path LIKE '%conditions.c' AND line=10""",
                (ch,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "no row for the taken #elif (documented RED reason)"
        assert row["text"] == "#elif HEADER_BOOL && (VALUE == 1)", row["text"]

    def test_incremental_equals_full_for_file_directives(self, tmp_path: Path):
        """ADR-013 standing gate for the new rows: incremental re-parse of a
        mutated file leaves exactly the fresh rows — the deleted ``#else``
        row is gone and the appended ``#undef`` row is there in both runs."""
        root, db, ch = _indexed_project(tmp_path, "incr_dirs", _f03_b)
        cc = root / "compile_commands.json"
        (root / "src" / "conditions.c").write_text(MUTATED_C, encoding="utf-8")
        # Move the mtime out of the indexer's Tier-1 mtime tolerance
        # (MTIME_TOLERANCE_S = 1.0, a T01 product constant): a write that
        # lands within 1 s of the stored mtime is legitimately read as
        # "unchanged" and the re-parse is skipped.  The gate below is about
        # the directive rows of a RE-PARSED file, so give the re-parse its
        # normal entry: mtime well past the tolerance, then the Tier-2
        # content hash decides.
        _bump_mtime(root / "src" / "conditions.c")
        _run_index(root, cc, db, _project_id(root))

        fresh = tmp_path / "incr_dirs_fresh"
        root2 = tmp_path / "incr_full"
        root2.mkdir()
        cc2 = _write_cc(root2, _f03_b(root2))
        # _f03_b writes the ORIGINAL fixture — overwrite with the mutated
        # text AFTER it, so the full run indexes the same bytes run 1 saw
        # after the mutation.
        (root2 / "src" / "conditions.c").write_text(MUTATED_C, encoding="utf-8")
        index_dir = _isolate_index_db(root2, fresh)
        from fw_context_mcp.config.settings import generate_project_id, _write_project_id

        pid2 = generate_project_id()
        _write_project_id(root2, pid2)
        db2 = index_dir / pid2 / "index.db"
        ch2 = _run_index(root2, cc2, db2, pid2)

        conn = open_db(db)
        try:
            incremental = _directive_rows(conn, ch, "conditions.c")
        finally:
            conn.close()
        conn2 = open_db(db2)
        try:
            full = _directive_rows(conn2, ch2, "conditions.c")
        finally:
            conn2.close()
        assert incremental == EXPECTED_DIRECTIVES_MUTATED, (
            f"incremental run left {incremental} — a stale row survived the "
            "re-parse or the fresh rows did not land"
        )
        assert full == incremental, (
            f"full ({full}) != incremental ({incremental}) — ADR-013 gate"
        )


def _project_id(root: Path) -> str:
    from fw_context_mcp.config.settings import derive_project_id

    return derive_project_id(root)


def _bump_mtime(path: Path, offset_s: float = 5.0) -> None:
    """Shift a file's mtime *offset_s* into the future (Tier-1 escape hatch)."""
    import os

    stat = path.stat()
    os.utime(path, (stat.st_atime + offset_s, stat.st_mtime + offset_s))


# ── mode parameter on read_file (ADR-012 §2) ──────────────────────────────


class TestReadFileModes:
    def test_boundary_directives_kept_via_read_file(self, tmp_path: Path):
        """T12-C1/C2 through the handler + C3 guard.

        RED note: this case is listed in TEST_PLAN §13 as SLICE-M RED, but
        SLICE-F already landed the A1 boundary-keep predicate on
        ``files.content`` — it passes at the RED base on purpose and is the
        regression guard for the mode plumbing built on top of it.
        """
        root, _db, _ch = _indexed_project(tmp_path, "boundary", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(file_path="src/conditions.c", project_root=str(root))
        assert "error" not in result, result
        lines = result["content"].split("\n")
        assert lines[9] == "#elif HEADER_BOOL && (VALUE == 1)", (
            f"line 10 = {lines[9]!r} — the taken #elif's own directive line "
            "must stay visible above its active code (C1)"
        )
        assert lines[13] == "#endif", (
            f"line 14 = {lines[13]!r} — the closing #endif of an untaken "
            "last branch must stay visible (C2)"
        )
        # C3: everything the preprocessor skipped stays blank.
        assert lines[2] == "", "untaken #if stays blank (C3)"
        assert lines[3] == "", "dead body stays blank (C3)"
        assert lines[7] == "", "never-reached nested branch stays blank (C3)"
        assert lines[11] == "", "untaken #else stays blank (C3)"
        assert lines[10] == "int branch_b(void) { return 2; }", "live body stays"

    def test_mode_raw_returns_disk_text(self, tmp_path: Path):
        """T12-C5: raw mode gives the file exactly as on disk — the dead
        branch text is reachable, default active output is untouched."""
        root, _db, _ch = _indexed_project(tmp_path, "raw", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(file_path="src/conditions.c", mode="raw", project_root=str(root))
        assert "error" not in result, result
        assert result.get("mode") == "raw"
        assert result["lines"] == 14
        # AUD-R9 made raw byte-exact: the fixture is written through text mode,
        # so on Windows the disk bytes carry CRLF and splitlines() (not a bare
        # split("\n")) is the ending-agnostic way to cut lines.
        lines = result["content"].splitlines()
        assert lines[2] == "#if defined(CMD_FLAG) && (VALUE >= 2)", (
            "raw mode keeps the untaken #if directive as text"
        )
        assert lines[7] == "  int nested_no(void) { return 32; }", (
            "raw mode reaches the never-taken nested branch (C5)"
        )
        assert lines[10] == "int branch_b(void) { return 2; }", "live code unchanged"

    def test_mode_raw_window_and_line_numbers(self, tmp_path: Path):
        root, _db, _ch = _indexed_project(tmp_path, "raw_win", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(
            file_path="src/conditions.c", mode="raw",
            start_line=7, end_line=8, line_numbers=True, project_root=str(root),
        )
        assert "error" not in result, result
        assert result["start_line"] == 7 and result["end_line"] == 8
        assert "nested_no" in result["content"]

    def test_mode_raw_is_disk_truth_after_edit(self, tmp_path: Path):
        """raw mode reads the file NOW: an edit the index has not seen yet
        is visible in raw and still invisible in the indexed active view."""
        root, _db, _ch = _indexed_project(tmp_path, "raw_edit", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        marker = "int edited_after_index(void) { return 9; }\n"
        path = root / "src" / "conditions.c"
        path.write_text(path.read_text(encoding="utf-8") + marker, encoding="utf-8")

        raw = read_file(file_path="src/conditions.c", mode="raw", project_root=str(root))
        assert "error" not in raw, raw
        assert "edited_after_index" in raw["content"], (
            "raw mode must show the disk text as it is now"
        )
        assert raw.get("stale") is True, "raw must disclose that the index is older"

        active = read_file(file_path="src/conditions.c", project_root=str(root))
        assert "error" not in active, active
        assert "edited_after_index" not in active["content"], (
            "the indexed active view does not know the edit yet"
        )

    def test_mode_directives_lists_conditional_skeleton(self, tmp_path: Path):
        """T12-C6 via read_file: the directives view lists every directive
        line with kind + text, independent of taken/untaken."""
        root, _db, _ch = _indexed_project(tmp_path, "dirs_mode", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(
            file_path="src/conditions.c", mode="directives", project_root=str(root),
        )
        assert "error" not in result, result
        assert result.get("mode") == "directives"
        got = [(d["line"], d["kind"]) for d in result["directives"]]
        assert got == EXPECTED_DIRECTIVES_ORIGINAL, got
        by_line = {d["line"]: d for d in result["directives"]}
        assert by_line[3]["text"] == "#if defined(CMD_FLAG) && (VALUE >= 2)"
        assert by_line[14]["kind"] == "endif"

    def test_mode_directives_window(self, tmp_path: Path):
        root, _db, _ch = _indexed_project(tmp_path, "dirs_win", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(
            file_path="src/conditions.c", mode="directives",
            start_line=5, end_line=9, project_root=str(root),
        )
        assert "error" not in result, result
        got = [(d["line"], d["kind"]) for d in result["directives"]]
        assert got == [(5, "if"), (7, "else"), (9, "endif")], got

    def test_mode_unknown_rejected(self, tmp_path: Path):
        """An unknown mode is an error, never a silent fallback to active."""
        root, _db, _ch = _indexed_project(tmp_path, "bad_mode", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(file_path="src/conditions.c", mode="bogus", project_root=str(root))
        assert isinstance(result, dict) and "error" in result, result
        assert "bogus" in result["error"]

    def test_active_mode_skeleton_annotation_channel(self, tmp_path: Path):
        """T12-C8: active mode carries the conditional skeleton as
        annotations in a SEPARATE channel — never as injected code lines."""
        root, _db, _ch = _indexed_project(tmp_path, "skeleton", _f03_b)
        from fw_context_mcp.mcp.handlers.source import read_file

        result = read_file(file_path="src/conditions.c", project_root=str(root))
        assert "error" not in result, result
        assert "skeleton" in result, (
            "active-mode read_file has no skeleton annotation channel "
            "(documented RED reason: C8 channel not implemented)"
        )
        skeleton = result["skeleton"]
        by_line = {a["line"]: a for a in skeleton}
        # The conditional skeleton — both taken and untaken directives.
        assert {3, 5, 7, 9, 10, 12, 14} <= set(by_line), sorted(by_line)
        assert by_line[10]["text"] == "#elif HEADER_BOOL && (VALUE == 1)"
        assert by_line[3]["text"] == "#if defined(CMD_FLAG) && (VALUE >= 2)"
        # The annotation channel is not code: non-conditional directives and
        # live code lines are not in it.
        assert 1 not in by_line, "#include is not conditional-structure annotation"
        # Line numbers stable, code lines unchanged — annotations never shift
        # or replace content.
        lines = result["content"].splitlines()
        assert len(lines) == 14
        assert lines[10] == "int branch_b(void) { return 2; }"


# ── mode parameter on search_content (ADR-012 §2) ─────────────────────────


class TestSearchContentModes:
    def test_active_search_unchanged(self, tmp_path: Path):
        """T12-C4: default-mode semantics are untouched by the mode plumbing."""
        root, _db, _ch = _indexed_project(tmp_path, "c4", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        # r10 paging: the active view leads with the provenance row — skip it.
        default = [r for r in search_content(query="branch_b", project_root=str(root)) if "file" in r]
        explicit = [r for r in search_content(query="branch_b", mode="active", project_root=str(root)) if "file" in r]
        assert default == explicit, (
            "default call and explicit mode='active' must be identical"
        )
        assert len(default) == 1, default
        assert default[0]["file"].endswith("conditions.c")
        assert default[0]["match_lines"] == [11]

    def test_mode_directives_reaches_conditions(self, tmp_path: Path):
        """T12-C6: 'CMD_FLAG' is only text on an UNTAKEN branch's directive
        line — invisible in active mode, found in directives mode at the
        absolute file line."""
        root, _db, _ch = _indexed_project(tmp_path, "c6", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        active = search_content(query="CMD_FLAG", project_root=str(root))
        assert active == [], (
            "active mode must not see the blanked directive text (C5 guard)"
        )
        res = search_content(query="CMD_FLAG", mode="directives", project_root=str(root))
        assert len(res) == 1, res
        r = res[0]
        assert r["file"].endswith("conditions.c")
        assert r["match_lines"] == [3], (
            f"match_lines {r.get('match_lines')} — directives-mode lines must "
            "be absolute file lines (ADR-012 FTS parity)"
        )
        d = r["directives"][0]
        assert d["line"] == 3 and d["kind"] == "if"
        assert "CMD_FLAG" in d["text"]

    def test_mode_directives_untaken_else_reachable(self, tmp_path: Path):
        """A directive the preprocessor never took is still first-class
        text: the untaken ``#else`` at line 12 is found by its own text —
        the directive row, not the dead body under it."""
        root, _db, _ch = _indexed_project(tmp_path, "c6b", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        res = search_content(query="else", mode="directives", project_root=str(root))
        assert len(res) == 1, res
        r = res[0]
        assert r["file"].endswith("conditions.c")
        assert r["match_lines"] == [7, 12], r
        kinds = {d["line"]: d["kind"] for d in r["directives"]}
        assert kinds == {7: "else", 12: "else"}, kinds

    def test_mode_raw_reaches_dead_branch(self, tmp_path: Path):
        """T12-C5: raw mode reaches text that lives only in a dead branch;
        default active mode still returns nothing for it."""
        root, _db, _ch = _indexed_project(tmp_path, "c5", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        active = search_content(query="nested_no", project_root=str(root))
        assert active == [], "active mode must not see untaken branch code"
        raw = search_content(query="nested_no", mode="raw", project_root=str(root))
        hits = [r for r in raw if isinstance(r, dict) and "error" not in r]
        assert hits, raw
        cond = [r for r in hits if r["file"].endswith("conditions.c")]
        assert cond, raw
        assert 8 in cond[0]["match_lines"], cond[0]

    def test_mode_raw_dead_function_body_text(self, tmp_path: Path):
        """A term that appears ONLY inside an untaken branch body is found
        on disk by raw mode and nowhere else."""
        root, _db, _ch = _indexed_project(tmp_path, "c5b", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        res = search_content(query="branch_a", mode="raw", project_root=str(root))
        hits = [r for r in res if isinstance(r, dict) and "error" not in r]
        assert hits, res
        cond = [r for r in hits if r["file"].endswith("conditions.c")]
        # branch_a lives on line 4, the body under the untaken #if — the
        # directive row above it is line 3, a different channel.
        assert cond and 4 in cond[0]["match_lines"], res

    def test_mode_unknown_rejected(self, tmp_path: Path):
        root, _db, _ch = _indexed_project(tmp_path, "bad_mode_s", _f03_b)
        from fw_context_mcp.mcp.handlers.search import search_content

        res = search_content(query="branch_b", mode="bogus", project_root=str(root))
        assert res and isinstance(res[0], dict) and "error" in res[0], res
