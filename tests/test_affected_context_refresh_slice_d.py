"""SLICE-D RED/GREEN oracle — T01 affected-context refresh (TEST_PLAN §4).

Strict chain: these RED cases must fail at the integration base commit
(f669c57) for the reasons documented on each test, then pass after the
SLICE-D fix.  GREEN cases are the post-fix contracts (O_INCREMENTAL_FULL_EQ
verify hook, warning driven by the actual context set, the adjacent
one-line-definition LIMITATION as a non-strict xfail, and the win32-safe
trust-rule premise test).

Mechanisms under test (T01 research, all confirmed by impl_D probe at the
base commit):

D1  ``_manifest_updater`` keys old entries by file path only
    (``old_entries[file] = entry``) and pass 1 refuses a fresh entry for a
    re-parsed TU whose pre-collected header list is empty (``tu_rel in
    tu_headers`` gate), so two compile actions of one file collapse to the
    LAST entry with a stale preliminary ``source_hash`` — the second
    context's compile action silently vanishes and "N updated, M reused"
    misreports a genuinely re-parsed file.

D2  ``reindex_file`` stores through ``store_symbols_for_unit`` with the
    default ``context_id=""`` and a path-keyed ``existing_files`` lookup
    (``get_file_mtimes`` collapses per-context rows), so a re-index of a
    file built by two contexts deletes one context's rows and inserts a
    phantom canonical (``context_id=''``) row instead of a per-context
    refresh.

D3  ``reindex_file`` on a header re-parses exactly ONE including unit
    (``_tu_for_header`` returns a single path; ``_reindex_match_tus`` takes
    ``via[:1]``) and ``_update_manifest_after_reindex`` stops at the first
    matching manifest entry (``break``) — other contexts keep stale rows
    and their manifest entries keep stale hashes.

Isolation: every DB lives under ``tmp_path`` (or the tmp-scoped index dir
redirected through ``.fw-context/local.toml``), disclosed in the receipt.
The repo-root ``.fw-context`` DB is never touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.manifest import load as load_manifest
from fw_context_mcp.indexer.manifest import tu_arguments
from fw_context_mcp.indexer.runner import run
from fw_context_mcp.utils import compute_source_hash

MTIME_BUMP_S = 5.0  # clear MTIME_TOLERANCE_S, see _advance_mtime


# ── fixture writers ──────────────────────────────────────────────────────────


def _write_cc(root: Path, entries: list[dict]) -> Path:
    """Write compile_commands.json; each entry is {file, args, output}."""
    fwd = str(root).replace("\\", "/")
    payload = []
    for i, e in enumerate(entries):
        payload.append(
            {
                "directory": fwd,
                "file": e["file"],
                "arguments": [
                    "gcc",
                    *e["args"],
                    "-c",
                    e["file"],
                    "-o",
                    e.get("output", f"build/o{i}.o"),
                ],
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


def _f02(root: Path) -> None:
    """F02: one file, two compile actions (-DMODE_VALUE=1 / =2), no headers."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "same.c").write_text(
        "#if MODE_VALUE == 1\n"
        "int mode_fn(void) {\n"
        "    return 1;\n"
        "}\n"
        "#else\n"
        "int mode_fn(void) {\n"
        "    return 2;\n"
        "}\n"
        "#endif\n",
        encoding="utf-8",
    )


def _f02_commands(root: Path) -> list[dict]:
    return [
        {"file": "src/same.c", "args": ["-std=c11", "-DMODE_VALUE=1", "-Isrc"], "output": "build/mode1.o"},
        {"file": "src/same.c", "args": ["-std=c11", "-DMODE_VALUE=2", "-Isrc"], "output": "build/mode2.o"},
    ]


def _f01(root: Path) -> None:
    """F01: common.hpp defines role_value per MODE_VALUE; two includers."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "common.hpp").write_text(
        "#if MODE_VALUE == 11\n"
        "int role_value(void) {\n"
        "    return 11;\n"
        "}\n"
        "#else\n"
        "int role_value(void) {\n"
        "    return 22;\n"
        "}\n"
        "#endif\n",
        encoding="utf-8",
    )
    (src / "master.c").write_text(
        '#include "common.hpp"\nint master_use(void) { return role_value(); }\n',
        encoding="utf-8",
    )
    (src / "slave.c").write_text(
        '#include "common.hpp"\nint slave_use(void) { return role_value(); }\n',
        encoding="utf-8",
    )


def _f01_commands(root: Path) -> list[dict]:
    return [
        {"file": "src/master.c", "args": ["-std=c11", "-DMODE_VALUE=11", "-Isrc"], "output": "build/m11.o"},
        {"file": "src/slave.c", "args": ["-std=c11", "-DMODE_VALUE=22", "-Isrc"], "output": "build/m22.o"},
    ]


# ── DB / manifest helpers ────────────────────────────────────────────────────


def _sym_rows(db_path: Path, ch: str, name: str) -> list[tuple[str, str]]:
    """[(context_id, source)] of every definition row of *name*."""
    conn = open_db(db_path)
    try:
        return [
            (r[1], r[0] or "")
            for r in conn.execute(
                "SELECT source, context_id FROM symbols "
                "WHERE config_hash=? AND name=? AND is_definition=1 "
                "ORDER BY context_id",
                (ch, name),
            ).fetchall()
        ]
    finally:
        conn.close()


def _ctx_defines(db_path: Path, ch: str) -> dict[str, str]:
    """{context_id: defines_json} ordered by entry_index."""
    conn = open_db(db_path)
    try:
        return {
            r[0]: r[1] or ""
            for r in conn.execute(
                "SELECT context_id, defines_json FROM build_contexts "
                "WHERE config_hash=? ORDER BY entry_index",
                (ch,),
            ).fetchall()
        }
    finally:
        conn.close()


def _manifest_entries(db_path: Path, ch: str) -> list[dict]:
    """Manifest entries resolved to {file, args, source_hash, context_id}."""
    manifest = load_manifest(db_path.parent, ch)
    if manifest is None:
        return []
    out = []
    for e in manifest.get("entries", []):
        out.append(
            {
                "file": e.get("file", ""),
                "args": tu_arguments(manifest, e),
                "source_hash": e.get("source_hash", ""),
                "context_id": e.get("context_id"),
            }
        )
    return out


def _entries_for(entries: list[dict], rel: str) -> list[dict]:
    return [e for e in entries if e["file"].replace("\\", "/") == rel]


def _advance_mtime(path: Path, seconds: float = MTIME_BUMP_S) -> None:
    now = path.stat().st_mtime
    os.utime(path, (now + seconds, now + seconds))


def _isolate_index_db(project_root: Path, tmp_dir: Path) -> Path:
    """Redirect the index dir via local.toml; return the index dir."""
    local_dir = project_root / ".fw-context"
    local_dir.mkdir(parents=True, exist_ok=True)
    index_dir = tmp_dir / "index"
    fwd = str(index_dir).replace("\\", "/")
    (local_dir / "local.toml").write_text(
        f'[index]\ndb_dir = "{fwd}"\n', encoding="utf-8"
    )
    return index_dir


def _indexed_project(tmp_path: Path, name: str, fixture, commands) -> tuple[Path, Path, str]:
    """Build a fixture project, isolate its index dir, run a full index."""
    root = tmp_path / name
    root.mkdir()
    fixture(root)
    cc = _write_cc(root, commands(root))
    index_dir = _isolate_index_db(root, tmp_path / f"{name}_state")
    import time

    from fw_context_mcp.config.settings import generate_project_id, _write_project_id

    pid = generate_project_id()
    # _write_project_id's atomic replace intermittently hits WinError 5 on
    # Windows (transient AV/indexer lock on the fresh tmp file) — retry.
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


def _no_canonical_rows(db_path: Path, ch: str, table: str) -> int:
    conn = open_db(db_path)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE config_hash=? AND context_id=''",
            (ch,),
        ).fetchone()[0]
    finally:
        conn.close()


# ── RED cases (must fail at the base commit) ─────────────────────────────────


class TestSliceDRed:
    """RED = must fail at the integration base commit, reasons documented."""

    def test_f02_second_context_is_indexed_not_silent_noop(self, tmp_path: Path):
        """The second compile action of one file must survive index and no-op rerun.

        Both DB contexts are indexed (SLICE-C); the RED half is the manifest:
        the updater's file-keyed carry-over collapses the pair to the last
        entry (empty preliminary source_hash, both -DMODE_VALUE=2), and a
        no-op rerun preserves the collapse — the second context's compile
        action silently vanishes from the manifest that drives staleness.
        """
        root, db_path, ch = _indexed_project(tmp_path, "red1_f02", _f02, _f02_commands)

        def assert_two_contexts():
            ctxs = _ctx_defines(db_path, ch)
            assert len(ctxs) == 2, "two compile actions must mint two contexts"
            rows = dict(_sym_rows(db_path, ch, "mode_fn"))
            cid1 = next(c for c in ctxs if "-DMODE_VALUE=1" in ctxs[c])
            cid2 = next(c for c in ctxs if "-DMODE_VALUE=2" in ctxs[c])
            assert "return 1" in rows[cid1], f"MODE=1 context must hold its own body (got {rows.get(cid1)!r})"
            assert "return 2" in rows[cid2], f"MODE=2 context must hold its own body (got {rows.get(cid2)!r})"

        assert_two_contexts()

        # A no-op rerun must not silently degrade either surface.
        _run_index(root, root / "compile_commands.json", db_path, _project_id(root))
        assert_two_contexts()

        entries = _entries_for(_manifest_entries(db_path, ch), "src/same.c")
        assert len(entries) == 2, "manifest must keep one entry per compile action"
        arg_sets = {tuple(e["args"]) for e in entries}
        assert len(arg_sets) == 2, (
            "the two compile actions must keep distinct argument sets "
            f"(got {len(arg_sets)}: {arg_sets})"
        )
        assert any("-DMODE_VALUE=1" in e["args"] for e in entries)
        assert any("-DMODE_VALUE=2" in e["args"] for e in entries)
        cur = compute_source_hash(root / "src" / "same.c")
        assert all(e["source_hash"] == cur for e in entries), (
            "a re-parsed TU's manifest entry must carry the real source hash "
            f"(got {[e['source_hash'] for e in entries]}, current {cur[:12]})"
        )

    def test_f02_incremental_refresh_never_loses_rows(self, tmp_path: Path):
        """A content touch must refresh both contexts and never drop rows.

        Run-path half (pins SLICE-C): both contexts gain the new function.
        Reindex half (RED): reindex_file re-parses both compile actions but
        stores with context_id='' and a path-collapsed existing-file lookup,
        so one context's rows are deleted and a phantom canonical row is
        inserted instead of a per-context refresh.
        """
        root, db_path, ch = _indexed_project(tmp_path, "red2_f02", _f02, _f02_commands)
        pid = _project_id(root)

        before = _sym_rows(db_path, ch, "mode_fn")
        assert len(before) == 2

        same = root / "src" / "same.c"
        same.write_text(
            same.read_text(encoding="utf-8")
            + "\nint extra_fn(void) {\n    return 9;\n}\n",
            encoding="utf-8",
        )
        _advance_mtime(same)
        _run_index(root, root / "compile_commands.json", db_path, pid)

        # Run-path half: both contexts refreshed, rows never lost.
        mode_rows = _sym_rows(db_path, ch, "mode_fn")
        assert len(mode_rows) == 2, f"mode_fn rows must never drop (got {len(mode_rows)})"
        assert all(cid for cid, _ in mode_rows), "per-context rows must keep real context ids"
        extra_rows = _sym_rows(db_path, ch, "extra_fn")
        assert len(extra_rows) == 2, "the new function must land in both contexts"

        # Reindex half: the same file through reindex_file_impl.
        from fw_context_mcp.mcp.handlers.maintenance import reindex_file

        result = reindex_file("src/same.c", str(root), with_analysis=False)
        assert result.get("translation_units", 0) == 2, (
            "both compile actions of the file must be re-parsed"
        )
        mode_rows = _sym_rows(db_path, ch, "mode_fn")
        assert len(mode_rows) == 2, f"reindex must not lose per-context rows (got {mode_rows})"
        assert all(cid for cid, _ in mode_rows), (
            "no phantom canonical (context_id='') rows may replace per-context rows"
        )
        extra_rows = _sym_rows(db_path, ch, "extra_fn")
        assert len(extra_rows) == 2, (
            f"reindex must keep the new function in both contexts (got {extra_rows})"
        )
        assert _no_canonical_rows(db_path, ch, "symbols") == 0
        assert _no_canonical_rows(db_path, ch, "files") == 0

    def test_f01_header_refresh_covers_both_contexts(self, tmp_path: Path):
        """Touching a shared header must refresh every context that includes it.

        RED: reindex_file on the header re-parses exactly one including TU
        and stores it under the canonical context — the MODE=11 context keeps
        its stale body, the MODE=22 context is untouched, and a phantom
        canonical row appears holding the new text.
        """
        root, db_path, ch = _indexed_project(tmp_path, "red3_f01", _f01, _f01_commands)

        ctxs = _ctx_defines(db_path, ch)
        assert len(ctxs) == 2
        cid11 = next(c for c in ctxs if "-DMODE_VALUE=11" in ctxs[c])
        cid22 = next(c for c in ctxs if "-DMODE_VALUE=22" in ctxs[c])
        rows = dict(_sym_rows(db_path, ch, "role_value"))
        assert "return 11" in rows[cid11] and "return 22" in rows[cid22], (
            "both contexts must be indexed before the touch"
        )

        hdr = root / "src" / "common.hpp"
        hdr.write_text(
            "#if MODE_VALUE == 11\n"
            "int role_value(void) {\n"
            "    return 111;\n"
            "}\n"
            "#else\n"
            "int role_value(void) {\n"
            "    return 222;\n"
            "}\n"
            "#endif\n",
            encoding="utf-8",
        )
        _advance_mtime(hdr)

        from fw_context_mcp.mcp.handlers.maintenance import reindex_file

        result = reindex_file("src/common.hpp", str(root), with_analysis=False)
        assert result.get("translation_units", 0) == 2, (
            "both TUs that include the header must be re-parsed"
        )
        rows = dict(_sym_rows(db_path, ch, "role_value"))
        assert "" not in rows, "the refresh must not mint a phantom canonical row"
        assert set(rows) == {cid11, cid22}, (
            f"exactly the two real contexts must hold role_value (got {set(rows)})"
        )
        assert "return 111" in rows[cid11], (
            f"the MODE=11 context must see the new header text (got {rows.get(cid11)!r})"
        )
        assert "return 222" in rows[cid22], (
            f"the MODE=22 context must see the new header text (got {rows.get(cid22)!r})"
        )
        assert _no_canonical_rows(db_path, ch, "symbols") == 0

    def test_manifest_carryover_preserves_every_compile_action(self, tmp_path: Path):
        """Manifest carry-over must preserve one entry per compile action.

        RED: the file-keyed old-entry dict makes a carried-over pair
        last-wins; on a fresh index, a no-op rerun, and after an unrelated
        third TU joins the build, the two same.c entries collapse to the
        last one with a stale preliminary source_hash.
        """
        root, db_path, ch = _indexed_project(tmp_path, "red4_f02", _f02, _f02_commands)
        pid = _project_id(root)

        def assert_pair_preserved():
            entries = _entries_for(_manifest_entries(db_path, ch), "src/same.c")
            assert len(entries) == 2, (
                f"both compile actions must stay in the manifest (got {len(entries)})"
            )
            arg_sets = {tuple(e["args"]) for e in entries}
            assert len(arg_sets) == 2, (
                f"carry-over must not collapse the argument sets (got {arg_sets})"
            )
            assert any("-DMODE_VALUE=1" in e["args"] for e in entries), (
                "the MODE=1 compile action must keep its own entry"
            )
            assert any("-DMODE_VALUE=2" in e["args"] for e in entries), (
                "the MODE=2 compile action must keep its own entry"
            )

        assert_pair_preserved()
        _run_index(root, root / "compile_commands.json", db_path, pid)
        assert_pair_preserved()

        # R2-F: an unrelated third TU joins the build — the pair survives.
        (root / "src" / "third.c").write_text(
            "int third_fn(void) {\n    return 3;\n}\n", encoding="utf-8"
        )
        cmds = _f02_commands(root) + [
            {"file": "src/third.c", "args": ["-std=c11", "-Isrc"], "output": "build/third.o"}
        ]
        _write_cc(root, cmds)
        _run_index(root, root / "compile_commands.json", db_path, pid)
        assert_pair_preserved()
        third = _entries_for(_manifest_entries(db_path, ch), "src/third.c")
        assert len(third) == 1, "the new TU must gain exactly one entry"
        cur = compute_source_hash(root / "src" / "same.c")
        for e in _entries_for(_manifest_entries(db_path, ch), "src/same.c"):
            assert e["source_hash"] == cur, (
                "a re-parsed TU's entries must carry the real source hash, "
                f"not a stale preliminary one (got {e['source_hash']!r})"
            )

    def test_reindex_file_updates_all_matching_manifest_entries(self, tmp_path: Path):
        """reindex_file must refresh every manifest entry of the target file.

        RED: _update_manifest_after_reindex stops at the first entry whose
        file matches (break), so the second compile action's entry keeps its
        stale (here: empty preliminary) source_hash.
        """
        root, db_path, ch = _indexed_project(tmp_path, "red5_f02", _f02, _f02_commands)

        same = root / "src" / "same.c"
        same.write_text(
            same.read_text(encoding="utf-8")
            + "\nint extra_fn(void) {\n    return 9;\n}\n",
            encoding="utf-8",
        )
        _advance_mtime(same)

        from fw_context_mcp.mcp.handlers.maintenance import reindex_file

        reindex_file("src/same.c", str(root), with_analysis=False)

        entries = _entries_for(_manifest_entries(db_path, ch), "src/same.c")
        assert len(entries) == 2
        cur = compute_source_hash(same)
        stale = [e for e in entries if e["source_hash"] != cur]
        assert not stale, (
            "every manifest entry of the re-indexed file must be refreshed; "
            f"stale: {[(e['args'], e['source_hash'][:12]) for e in stale]} "
            f"(current {cur[:12]})"
        )


# ── GREEN cases (must pass after the fix) ────────────────────────────────────


def _canonical_snapshot(db_path: Path, ch: str) -> dict[str, list[tuple]]:
    """Deterministic per-context snapshot of every storage surface.

    canonical_snapshot_rule (orchestrator.yaml): sort deterministically,
    drop volatile row ids/timings, retain provenance and semantic values.
    ``files.content_hash`` is excluded on purpose: it embeds the manifest
    entry hash, which legitimately differs between a run that resolved the
    entry through manifest.json and a clean rebuild that hashed source-only
    (same exclusion as the SLICE-C suite, for the same reason).
    """
    conn = open_db(db_path)
    try:
        symbols = conn.execute(
            "SELECT usr, context_id, is_definition, source, line, file_path "
            "FROM symbols WHERE config_hash=? ORDER BY usr, context_id, line, file_path",
            (ch,),
        ).fetchall()
        refs = conn.execute(
            "SELECT to_usr, from_file, from_line, from_usr, ref_kind, context_id "
            "FROM refs WHERE config_hash=? "
            "ORDER BY to_usr, from_file, from_line, ref_kind, context_id",
            (ch,),
        ).fetchall()
        files = conn.execute(
            "SELECT path, context_id, source_hash, flags_hash "
            "FROM files WHERE config_hash=? ORDER BY path, context_id",
            (ch,),
        ).fetchall()
        contexts = conn.execute(
            "SELECT context_id, main_file, canonical_argv, response_hashes, "
            "output_name, defines_json, flags_hash "
            "FROM build_contexts WHERE config_hash=? ORDER BY context_id, entry_index",
            (ch,),
        ).fetchall()
        macros = conn.execute(
            "SELECT name, value, line, context_id FROM macros WHERE config_hash=? "
            "ORDER BY name, line, context_id",
            (ch,),
        ).fetchall()
    finally:
        conn.close()
    keys = ("symbols", "refs", "files", "contexts", "macros")
    vals = (symbols, refs, files, contexts, macros)
    return {k: [tuple(r) for r in v] for k, v in zip(keys, vals)}


class TestSliceDGreen:
    """GREEN = the post-fix contracts (O_INCREMENTAL_FULL_EQ and friends)."""

    @pytest.mark.parametrize("fixture,commands", [(_f01, _f01_commands), (_f02, _f02_commands)])
    def test_incremental_equals_clean_full_on_canonical_queries(
        self, tmp_path: Path, fixture, commands
    ):
        """O_INCREMENTAL_FULL_EQ (ADR-013): the verify hook.

        A full index followed by a mutation and an incremental run must land
        on the same canonical snapshot as a clean full index of the final
        tree.  Both DBs share the fixture root (config_hash is root-keyed,
        TU-list-free), so context ids are directly comparable.
        """
        # Incremental DB: full index, then mutate, then incremental run.
        root = tmp_path / "g1_tree"
        root.mkdir()
        fixture(root)
        cc = _write_cc(root, commands(root))
        index_dir = _isolate_index_db(root, tmp_path / "g1_incremental")
        from fw_context_mcp.config.settings import generate_project_id, _write_project_id

        pid = generate_project_id()
        _write_project_id(root, pid)
        db_path = index_dir / pid / "index.db"
        ch = _run_index(root, cc, db_path, pid)

        if fixture is _f01:
            (root / "src" / "common.hpp").write_text(
                "#if MODE_VALUE == 11\n"
                "int role_value(void) {\n"
                "    return 111;\n"
                "}\n"
                "#else\n"
                "int role_value(void) {\n"
                "    return 222;\n"
                "}\n"
                "#endif\n",
                encoding="utf-8",
            )
            _advance_mtime(root / "src" / "common.hpp")
        else:
            same = root / "src" / "same.c"
            same.write_text(
                same.read_text(encoding="utf-8")
                + "\nint extra_fn(void) {\n    return 9;\n}\n",
                encoding="utf-8",
            )
            _advance_mtime(same)
        _run_index(root, root / "compile_commands.json", db_path, pid)
        snap_inc = _canonical_snapshot(db_path, ch)

        # Clean full DB: same final tree, fresh index dir.
        index_dir2 = _isolate_index_db(root, tmp_path / "g1_clean")
        pid2 = generate_project_id()
        _write_project_id(root, pid2)
        db_path2 = index_dir2 / pid2 / "index.db"
        ch2 = _run_index(root, root / "compile_commands.json", db_path2, pid2)
        snap_full = _canonical_snapshot(db_path2, ch2)

        assert ch2 == ch, "the final tree must share one config_hash"
        for surface in snap_inc:
            assert snap_inc[surface] == snap_full[surface], (
                f"{surface}: incremental run must equal a clean full index "
                "on canonical queries"
            )

    def test_reindex_file_single_context_header_warning(self, tmp_path: Path):
        """The partial-coverage warning must be driven by the actual context set.

        After slave.c leaves compile_commands.json, a header re-index can
        refresh only master.c's context.  The result must warn with the real
        counts (1 of 2 includer contexts), refresh the reachable context,
        and leave the unreachable context's rows exactly as they were.
        """
        root, db_path, ch = _indexed_project(tmp_path, "g2_f01", _f01, _f01_commands)

        ctxs = _ctx_defines(db_path, ch)
        cid11 = next(c for c in ctxs if "-DMODE_VALUE=11" in ctxs[c])
        cid22 = next(c for c in ctxs if "-DMODE_VALUE=22" in ctxs[c])
        before22 = dict(_sym_rows(db_path, ch, "role_value"))[cid22]

        # slave.c drops out of the build (its context rows stay in the DB).
        _write_cc(root, _f01_commands(root)[:1])
        hdr = root / "src" / "common.hpp"
        hdr.write_text(
            "#if MODE_VALUE == 11\n"
            "int role_value(void) {\n"
            "    return 111;\n"
            "}\n"
            "#else\n"
            "int role_value(void) {\n"
            "    return 222;\n"
            "}\n"
            "#endif\n",
            encoding="utf-8",
        )
        _advance_mtime(hdr)

        from fw_context_mcp.mcp.handlers.maintenance import reindex_file

        result = reindex_file("src/common.hpp", str(root), with_analysis=False)

        warning = result.get("warning") or ""
        assert "1 of 2" in warning, (
            f"the warning must state the real coverage: refreshed 1 of 2 includer "
            f"contexts (got {warning!r})"
        )
        rows = dict(_sym_rows(db_path, ch, "role_value"))
        assert "" not in rows, "no phantom canonical row may appear"
        assert "return 111" in rows[cid11], "the reachable context must be refreshed"
        assert rows[cid22] == before22, (
            "the unreachable context's rows must be preserved untouched, "
            "not deleted and not half-refreshed"
        )

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Adjacent LIMITATION (T01 research R5, not a SLICE-D blocker): "
            "one-line definitions store source='' because body extraction "
            "requires a multi-line extent (ops.py end_line > start_line). "
            "Independent of context identity; documented, fixed separately."
        ),
    )
    def test_one_line_definitions_store_body(self, tmp_path: Path):
        """A one-line function definition should store its own body text."""
        root = tmp_path / "g3_oneline"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "tiny.c").write_text(
            "int tiny_fn(void) { return 1; }\n", encoding="utf-8"
        )
        cc = _write_cc(
            root, [{"file": "src/tiny.c", "args": ["-std=c11", "-Isrc"], "output": "build/t.o"}]
        )
        index_dir = _isolate_index_db(root, tmp_path / "g3_state")
        from fw_context_mcp.config.settings import generate_project_id, _write_project_id

        pid = generate_project_id()
        _write_project_id(root, pid)
        db_path = index_dir / pid / "index.db"
        ch = _run_index(root, cc, db_path, pid)
        rows = _sym_rows(db_path, ch, "tiny_fn")
        assert rows, "the one-line function must be indexed"
        assert any(body.strip() for _, body in rows), (
            "the definition row must carry the function body text"
        )

    def test_trust_rule_invariant_passes_on_win32(self):
        """The trust rule has one definition — checked with win32-safe paths.

        Repairs the premise of tests/test_manifest.py's
        TestTheTrustRuleHasOneDefinition on Windows: offender paths come back
        with backslash separators there, so the literal ``startswith(prefix
        + ":")`` scan falsely flags the allowlisted file itself.  Production
        is untouched; the scan normalizes separators instead.  The original
        test is left unmodified (documented in the SLICE-D receipt).
        """
        src_root = Path(__file__).resolve().parents[1] / "src"
        allow = {"fw_context_mcp/indexer/manifest.py"}
        offenders: list[str] = []
        for path in sorted(src_root.rglob("*.py")):
            rel = str(path.relative_to(src_root)).replace("\\", "/")
            if rel in allow:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if '.get("generated")' in line:
                    offenders.append(f"{rel}:{lineno}")
        assert not offenders, (
            'the trust rule must stay the single .get("generated") decision in '
            f"{sorted(allow)}; unexpected copies: {offenders}"
        )


# ── small local helper ───────────────────────────────────────────────────────


def _project_id(root: Path) -> str:
    from fw_context_mcp.config.settings import derive_project_id

    return derive_project_id(root)
