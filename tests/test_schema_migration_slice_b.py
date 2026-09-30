"""T14 SLICE-B oracle: consolidated rows/3 schema and structural migration.

Frozen cases (TEST_PLAN §2, T02::test_spec.yaml):
- T02::schema_old_db_reports_reindex_needed — a DB written before the
  consolidated per-context schema (rows/2) must land in ``reindex_needed``
  when opened by new code, must never be silently migrated into per-context
  semantics, and its old rows must survive readable in the canonical context.
- T02::schema_fresh_db_end_to_end — a fresh DB must carry every table/column
  the accepted ADR storage set requires, and a real index run on it must work
  end to end and report ``ready``.

Plus the SLICE-B-specific oracles: interrupted-upgrade self-heal (a crash
mid-copy must leave the DB recoverable or cleanly reindex-required — never
half-migrated-but-claimed-current), reindex_needed surfacing through
``get_active_build``, and the G1 fresh-DB incremental==full equivalence.

The old-schema fixture is a REAL index DB captured with the d2666dc
indexer (the last rows/2-era build): ``tests/fixtures/legacy_rows2/`` —
checked in with its ``provenance.json`` (sha256, builder commit, schema
shape, expected counts) per AUD-R7/D7.  ``tests/test_legacy_migration.py``
pins the END-TO-END migration flow (read → reindex_needed, incremental
index → in-place rows/3 migration, data preserved) on the same fixture.
"""

from __future__ import annotations

import inspect
import shutil
import sqlite3
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import (
    CURRENT_ROW_FORMAT,
    CURRENT_SCHEMA_VERSION,
    insert_macros_batch,
    insert_refs_batch,
    insert_symbols_batch,
    open_db,
    transaction,
    upsert_build_config,
    upsert_file,
    upsert_project,
)
from fw_context_mcp.indexer.db import _schema as schema_module

# AUD-R7 (D7): the checked-in REAL rows/2 fixture — captured by the
# d2666dc indexer, sha256-pinned in provenance.json.  Not synthesized.
FIXTURE_DB = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "legacy_rows2" / "index.db"
)
OLD_PID = "implb-oldschema-fixture-0001"

requires_fixture = pytest.mark.skipif(
    not FIXTURE_DB.exists(),
    reason="checked-in rows/2 fixture missing (tests/fixtures/legacy_rows2)",
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _cols(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    """{column_name: {notnull, pk, type}} from PRAGMA table_info."""
    out: dict[str, dict] = {}
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        out[row["name"]] = {
            "notnull": row["notnull"],
            "pk": row["pk"],
            "type": row["type"],
        }
    return out


def _unique_index_columns(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    """Column lists of every UNIQUE index on *table*."""
    out = []
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if row["unique"]:
            cols = [
                r["name"]
                for r in conn.execute(f"PRAGMA index_info({row['name']})").fetchall()
            ]
            out.append(cols)
    return out


def _make_project_root(tmp_path: Path, project_id: str) -> Path:
    """A project root whose .fw-context/config.toml points at a temp index dir."""
    root = tmp_path / "proj"
    (root / ".fw-context").mkdir(parents=True)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    cc = root / "compile_commands.json"
    cc.write_text("[]", encoding="utf-8")
    (root / ".fw-context" / "config.toml").write_text(
        "[project]\n"
        f'id = "{project_id}"\n'
        "\n"
        "[index]\n"
        f'db_dir = "{str(index_dir).replace(chr(92), "/")}"\n'
        'compile_commands = "compile_commands.json"\n',
        encoding="utf-8",
    )
    (root / ".fw-context" / "local.toml").write_text("", encoding="utf-8")
    return root


def _old_db_copy(tmp_path: Path, project_id: str) -> Path:
    """Copy the captured old-schema fixture into a temp index layout.

    Rewrites the projects/build_configs identity to the temp layout so
    ``get_active_build(project_root=...)`` resolves THIS copy.  The stored
    compile_commands path is repointed at a real compile_commands.json the
    helper writes under the temp project — the checked-in fixture records
    the path of the (long-deleted) capture tree, and a copy that still
    named it would report a spurious ``compile_commands_missing`` on every
    host where that tree is gone (measured WSL vs Windows).
    """
    root = _make_project_root(tmp_path, project_id)
    index_dir = tmp_path / "index"
    db_path = index_dir / project_id / "index.db"
    db_path.parent.mkdir(parents=True)
    shutil.copy2(FIXTURE_DB, db_path)
    cc_path = root / "compile_commands.json"
    cc_path.write_text("[]", encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        created_at = conn.execute(
            "SELECT created_at FROM build_configs LIMIT 1"
        ).fetchone()["created_at"]
        # Backdate the cc file to the capture's indexed_at so the staleness
        # check sees "present and not newer" — a fresh mtime would report a
        # spurious compile_commands_newer.
        import os
        from datetime import datetime

        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        cc_time = dt.timestamp() - 10.0
        os.utime(cc_path, (cc_time, cc_time))
        conn.execute(
            "UPDATE projects SET project_id = ?, root_path = ?",
            (project_id, str(root)),
        )
        conn.execute(
            "UPDATE build_configs SET project_id = ?, compile_commands_path = ?",
            (project_id, str(cc_path)),
        )
    conn.close()
    return db_path


def _write_c_project(root: Path, entries: int = 1) -> Path:
    """Tiny C project (no gcc needed — libclang parses directly).

    *entries* = 2 writes the same TU twice with differing defines — the
    multi-context shape.  The G1 equivalence oracle uses ONE entry: two
    entries for the same file hit a pre-existing incremental defect (the
    second TU's replace_file_data wipes the first's rows), which is T02/T01
    territory and not a SLICE-B gate.
    """
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "handler.h").write_text(
        "#ifndef HANDLER_H\n"
        "#define HANDLER_H\n"
        "int run(int v);\n"
        "#endif\n",
        encoding="utf-8",
    )
    (src / "main.c").write_text(
        '#include "handler.h"\n'
        "\n"
        "#ifdef MODE_VALUE\n"
        "static int mode_check(int v) {\n"
        "    return v == MODE_VALUE;\n"
        "}\n"
        "#endif\n"
        "\n"
        "int run(int v) {\n"
        "    if (mode_check(v)) {\n"
        "        return 1;\n"
        "    }\n"
        "    return 22;\n"
        "}\n",
        encoding="utf-8",
    )
    entry = (
        '{{"directory": "{root}", "file": "src/main.c", "arguments": '
        '["gcc", "-std=c11", {defs}"-Isrc", "-c", "src/main.c", '
        '"-o", "build/a.o"]}}'
    )
    parts = [
        entry.format(
            root=str(root).replace(chr(92), "/"),
            defs='"-DMODE_VALUE=1", ' if i == 0 else "",
        )
        for i in range(entries)
    ]
    cc = root / "compile_commands.json"
    cc.write_text("[\n  " + ",\n  ".join(parts) + "\n]\n", encoding="utf-8")
    return cc


# ── fresh-DB consolidated shape ───────────────────────────────────────────────


class TestConsolidatedSchemaFreshDb:
    """T02::schema_fresh_db_end_to_end storage half: the frozen rows/3 shape."""

    def test_row_format_bumped_with_config_hash_pairing(self):
        assert CURRENT_ROW_FORMAT == "fw-context-rows/3", (
            "SLICE-B must move the row format to rows/3: the consolidated "
            "schema changes what per-context rows mean (ADR-005)"
        )
        from fw_context_mcp.indexer.db._schema import ROW_FORMAT_PAIRED_WITH

        assert ROW_FORMAT_PAIRED_WITH == "fw-context-cc/5", (
            "a row-format bump without a config-hash bump stamps the new "
            "format over old text that a reindex never rewrites — the two "
            "must move together (test_row_format_invalidation)"
        )
        source = inspect.getsource(
            __import__(
                "fw_context_mcp.indexer.manifest", fromlist=["compute_config_hash"]
            ).compute_config_hash
        )
        assert '"_format": "fw-context-cc/5"' in source, (
            "compute_config_hash must mint new config hashes (cc/5) so every "
            "old index gets one real reindex instead of a migrated reuse"
        )

    def test_new_consolidated_tables_exist(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        for table in (
            "build_contexts",       # ADR-001/002 context identity (T02)
            "call_sites",           # ADR-004 call-site uncertainty (T05)
            "conditionals",         # ADR-013 conditional provenance (T03)
            "conditional_branches", # ADR-013 per-branch evaluation
            "conditional_macros",   # ADR-013 macro inputs
            "file_directives",      # ADR-012 directive channel (T12)
            "file_directives_fts",  # ADR-012 optional FTS, frozen now
        ):
            assert table in tables, f"consolidated schema must create {table}"

    def test_per_context_columns_and_rebuilt_unique_keys(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            # context_id on every per-context table (ADR-001/002)
            for table in ("symbols", "files", "refs", "macros",
                          "indirect_call_sites", "fp_assignments"):
                cols = _cols(conn, table)
                assert "context_id" in cols, f"{table} must carry context_id"

            # per-context UNIQUE keys (ADR-005: rebuilt, not just added)
            sym_uniques = _unique_index_columns(conn, "symbols")
            assert ["config_hash", "usr", "context_id"] in sym_uniques, (
                f"symbols UNIQUE must be (config_hash, usr, context_id), got {sym_uniques}"
            )
            file_uniques = _unique_index_columns(conn, "files")
            assert ["config_hash", "path", "context_id"] in file_uniques, (
                f"files UNIQUE must be (config_hash, path, context_id), got {file_uniques}"
            )
            refs_uniques = _unique_index_columns(conn, "refs")
            assert any(
                "context_id" in cols for cols in refs_uniques
            ), f"idx_refs_unique must include context_id, got {refs_uniques}"
            fpa_uniques = _unique_index_columns(conn, "fp_assignments")
            assert fpa_uniques, (
                "fp_assignments must carry a UNIQUE key (its INSERT OR IGNORE "
                "is a no-op without one) including context_id"
            )
            assert any("context_id" in cols for cols in fpa_uniques)
            # indirect_call_sites deliberately has NO unique key: one field
            # can be invoked many times from one line (documented at
            # _refs.py:170-174) — the fp evidence slice keeps that.
        finally:
            conn.close()

    def test_macros_nullable_file_variant_with_command_line_key(self, tmp_path: Path):
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            cols = _cols(conn, "macros")
            assert cols["file_id"]["notnull"] == 0, (
                "macros.file_id must be nullable — a command-line -D macro has "
                "no file row (T03 C4 / ADR-011)"
            )
            assert "def_source" in cols, (
                "macros.def_source must exist ('command_line'|'builtin'|file)"
            )
            uniques = _unique_index_columns(conn, "macros")
            assert any(
                "file_id" in cols_ and "context_id" in cols_
                for cols_ in uniques
            ), f"file-macro UNIQUE must include context_id, got {uniques}"
        finally:
            conn.close()

    def test_fp_evidence_columns_exist(self, tmp_path: Path):
        """ADR-004: fp tables gain certainty/evidence (refs untouched)."""
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            for table in ("indirect_call_sites", "fp_assignments"):
                cols = _cols(conn, table)
                for column in ("certainty", "evidence", "order_index"):
                    assert column in cols, f"{table}.{column} must exist"
            assert "certainty" not in _cols(conn, "refs"), (
                "refs stay AST-proven; certainty lives on call_sites/fp tables"
            )
        finally:
            conn.close()

    def test_symbol_attr_section_columns_exist(self, tmp_path: Path):
        """ADR-009: symbol attr/section flag columns (stage-1 storage)."""
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            cols = _cols(conn, "symbols")
            for column in ("section", "attr_flags"):
                assert column in cols, f"symbols.{column} must exist (ADR-009)"
        finally:
            conn.close()

    def test_write_paths_survive_per_context_keys(self, tmp_path: Path):
        """Existing upserts must keep working (conflict targets follow the
        rebuilt keys; writes go to the canonical context until slice C)."""
        db_path = tmp_path / "index.db"
        conn = open_db(db_path)
        try:
            with transaction(conn):
                upsert_project(conn, "pid-sliceb", "p", str(tmp_path))
                upsert_build_config(conn, "ch-sliceb", "pid-sliceb", str(tmp_path / "cc.json"))
            fid = upsert_file(conn, "ch-sliceb", "a.c", "c")
            upsert_file(conn, "ch-sliceb", "a.c", "c", mtime=2.0)
            n_files = conn.execute(
                "SELECT COUNT(*) FROM files WHERE config_hash='ch-sliceb'"
            ).fetchone()[0]
            assert n_files == 1, "upsert_file must still dedupe on the rebuilt key"

            row = (
                "ch-sliceb", fid, "a.c", "", "u:decl", "f", "f", "function",
                1, 1, 0, 0, "int f(int)", "", None, 0, 0, "", 0, "", 0, 0.0, "", 0,
            )
            insert_symbols_batch(conn, [row])
            defn = row[:11] + (1,) + row[12:]
            insert_symbols_batch(conn, [defn])
            promoted = conn.execute(
                "SELECT is_definition, context_id FROM symbols WHERE usr='u:decl'"
            ).fetchone()
            assert promoted["is_definition"] == 1, "decl→def promotion must survive"
            assert promoted["context_id"] == "", "writes land in the canonical context"

            macro = ("ch-sliceb", fid, "MODE_VALUE", "1", "1", 3, 0)
            insert_macros_batch(conn, [macro, macro])
            n_macros = conn.execute(
                "SELECT COUNT(*) FROM macros WHERE config_hash='ch-sliceb'"
            ).fetchone()[0]
            assert n_macros == 1, "macro upsert must still dedupe on the rebuilt key"

            # from_usr NULL rows are DISTINCT under any SQLite unique index
            # (documented at the refs table comment), so a dedupe-proving
            # ref carries a real caller USR — as production refs do.
            ref = ("ch-sliceb", "u:decl", "a.c", 4, "u:caller", "call", None)
            insert_refs_batch(conn, [ref, ref])
            n_refs = conn.execute(
                "SELECT COUNT(*) FROM refs WHERE config_hash='ch-sliceb'"
            ).fetchone()[0]
            assert n_refs == 1, "INSERT OR IGNORE must dedupe refs per context"
        finally:
            conn.close()

    def test_fresh_db_end_to_end(self, tmp_path: Path):
        """T02::schema_fresh_db_end_to_end — real index run on a fresh DB."""
        from fw_context_mcp.indexer.runner import run

        root = tmp_path / "fresh-proj"
        root.mkdir()
        cc = _write_c_project(root, entries=2)
        pid = "sliceb-fresh-e2e"
        (root / ".fw-context").mkdir()
        (root / ".fw-context" / "config.toml").write_text(
            "[project]\n"
            f'id = "{pid}"\n'
            "\n[index]\n"
            f'db_dir = "{str(tmp_path / "index").replace(chr(92), "/")}"\n'
            'compile_commands = "compile_commands.json"\n',
            encoding="utf-8",
        )
        (root / ".fw-context" / "local.toml").write_text("", encoding="utf-8")

        config_hash = run(
            cc,
            tmp_path / "index" / pid / "index.db",
            project_root=root,
            project_id=pid,
            index_refs=True,
            index_embeddings=False,
            analyze_symbols=False,
        )
        assert config_hash

        conn = open_db(tmp_path / "index" / pid / "index.db")
        try:
            sym_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE config_hash=?", (config_hash,)
            ).fetchone()[0]
            assert sym_count >= 2, "the index run must store symbols"
            contexts = conn.execute(
                "SELECT DISTINCT context_id FROM symbols WHERE config_hash=?",
                (config_hash,),
            ).fetchall()
            # PIN SUPERSEDED by SLICE-C (T14 ADR-001/002): the real index run
            # now writes per-context rows.  "SLICE-B stores rows in the
            # canonical context only" held only until SLICE-C landed; the
            # frozen oracle this test keeps is that a fresh run indexes and
            # the schema keys work, not that writers stay context-blind.
            assert all(r["context_id"] != "" for r in contexts), (
                "the fresh e2e run must register real (non-canonical) contexts "
                "for its compile actions — one per TU (ADR-001)"
            )
            n_registered = conn.execute(
                "SELECT COUNT(*) FROM build_contexts WHERE config_hash=? "
                "AND context_id != ''",
                (config_hash,),
            ).fetchone()[0]
            assert n_registered >= 1, (
                "every compile action must register in build_contexts"
            )
            ref_count = conn.execute(
                "SELECT COUNT(*) FROM refs WHERE config_hash=?", (config_hash,)
            ).fetchone()[0]
            assert ref_count >= 1, "refs indexing must work on the new keys"
            row_format = conn.execute(
                "SELECT row_format FROM build_configs WHERE config_hash=?",
                (config_hash,),
            ).fetchone()["row_format"]
            assert row_format == CURRENT_ROW_FORMAT, (
                "a completed run stamps the current row format"
            )
        finally:
            conn.close()

        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        build = get_active_build(project_root=str(root))
        assert build["status"] == "ready", (
            f"a fresh, complete index must be ready, got {build.get('status')} "
            f"with reasons {build.get('reindex_reasons')}"
        )


# ── old-schema DB → reindex_required, never silent migration ─────────────────


class TestOldSchemaDbReportsReindexNeeded:
    """T02::schema_old_db_reports_reindex_needed (frozen oracle)."""

    @requires_fixture
    def test_old_db_reports_reindex_needed_and_keeps_canonical_rows(
        self, tmp_path: Path
    ):
        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        pid = "sliceb-old-db"
        db_path = _old_db_copy(tmp_path, pid)
        root = tmp_path / "proj"

        build = get_active_build(project_root=str(root))
        assert build["status"] == "reindex_needed", (
            f"an old rows/2 DB must ask for a reindex, got {build.get('status')} "
            f"with reasons {build.get('reindex_reasons')} — silent migration of "
            "semantic data is forbidden (ADR-005)"
        )
        assert any(
            "row_format_mismatch" in r for r in build.get("reindex_reasons", [])
        ), f"the rows/3 mismatch must be named, got {build.get('reindex_reasons')}"

        conn = open_db(db_path)
        try:
            # old rows survive, in the canonical context, once per USR
            symbols = conn.execute(
                "SELECT usr, COUNT(*) AS n FROM symbols GROUP BY usr, config_hash"
            ).fetchall()
            assert symbols, "old symbol rows must survive the structural pass"
            fabricated = conn.execute(
                "SELECT COUNT(DISTINCT context_id) AS n FROM symbols"
            ).fetchone()["n"]
            assert fabricated == 1, (
                "migration must NOT fabricate per-context rows: exactly one "
                "(canonical) context may exist"
            )
            n_ctx = conn.execute("SELECT COUNT(*) FROM build_contexts").fetchone()[0]
            assert n_ctx >= 1, (
                "the canonical context must be registered in build_contexts"
            )
            canonical = conn.execute(
                "SELECT COUNT(*) FROM build_contexts WHERE context_id=''"
            ).fetchone()[0]
            assert canonical >= 1, "canonical context row (context_id='') must exist"
            # readable: old body text is still answerable
            body = conn.execute(
                "SELECT source FROM symbols WHERE source != '' LIMIT 1"
            ).fetchone()
            assert body is not None, "old rows must stay readable after migration"
        finally:
            conn.close()

    @requires_fixture
    def test_reindex_simulation_clears_the_reason(self, tmp_path: Path):
        from fw_context_mcp.indexer.db import CURRENT_ROW_FORMAT
        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        pid = "sliceb-old-db-clears"
        db_path = _old_db_copy(tmp_path, pid)
        root = tmp_path / "proj"

        first = get_active_build(project_root=str(root))
        assert first["status"] == "reindex_needed"

        # simulate what a completed reindex writes (same stamp as _run_postprocess)
        conn = open_db(db_path)
        try:
            conn.execute(
                "UPDATE build_configs SET row_format = ?", (CURRENT_ROW_FORMAT,)
            )
            conn.commit()
        finally:
            conn.close()

        second = get_active_build(project_root=str(root))
        assert second["status"] == "ready", (
            f"after the reindex stamp the reason must clear, got "
            f"{second.get('status')} with {second.get('reindex_reasons')}"
        )

    @requires_fixture
    def test_pre_program_db_without_row_format_reports_reindex_needed(
        self, tmp_path: Path
    ):
        """A DB older than rows/2 (row_format='') requires reindex outright."""
        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        pid = "sliceb-pre-program"
        db_path = _old_db_copy(tmp_path, pid)
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute("UPDATE build_configs SET row_format = ''")
        conn.close()
        build = get_active_build(project_root=str(tmp_path / "proj"))
        assert build["status"] == "reindex_needed"
        assert any(
            "row_format_mismatch" in r for r in build.get("reindex_reasons", [])
        )


# ── interrupted-upgrade safety ────────────────────────────────────────────────


class TestStructuralMigrationSafety:
    """A crash mid-copy must never leave a half-migrated-but-claimed DB."""

    @requires_fixture
    def test_crash_mid_migration_is_atomic_and_recoverable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        pid = "sliceb-crash"
        db_path = _old_db_copy(tmp_path, pid)

        def _explode(conn):  # pragma: no cover - test injection
            raise RuntimeError("injected crash mid-copy")

        monkeypatch.setattr(schema_module, "_rebuild_files_per_context", _explode)
        with pytest.raises(Exception) as excinfo:
            open_db(db_path)
        assert "injected crash" in str(excinfo.value)
        monkeypatch.undo()

        # The DB must NOT claim to be current, and must still be the old shape
        # (the transaction rolled back) — never half-migrated-but-claimed.
        raw = sqlite3.connect(str(db_path))
        raw.row_factory = sqlite3.Row
        try:
            ver = raw.execute("PRAGMA user_version").fetchone()[0]
            sym_cols = {r[1] for r in raw.execute("PRAGMA table_info(symbols)")}
            assert "context_id" not in sym_cols or ver != CURRENT_SCHEMA_VERSION, (
                "a crashed migration must not leave the DB claiming the new "
                "schema version"
            )
        finally:
            raw.close()

        # recovery: the next open completes the migration cleanly
        conn = open_db(db_path)
        try:
            cols = _cols(conn, "symbols")
            assert "context_id" in cols, "recovery must complete the migration"
            n = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert n >= 1, "old rows survive the recovered migration"
        finally:
            conn.close()

    @requires_fixture
    def test_poisoned_half_migrated_db_never_claims_current(self, tmp_path: Path):
        """A DB stamped current but carrying old table shapes must self-heal."""
        pid = "sliceb-poisoned"
        db_path = _old_db_copy(tmp_path, pid)
        raw = sqlite3.connect(str(db_path))
        with raw:
            raw.execute(
                """CREATE TABLE IF NOT EXISTS build_contexts (
                        id INTEGER PRIMARY KEY,
                        config_hash TEXT NOT NULL,
                        context_id TEXT NOT NULL DEFAULT '',
                        entry_index INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            raw.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        raw.close()

        conn = open_db(db_path)
        try:
            # shape-based detection heals what the version stamp hid
            sym_cols = _cols(conn, "symbols")
            assert "context_id" in sym_cols, (
                "a DB stamped current with old table shapes must be detected "
                "by shape and upgraded, not trusted"
            )
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "call_sites" in tables
            n = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert n >= 1, "rows survive the healing pass"
        finally:
            conn.close()


# ── G1 fresh-DB: incremental equals clean full ────────────────────────────────


class TestG1FreshDbIncrementalEqualsFull:
    """G1 (O_INCREMENTAL_FULL_EQ) on a fresh DB, in-process (no gcc needed)."""

    @staticmethod
    def _canonical_rows(db_path: Path) -> dict:
        """Comparable snapshot — mirrors the accepted G1 harness
        (test_index_equivalence.py): symbol tuples, ref tuples, and the
        stored file CONTENT.  ``files.content_hash`` is deliberately NOT
        compared: a fresh run stores the source-only entry hash (no manifest
        exists while it indexes) and the next incremental run stores the
        manifest-based one — a pre-existing asymmetry that carries no
        semantic weight and is not a SLICE-B gate."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            symbols = [
                (r["usr"], r["name"], r["kind"], r["line"], r["col"],
                 r["end_line"], r["is_definition"], r["source"])
                for r in conn.execute("SELECT * FROM symbols ORDER BY usr, line")
            ]
            refs = [
                (r["to_usr"], r["from_file"], r["from_line"], r["from_usr"],
                 r["ref_kind"])
                for r in conn.execute("SELECT * FROM refs ORDER BY id")
            ]
            content = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT path, content FROM files "
                    "WHERE instr(path, ':') = 0 AND path NOT LIKE '/%'"
                )
            }
        finally:
            conn.close()
        return {"symbols": symbols, "refs": refs, "content": content}

    def test_incremental_equals_clean_full_after_source_edit(self, tmp_path: Path):
        from fw_context_mcp.indexer.runner import run

        def _index(root: Path, db: Path) -> None:
            run(
                root / "compile_commands.json",
                db,
                project_root=root,
                project_id="sliceb-g1",
                index_refs=True,
                index_embeddings=False,
                analyze_symbols=False,
            )

        inc_root = tmp_path / "g1-inc"
        inc_root.mkdir()
        _write_c_project(inc_root)
        inc_db = tmp_path / "g1-inc" / "index.db"
        _index(inc_root, inc_db)

        # source edit + mtime advance → incremental run must repair the rows
        main_c = inc_root / "src" / "main.c"
        main_c.write_text(
            main_c.read_text(encoding="utf-8")
            + "\nint tail_fn(int x) {\n    return x + 1;\n}\n",
            encoding="utf-8",
        )
        stamp = main_c.stat().st_mtime + 10
        import os

        os.utime(main_c, (stamp, stamp))
        _index(inc_root, inc_db)

        full_root = tmp_path / "g1-full"
        full_root.mkdir()
        _write_c_project(full_root)
        shutil.copy2(main_c, full_root / "src" / "main.c")
        os.utime(full_root / "src" / "main.c", (stamp, stamp))
        full_db = tmp_path / "g1-full" / "index.db"
        _index(full_root, full_db)

        inc = self._canonical_rows(inc_db)
        full = self._canonical_rows(full_db)

        assert inc["symbols"] == full["symbols"], (
            "incremental symbols must equal a clean rebuild after the edit"
        )
        assert inc["refs"] == full["refs"], (
            "incremental refs must equal a clean rebuild after the edit"
        )
        assert inc["content"] == full["content"], (
            "incremental stored content must equal a clean rebuild after the edit"
        )
        assert any("tail_fn" in s[1] for s in inc["symbols"]), (
            "the edited-in function must exist on the incremental side"
        )
