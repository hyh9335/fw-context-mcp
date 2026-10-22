"""Database schema definitions, migrations, and data backfills for fw-context-mcp.

This module defines the complete SQLite schema for the project index.
It handles three concerns that are usually split across separate modules:

1. **DDL**: CREATE TABLE/INDEX/TRIGGER statements in ``_SCHEMA``.
2. **Migration**: Column additions in ``_MIGRATION_ADD_COLUMNS`` and
   complex backfills in ``_run_data_migrations()``.
3. **Self-healing**: Critical-table re-creation and migration re-application
   on every connection open — the database must survive interrupted builds
   and partial schema upgrades.

── Schema versioning strategy ──

The schema version (``CURRENT_SCHEMA_VERSION``) is NOT a hand-maintained
integer.  It is a SHA-256 fingerprint of every CREATE TABLE column name
and every ALTER TABLE ADD COLUMN statement.  When a developer adds a new
column to ``_MIGRATION_ADD_COLUMNS``, the fingerprint changes automatically.
This eliminates the "forgot to bump the version constant" defect class.

── Table design rationale ──

Each table and index exists for a specific purpose.  The comments below
each CREATE statement explain:

- **Why the table exists** — the problem it solves for callers.
- **Why these column types** — TEXT over BLOB, INTEGER booleans, etc.
- **Why these indexes** — the query patterns each index accelerates.
- **UNIQUE constraints** — what constitutes a duplicate row and why.

── Migration strategy ──

Column additions are idempotent ALTER TABLE ADD COLUMN statements.
Complex data backfills (FTS5 rebuild, deduplication, composite PK upgrade)
run unconditionally on every ``open_db()`` call.  This guarantees that
a database last opened with an old fw-context version is automatically
brought to the current schema on first use.

Note: SQL DEFAULT (datetime('now')) captures per-row INSERT timestamps.
Rows inserted in the same transaction may share identical timestamps —
this is expected and harmless.  Use first_indexed_at for the earliest event.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3

log = logging.getLogger(__name__)

__all__ = [
    "CURRENT_ROW_FORMAT",
    "CURRENT_SCHEMA_VERSION",
    "ROW_FORMAT_PAIRED_WITH",
    "_ensure_column",
    "drop_fts_triggers",
    "missing_columns",
    "row_format_effect",
    "row_format_is_newer",
    "row_format_is_older",
]

# Which MEANING the stored text of a build carries.  Bump it when the same
# columns start to hold different text, and the staleness check then asks
# every index for one reindex.
#
# CURRENT_SCHEMA_VERSION cannot serve this purpose: it is a hash of the
# column set, thus it moves only when a column appears or goes.
#
# /1 — files.content and symbols.source hold only the lines that the
#      preprocessor took.  Before it, both held every #ifdef branch, thus
#      dead code reached every tool as live code.
# /2 — files.content holds the comments and the preprocessor directives of
#      every file.  Before it, one line was kept for each token — the line
#      the token starts on — and a header carried no token at all.  A block
#      comment thus lost its closing marker, and a reader took the live code
#      below it as commented out.
# /3 — per-context rows (T14 SLICE-B, ADR-005).  Every per-context table
#      (symbols, files, refs, macros, indirect_call_sites, fp_assignments)
#      carries context_id and its UNIQUE keys include it; build_contexts
#      registers the contexts.  A rows/2 index stores one observation per
#      USR with no compile-context identity, so old rows are migrated to
#      the canonical context (context_id='') — and build_configs keeps
#      row_format='fw-context-rows/2', which surfaces as reindex_needed
#      until a real reindex writes per-context-aware rows.
CURRENT_ROW_FORMAT = "fw-context-rows/3"

# The config-hash format that was current when CURRENT_ROW_FORMAT last moved.
#
# WHY this constant exists: a bump of CURRENT_ROW_FORMAT alone does not
# rewrite one row.  The staleness check asks for a reindex, but that run
# mints no new build config, and the content pass skips every file that
# already holds text.  `_step_finalize_manifest` then stamps the NEW format
# over rows that still hold the OLD text, and the check goes quiet for good
# over an index that answers with dead code.
#
# A new config hash is what really rewrites the rows: it leaves no row for
# the build, thus a plain `fw-context index` writes every file and every
# body again.  The two must therefore move together, and
# `test_row_format_invalidation.py` fails when only one of them does.
ROW_FORMAT_PAIRED_WITH = "fw-context-cc/5"


def _row_format_number(value: str) -> int | None:
    """Give the ordinal of a row format, or None when it has no readable one."""
    prefix, _, number = value.rpartition("/")
    if prefix != "fw-context-rows" or not number.isdigit():
        return None
    return int(number)


def row_format_is_older(stored: str) -> bool:
    """Say whether *stored* text carries an OLDER meaning than this version reads.

    Only this direction asks for a reindex, and the index run repairs it: the
    indexer writes the current format over the old rows.

    An absent format and a format this version cannot read both answer True.
    That is the safe direction — a reindex costs one run, and text of an
    unknown meaning costs every answer that quotes it.
    """
    stored_number = _row_format_number(stored)
    current_number = _row_format_number(CURRENT_ROW_FORMAT)
    if stored_number is None or current_number is None:
        return stored != CURRENT_ROW_FORMAT
    return stored_number < current_number


def row_format_is_newer(stored: str) -> bool:
    """Say whether *stored* text carries a NEWER meaning than this version reads.

    WHY this direction needs an answer of its own: a reindex cannot repair
    it.  The index is the correct one and the READER is the old one, thus the
    indexer writes the same new format again and the mismatch stays.  A
    staleness check that asked for a reindex here put a daemon into a loop:
    reindex, mismatch, reindex.

    The repair is to restart the LLM client — Claude Code, opencode, and
    every other one.  The MCP server is a child process of that client, thus
    it cannot restart alone.
    """
    stored_number = _row_format_number(stored)
    current_number = _row_format_number(CURRENT_ROW_FORMAT)
    if stored_number is None or current_number is None:
        return False
    return stored_number > current_number


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, type_def: str) -> None:
    """Add a column to a table if it does not exist (idempotent).

    Used as a guard in functions that write to migrated columns.  The
    ALTER TABLE DDL auto-commits when ``isolation_level=''`` (the default)
    so no explicit COMMIT is needed.

    Why this exists: a migration may have run for project A's database
    but not project B's.  Rather than track per-project migration state,
    each write-path checks whether the column exists and adds it if not.
    This is cheaper than a full schema-version check on every write.
    """
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise


def _ensure_migrated_columns(conn: sqlite3.Connection) -> None:
    """Apply all ALTER TABLE ADD COLUMN migrations (idempotent).

    Runs on every ``open_db()`` call, not only when the schema version is
    out of date.  This handles cases where the version was stamped but
    individual migrations were skipped or interrupted (power loss, killed
    process, partial upgrade).

    Why a fast path: on a fully migrated database this returns in
    sub-milliseconds because PRAGMA table_info is read-only and cached
    by SQLite.  The write transaction is only entered when columns are
    actually missing — avoiding contention on the write lock.
    """
    # Fast path — read-only, no write lock.
    for table, expected in _MIGRATED_COLUMNS.items():
        actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not expected.issubset(actual):
            break
    else:
        return  # all columns present

    # Slow path — some columns missing, apply migrations.
    for stmt in _MIGRATION_ADD_COLUMNS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e) and "no such table" not in str(e):
                raise

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check whether a table exists in the database.

    Why a separate function: several migration paths need to check for
    optional tables (embeddings, llm_analysis) that may not exist in
    indexes built before those features were added.  Using sqlite_master
    directly each time would duplicate the query pattern.
    """
    result = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return result is not None


def _parse_expected_columns(schema_sql: str, migration_statements: list[str]) -> dict[str, set[str]]:
    """Extract table→column mapping from CREATE and ALTER TABLE statements.

    Parses ``_SCHEMA`` for ``CREATE TABLE ... (col1, col2, ...)`` and the
    migration list for ``ALTER TABLE t ADD COLUMN c``.

    Why regex-based parsing instead of SQLite introspection: the function
    must derive the schema fingerprint BEFORE the database exists — it runs
    against the source strings themselves.  SQLite introspection would
    require an actual database file to query.

    Why virtual tables are excluded: they are rebuilt on every connection
    open, so their columns are not part of the structural fingerprint.
    """
    tables: dict[str, set[str]] = {}

    # Parse CREATE TABLE statements from _SCHEMA
    for match in re.finditer(
        r"CREATE TABLE.*?(\w+)\s*\((.*?)\);",
        schema_sql,
        re.DOTALL | re.IGNORECASE,
    ):
        table = match.group(1)
        if table.startswith("idx_"):
            continue
        body = match.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            col_name = line.split()[0].strip()
            if not col_name:
                continue
            if col_name.upper() in (
                "PRIMARY",
                "UNIQUE",
                "FOREIGN",
                "CHECK",
                "CONSTRAINT",
            ):
                continue
            if col_name.upper().startswith("UNIQUE"):
                continue  # UNIQUE(col, ...) constraint, not a column
            cols.add(col_name)
        tables[table] = cols

    # Parse ALTER TABLE ADD COLUMN from migration statements
    for stmt in migration_statements:
        alt_match = re.match(
            r"ALTER TABLE (\w+) ADD COLUMN (\w+)",
            stmt.strip(),
            re.IGNORECASE,
        )
        if alt_match:
            table, col = alt_match.group(1), alt_match.group(2)
            tables.setdefault(table, set()).add(col)

    return tables


def _derive_schema_version(
    schema_sql: str,
    migration_statements: list[str],
) -> int:
    """Compute a stable fingerprint of the full database schema.

    Derived from the actual ``_SCHEMA`` and ALTER TABLE migrations
    — NOT a hand-maintained integer.  When a developer adds an
    ALTER TABLE to ``_MIGRATION_ADD_COLUMNS``, the fingerprint
    changes automatically.

    Why SHA-256 instead of a sequential integer: eliminates the human
    error of forgetting to bump the version.  Two developers adding
    migrations to different branches will produce different fingerprints,
    which triggers a full reindex instead of silent schema drift.

    The result is masked to 31 bits (``& 0x7FFFFFFF``) to fit in a
    signed 32-bit SQLite INTEGER without overflow.
    """

    tables = _parse_expected_columns(schema_sql, migration_statements)
    canonical = "".join(f"{table}:{','.join(sorted(cols))};" for table, cols in sorted(tables.items()))
    return int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:4], "big") & 0x7FFFFFFF


# Simple add-column migrations — run idempotently and feed into the schema
# fingerprint.  Add new ALTER TABLE statements here and CURRENT_SCHEMA_VERSION
# changes automatically.  (Complex migrations with backfill logic live inline in
# open_db() — their ALTER TABLE must also be listed here so the fingerprint
# stays accurate.)
_MIGRATION_ADD_COLUMNS = [
    "ALTER TABLE files ADD COLUMN mtime REAL NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN end_line INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN file_path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN name_tokens TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN enum_value INTEGER",
    "ALTER TABLE symbols ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN inputs TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN outputs TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN is_virtual INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN is_pure_virtual INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN parent_usr TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN template_usr TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE symbols ADD COLUMN is_project INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE symbols ADD COLUMN pagerank REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE symbols ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE llm_analysis ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN embedding_dim INTEGER",
    "ALTER TABLE files ADD COLUMN content TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE files ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE files ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE files ADD COLUMN flags_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN manifest_verification TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE build_configs ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN first_indexed_at TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN analyze_vendor INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE files ADD COLUMN is_project INTEGER NOT NULL DEFAULT 0",
    # Multi-variant / multi-image build identity.  variant='' image='' board=''
    # for single-project builds (backward-compatible defaults).
    "ALTER TABLE build_configs ADD COLUMN variant TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN image TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE build_configs ADD COLUMN board TEXT NOT NULL DEFAULT ''",
    # Incremental re-embedding: content-addressable hash of the fields that
    # feed an embedding description.  Lets _build_embeddings skip unchanged
    # symbols instead of re-embedding the whole index on every `fw-context index`.
    "ALTER TABLE embeddings ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
    # Weakness of a definition.  A `.weak` symbol is one the linker drops
    # as soon as a strong definition of the same name exists, and the
    # vector table cannot be resolved without knowing which is which:
    # measured, ten slots across two projects reached no handler because
    # a CMSIS startup and an RTOS both defined the core exceptions and
    # the index saw no difference between them.
    "ALTER TABLE symbols ADD COLUMN is_weak INTEGER NOT NULL DEFAULT 0",
    # Position of a vector table slot, counting from zero and counting
    # the reserved entries.  Meaningful for ref_kind='vector' and
    # 'vector_data'; NULL everywhere else except 'runtime_vector', where
    # it is the IRQ NUMBER rather than a position — see the column
    # comment on refs.slot_index below.  What a position MEANS is
    # architecture knowledge the index deliberately does not decide: on
    # Cortex-M slot 15 is SysTick and 16+n is external IRQ n, on arm64 it
    # selects an exception class.  The position is a fact of the file.
    "ALTER TABLE refs ADD COLUMN slot_index INTEGER",
    # The entry point the linker script names with `ENTRY()`.  It belongs to
    # the build and not to a file, thus a column here rather than a symbol
    # row: a Cortex-M build says `ENTRY(Reset_Handler)` and a Zephyr build
    # says `ENTRY("__start")`, and neither fact is a property of any one
    # translation unit.  Empty when no script of the build names one.
    "ALTER TABLE build_configs ADD COLUMN entry_point TEXT NOT NULL DEFAULT ''",
    # Records CURRENT_ROW_FORMAT — which meaning the stored text carries.
    # See the comment on the column in _SCHEMA.  This column needs no
    # _schema_bump_ marker of its own: it is a real column, thus it moves
    # CURRENT_SCHEMA_VERSION by itself, and every existing index therefore
    # asks for the one reindex that fills it.
    "ALTER TABLE build_configs ADD COLUMN row_format TEXT NOT NULL DEFAULT ''",
    # ── rows/3 per-context columns (T14 SLICE-B, ADR-005) ─────────────────
    # These four tables gain context_id without a table rebuild.  The three
    # tables that NEED one (symbols, files, macros — their UNIQUE keys
    # change) are rebuilt by _run_structural_migration; their new columns
    # live in the _SCHEMA CREATE bodies, not here, because an ALTER ADD
    # COLUMN cannot add a UNIQUE constraint.
    "ALTER TABLE refs ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE refs ADD COLUMN context_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE indirect_call_sites ADD COLUMN context_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE indirect_call_sites ADD COLUMN certainty TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE indirect_call_sites ADD COLUMN evidence TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE indirect_call_sites ADD COLUMN order_index INTEGER",
    "ALTER TABLE fp_assignments ADD COLUMN context_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE fp_assignments ADD COLUMN certainty TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE fp_assignments ADD COLUMN evidence TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE fp_assignments ADD COLUMN order_index INTEGER",
    # Schema version bump — DO NOT REMOVE. When adding new migration steps after this
    # column, also add a NEW ALTER TABLE … ADD COLUMN _schema_bump_… line. The hash
    # of _MIGRATION_ADD_COLUMNS drives CURRENT_SCHEMA_VERSION.
    "ALTER TABLE build_configs ADD COLUMN _schema_bump_rows3_per_context INTEGER NOT NULL DEFAULT 0"  # schema version bump marker,
]

# Pre-computed {table: {columns}} from _MIGRATION_ADD_COLUMNS.
# Used by _ensure_migrated_columns() for a fast read-only check that
# skips the write transaction when all columns already exist.
_MIGRATED_COLUMNS: dict[str, set[str]] = {}
for _stmt in _MIGRATION_ADD_COLUMNS:
    _m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", _stmt, re.IGNORECASE)
    if _m:
        _MIGRATED_COLUMNS.setdefault(_m.group(1), set()).add(_m.group(2))

_SCHEMA = """
-- ── projects: multi-project registry ────────────────────────────────────
-- Each row is one indexed project.  Separate from build_configs so a single
-- project root can be re-indexed under different configs without duplicating
-- the project identity.  TEXT PRIMARY KEY (UUID4) avoids autoincrement
-- conflicts when merging databases.
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    root_path    TEXT NOT NULL
);

-- ── build_configs: one row per indexing run ─────────────────────────────
-- config_hash is a content-addressable fingerprint of the build configuration
-- (compile_commands.json path + compiler flags).  This lets the indexer
-- detect when the build config changed without comparing every file.
-- embedding_dim stores the dimension of the embedding model used during
-- indexing — needed to validate embeddings at query time.
-- manifest_verification tracks whether manifest.json exists (for header-
-- change detection).  first_indexed_at records the earliest index date
-- for staleness warnings.
CREATE TABLE IF NOT EXISTS build_configs (
    config_hash             TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(project_id),
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    compile_commands_path   TEXT NOT NULL,
    embedding_dim           INTEGER,
    manifest_verification   TEXT NOT NULL DEFAULT 'none',
    description             TEXT NOT NULL DEFAULT '',
    first_indexed_at        TEXT NOT NULL DEFAULT '',
    analyze_vendor          INTEGER NOT NULL DEFAULT 0,
    -- The `ENTRY()` of the linker script of this build, or '' when no
    -- script names one.  A property of the build, not of a file.
    entry_point             TEXT NOT NULL DEFAULT '',
    -- Which MEANING the stored text of this build carries, as
    -- CURRENT_ROW_FORMAT wrote it.  CURRENT_SCHEMA_VERSION cannot answer
    -- that question: it is a hash of the COLUMN SET, thus it moves only
    -- when a column appears or goes, and the meaning of a column can
    -- change while every name stays.  That happened once already — the
    -- text of files.content and symbols.source became ifdef-filtered —
    -- and no check noticed, thus every index went on serving dead code as
    -- live code until something else forced a reindex.  An empty string
    -- means a build indexed before this column existed.
    row_format              TEXT NOT NULL DEFAULT ''
);

-- ── build_contexts: the compile contexts of one build config ────────────
-- CRITICAL_TABLE: context identity — every per-context join needs this row.
-- One row per (config_hash, context_id).  A context is one entry of
-- compile_commands.json (ADR-001): the same TU listed twice with different
-- defines is two contexts of one build.  context_id is the truncated
-- 16-hex identity hash from ADR-001; the CANONICAL context uses the empty
-- string — rows written by pre-context code, and rows whose write path has
-- no context yet (T14 slices C..M fill them in), live there.
-- entry_index is the 0-based position of the entry in compile_commands.json
-- (0 for the canonical row); the argv/defines/flags columns record what
-- made the context, so a reader can name it without the original manifest.
CREATE TABLE IF NOT EXISTS build_contexts (
    id              INTEGER PRIMARY KEY,
    config_hash     TEXT NOT NULL REFERENCES build_configs(config_hash),
    context_id      TEXT NOT NULL,
    entry_index     INTEGER NOT NULL DEFAULT 0,
    main_file       TEXT NOT NULL DEFAULT '',
    compile_dir     TEXT NOT NULL DEFAULT '',
    canonical_argv  TEXT NOT NULL DEFAULT '',
    response_hashes TEXT NOT NULL DEFAULT '',
    output_name     TEXT NOT NULL DEFAULT '',
    defines_json    TEXT NOT NULL DEFAULT '',
    flags_hash      TEXT NOT NULL DEFAULT '',
    UNIQUE(config_hash, context_id)
);
CREATE INDEX IF NOT EXISTS idx_build_contexts_config ON build_contexts(config_hash);
CREATE INDEX IF NOT EXISTS idx_build_contexts_main   ON build_contexts(config_hash, main_file);

-- ── call_sites: direct call sites with resolution certainty ─────────────
-- CRITICAL_TABLE: the call-site query surface — loss breaks T05 tools.
-- One row per call expression the AST reader resolved.  certainty is
-- 'exact' | 'possible' | 'unresolved' (T05); resolution_kind names HOW the
-- target was resolved (direct, implicit_construct, dispatch, ...);
-- candidate_usrs is the JSON list of alternatives when certainty is not
-- 'exact'.  refs stays AST-proven (ADR-004): certainty lives here, not on
-- refs.  The canonical projection of "who calls whom" is a query-layer
-- view over this table, NOT a table of its own (ADR-002).
CREATE TABLE IF NOT EXISTS call_sites (
    id              INTEGER PRIMARY KEY,
    config_hash     TEXT    NOT NULL REFERENCES build_configs(config_hash),
    context_id      TEXT    NOT NULL DEFAULT '',
    from_file       TEXT    NOT NULL,
    from_line       INTEGER NOT NULL,
    from_col        INTEGER,
    from_usr        TEXT,
    certainty       TEXT    NOT NULL DEFAULT '',
    resolution_kind TEXT    NOT NULL DEFAULT '',
    target_usr      TEXT,
    candidate_usrs  TEXT    NOT NULL DEFAULT '',
    origin          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cs_caller ON call_sites(config_hash, from_usr);
CREATE INDEX IF NOT EXISTS idx_cs_target ON call_sites(config_hash, target_usr);

-- ── conditionals: preprocessor conditional provenance ───────────────────
-- CRITICAL_TABLE: the conditional provenance surface (ADR-013).
-- One row per #if/#ifdef/#ifndef/#elif/#else/#endif directive, keyed per
-- context (the same file under two defines sees different directives —
-- ADR-013 wins over a bare (config_hash, file_id, line) key).
-- directive is the keyword, condition the raw expression text (NULL for
-- #else/#endif), parent_id links a branch to its enclosing #if.
CREATE TABLE IF NOT EXISTS conditionals (
    id          INTEGER PRIMARY KEY,
    config_hash TEXT    NOT NULL REFERENCES build_configs(config_hash),
    context_id  TEXT    NOT NULL DEFAULT '',
    file_id     INTEGER NOT NULL REFERENCES files(id),
    line        INTEGER NOT NULL,
    directive   TEXT    NOT NULL,
    condition   TEXT,
    parent_id   INTEGER REFERENCES conditionals(id),
    UNIQUE(config_hash, file_id, line, context_id)
);
CREATE INDEX IF NOT EXISTS idx_conditionals_file ON conditionals(config_hash, file_id);
CREATE INDEX IF NOT EXISTS idx_conditionals_parent ON conditionals(parent_id);

-- ── conditional_branches: evaluation of one conditional ─────────────────
-- CRITICAL_TABLE: loss orphans the branch outcome of every conditional.
-- branch_kind is 'if' | 'elif' | 'else'; taken is -1 when unevaluated,
-- else 0/1 (the branch the preprocessor took for this context).
-- UNIQUE(conditional_id, line): one branch per line of a conditional.
CREATE TABLE IF NOT EXISTS conditional_branches (
    id             INTEGER PRIMARY KEY,
    conditional_id INTEGER NOT NULL REFERENCES conditionals(id),
    branch_kind    TEXT    NOT NULL,
    line           INTEGER NOT NULL,
    condition      TEXT,
    taken          INTEGER NOT NULL DEFAULT -1,
    UNIQUE(conditional_id, line)
);

-- ── conditional_macros: macros an evaluation depends on ─────────────────
-- CRITICAL_TABLE: loss hides which -D flag flips a conditional.
-- def_source is 'file' | 'command_line' | 'builtin' — where the macro was
-- defined; def_file/def_line locate file definitions.
CREATE TABLE IF NOT EXISTS conditional_macros (
    conditional_id INTEGER NOT NULL REFERENCES conditionals(id),
    macro_name     TEXT    NOT NULL,
    def_source     TEXT    NOT NULL DEFAULT '',
    def_file       TEXT    NOT NULL DEFAULT '',
    def_line       INTEGER,
    UNIQUE(conditional_id, macro_name)
);

-- ── file_directives: preprocessor directives per file ───────────────────
-- CRITICAL_TABLE: the directive channel surface (T12 / ADR-012).
-- One row per directive line the preprocessor saw in a project file
-- (#include/#define/#if/#pragma/...), keyed per build.  The optional FTS
-- below is created eagerly so later slices never reopen the schema
-- (freeze discipline: one consolidated bump).
CREATE TABLE IF NOT EXISTS file_directives (
    id          INTEGER PRIMARY KEY,
    config_hash TEXT    NOT NULL REFERENCES build_configs(config_hash),
    path        TEXT    NOT NULL,
    line        INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    text        TEXT    NOT NULL DEFAULT '',
    UNIQUE(config_hash, path, line, kind)
);
CREATE INDEX IF NOT EXISTS idx_file_directives_path ON file_directives(config_hash, path);
CREATE INDEX IF NOT EXISTS idx_file_directives_kind ON file_directives(config_hash, kind);

CREATE VIRTUAL TABLE IF NOT EXISTS file_directives_fts USING fts5(
    kind,
    text,
    content='file_directives',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS file_directives_ai AFTER INSERT ON file_directives BEGIN
    INSERT INTO file_directives_fts(rowid, kind, text)
    VALUES (new.id, new.kind, new.text);
END;

CREATE TRIGGER IF NOT EXISTS file_directives_ad AFTER DELETE ON file_directives BEGIN
    INSERT INTO file_directives_fts(file_directives_fts, rowid, kind, text)
    VALUES ('delete', old.id, old.kind, old.text);
END;

CREATE TRIGGER IF NOT EXISTS file_directives_au AFTER UPDATE ON file_directives BEGIN
    INSERT INTO file_directives_fts(file_directives_fts, rowid, kind, text)
    VALUES ('delete', old.id, old.kind, old.text);
    INSERT INTO file_directives_fts(rowid, kind, text)
    VALUES (new.id, new.kind, new.text);
END;

-- ── files: source files tracked during indexing ─────────────────────────
-- UNIQUE(config_hash, path, context_id) means one observation of a file
-- per compile context (rows/3): the same TU under two defines is two rows.
-- Rows written before per-context storage (and every write path that has
-- no context yet) use context_id = '' — the canonical context.
-- mtime is a REAL (Julian day fraction) so file-change detection uses
-- sub-second precision and supports comparison across OS boundaries.
-- language stores 'c' or 'cpp' — needed for libclang TU creation and
-- syntax-aware search filtering.
-- generated = 1 marks auto-generated files (e.g., protobuf output) that
-- should be excluded from dead-code analysis.
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT NOT NULL REFERENCES build_configs(config_hash),
    path         TEXT NOT NULL,
    language     TEXT NOT NULL,     -- 'c' | 'cpp'
    generated    INTEGER NOT NULL DEFAULT 0,
    is_project   INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    context_id   TEXT    NOT NULL DEFAULT '',
    UNIQUE(config_hash, path, context_id)
);

-- ── symbols: the core table — one row per clang Cursor ─────────────────
-- USR (Unified Symbol Resolution) is clang's stable identifier.  The
-- UNIQUE(config_hash, usr, context_id) constraint prevents the same symbol
-- observation from being inserted twice across different TUs (rows/3:
-- per-context rows; the canonical context is context_id = '').
-- TEXT columns over BLOB: SQLite TEXT is natively searchable via LIKE
-- and FTS5, while BLOB requires deserialization for every query.
-- INTEGER booleans (is_definition, is_virtual, is_template): SQLite has
-- no native BOOLEAN type; INTEGER NOT NULL DEFAULT 0 is idiomatic and
-- allows efficient WHERE clause filtering.
-- parent_usr links methods/fields to their containing class, enabling
-- class-member queries and inheritance traversal.
-- template_usr links specializations to the primary template, enabling
-- template instantiation discovery.
-- is_project distinguishes application code from vendor SDK — a per-symbol
-- flag rather than per-file because files may mix project and vendor symbols
-- (e.g., headers included by both).
-- pagerank is pre-computed call-graph centrality — stored on the symbol
-- so ranking queries do not need runtime graph traversal.
-- source contains the ifdef-filtered function body text — stored eagerly so
-- search_bodies queries hit the FTS5 index, not files on disk.  "Filtered"
-- means that a line of an inactive #if branch is a bare newline: the text
-- holds only the code that compiles for this build_config, and every line
-- that stays keeps its number.  The lines come from the record the
-- preprocessor made of the branches it skipped (indexer/skipped_ranges.py).
-- Rows written before fw-context-cc/3 hold the unfiltered text.
-- section / attr_flags (ADR-009, stage-1 storage): linker section the
-- symbol lands in and the attribute flag string (weak, alias, used, ...).
-- Empty until the symbol-attr slice fills them; the columns exist now so
-- the consolidated schema never reopens.
CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY,
    config_hash    TEXT    NOT NULL REFERENCES build_configs(config_hash),
    file_id        INTEGER NOT NULL REFERENCES files(id),
    file_path      TEXT    NOT NULL DEFAULT '',
    name_tokens    TEXT    NOT NULL DEFAULT '',
    usr            TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    qualified_name TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    line           INTEGER NOT NULL,
    col            INTEGER NOT NULL,
    end_line       INTEGER NOT NULL DEFAULT 0,
    is_definition  INTEGER NOT NULL DEFAULT 0,
    -- A definition another object may override: `.weak` in assembly.  The
    -- linker keeps the strong one, and without this the index cannot tell
    -- which that is.  Measured on the Mbed project and the second Mbed project, ten
    -- vector slots — HardFault_Handler, SysTick_Handler and the rest of
    -- the core exceptions — reached no handler because a CMSIS startup
    -- defines them weakly and an RTOS defines them properly, and both
    -- looked equally good.
    is_weak        INTEGER NOT NULL DEFAULT 0,
    signature      TEXT    NOT NULL DEFAULT '',
    docstring      TEXT    NOT NULL DEFAULT '',
    is_virtual     INTEGER NOT NULL DEFAULT 0,
    is_pure_virtual INTEGER NOT NULL DEFAULT 0,
    parent_usr     TEXT    NOT NULL DEFAULT '',
    is_template    INTEGER NOT NULL DEFAULT 0,
    template_usr   TEXT    NOT NULL DEFAULT '',
    is_project     INTEGER NOT NULL DEFAULT 0,
    pagerank       REAL    NOT NULL DEFAULT 0.0,
    source         TEXT    NOT NULL DEFAULT '',
    context_id     TEXT    NOT NULL DEFAULT '',
    section        TEXT    NOT NULL DEFAULT '',
    attr_flags     TEXT    NOT NULL DEFAULT '',
    UNIQUE(config_hash, usr, context_id)
);

-- ── Index rationale ─────────────────────────────────────────────────────
-- Each index exists because a specific query pattern would otherwise
-- require a full table scan over thousands of symbols.

-- idx_symbols_name: lookup_symbol() exact-name matching — the most frequent
-- query in the system.  Covers both bare names and prefix LIKE.
CREATE INDEX IF NOT EXISTS idx_symbols_name        ON symbols(name);

-- idx_symbols_qname: three-tier name resolution where qualified_name is
-- the second tier (exact match) and third tier (suffix LIKE).  Without
-- this index, every find_refs() call scans all symbols.
CREATE INDEX IF NOT EXISTS idx_symbols_qname       ON symbols(qualified_name);

-- idx_symbols_kind: filtered queries (search_code with kind='function',
-- find_dead_code with kind IN ('function','method',...)).  Also accelerates
-- get_file_map which groups symbols by kind.
CREATE INDEX IF NOT EXISTS idx_symbols_kind        ON symbols(kind);

-- idx_symbols_file: file-level queries — "give me all symbols in main.cpp".
-- Used by get_file_map and per-file reindex lookups.
CREATE INDEX IF NOT EXISTS idx_symbols_file        ON symbols(file_id);

-- idx_symbols_parent: class member queries — get_class_members and
-- inheritance traversal.  config_hash is the leading column because
-- every query is scoped to one build config.
CREATE INDEX IF NOT EXISTS idx_symbols_parent     ON symbols(config_hash, parent_usr);

-- idx_symbols_template: find_template_instances — resolves
-- specializations by template_usr.  Without this, every specialization
-- lookup scans the entire symbols table.
CREATE INDEX IF NOT EXISTS idx_symbols_template  ON symbols(config_hash, template_usr);

-- idx_symbols_filepath: file-path-based filtering (exclude vendor paths
-- in project_only queries).  Also supports search_content which filters
-- at the file level before joining with FTS5 results.
CREATE INDEX IF NOT EXISTS idx_symbols_filepath  ON symbols(config_hash, file_path);

-- idx_symbols_def_kind: find_dead_code and find_hotspots — both filter
-- on is_definition + kind.  A compound index avoids an intersection of
-- two separate indexes.  config_hash leads because all queries are
-- scoped to one build.
CREATE INDEX IF NOT EXISTS idx_symbols_def_kind   ON symbols(config_hash, is_definition, kind);

-- idx_files_config: joins between files and other tables on config_hash.
-- Every file-related query is config-scoped.
CREATE INDEX IF NOT EXISTS idx_files_config        ON files(config_hash);

-- idx_files_is_project: project_only file filtering for search_content
-- and per-file project/vendor classification.
CREATE INDEX IF NOT EXISTS idx_files_is_project    ON files(is_project);

-- idx_files_config_mtime: incremental indexing — finds files whose mtime
-- changed since the last index.  config_hash + path is the unique key,
-- and including mtime in the index makes change detection a covered query.
CREATE INDEX IF NOT EXISTS idx_files_config_mtime  ON files(config_hash, path, mtime);

-- ── symbols_fts: full-text search over symbol metadata ─────────────────
-- External content FTS5 table (content='symbols') — the FTS5 index stores
-- only the tokenized text, while the actual data lives in symbols.  This
-- avoids duplicating gigabytes of source text.
-- Triggers keep the FTS index in sync: insert → insert, delete → delete,
-- update → delete-old + insert-new.  These are dropped during bulk indexing
-- (via drop_fts_triggers) and recreated afterward for performance.
-- name_tokens is included so CamelCase/snake_case token searches work
-- without regex — "FileSystem" is indexed as "file system".
-- summary/inputs/outputs enable semantic LLM-generated descriptions to
-- participate in keyword search.
-- source enables search_bodies — the only FTS5 column that points at
-- function body text rather than metadata.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    signature,
    docstring,
    file_path,
    name_tokens,
    summary,
    inputs,
    outputs,
    source,
    content='symbols',
    content_rowid='id'
);

-- ── FTS5 content-sync triggers ──────────────────────────────────────────
-- Each trigger pattern follows the FTS5 external-content protocol:
-- INSERT → push the new row into the FTS index.
-- DELETE → push a 'delete' token with the old rowid (FTS5 removes it).
-- UPDATE → push a delete for the old row, then an insert for the new row.
-- This is idempotent because FTS5 merges token lists per rowid.
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, signature, docstring, file_path, name_tokens, summary, inputs, outputs, source)
    VALUES (new.id, new.name, new.qualified_name, new.signature, new.docstring, new.file_path, new.name_tokens, new.summary, new.inputs, new.outputs, new.source);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, signature, docstring, file_path, name_tokens, summary, inputs, outputs, source)
    VALUES ('delete', old.id, old.name, old.qualified_name, old.signature, old.docstring, old.file_path, old.name_tokens, old.summary, old.inputs, old.outputs, old.source);
END;

CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, signature, docstring, file_path, name_tokens, summary, inputs, outputs, source)
    VALUES ('delete', old.id, old.name, old.qualified_name, old.signature, old.docstring, old.file_path, old.name_tokens, old.summary, old.inputs, old.outputs, old.source);
    INSERT INTO symbols_fts(rowid, name, qualified_name, signature, docstring, file_path, name_tokens, summary, inputs, outputs, source)
    VALUES (new.id, new.name, new.qualified_name, new.signature, new.docstring, new.file_path, new.name_tokens, new.summary, new.inputs, new.outputs, new.source);
END;

-- ── files_fts: full-text search over ifdef-filtered file content ───────
-- External content FTS5 pointing at files.content.  Enables search_content
-- queries that find patterns in full file text (not limited to function
-- bodies).  Separate from symbols_fts because file-level and symbol-level
-- searches have different result granularity and ranking requirements.
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    path,
    content,
    content='files',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, path, content)
    VALUES (new.id, new.path, new.content);
END;

CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, path, content)
    VALUES ('delete', old.id, old.path, old.content);
END;

CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, path, content)
    VALUES ('delete', old.id, old.path, old.content);
    INSERT INTO files_fts(rowid, path, content)
    VALUES (new.id, new.path, new.content);
END;

-- ── refs: cross-reference / call-graph edges ────────────────────────────
-- Each row records one reference from source code to a symbol.  to_usr
-- points at the referenced definition; from_usr is the enclosing function
-- that contains the reference (NULL when at file scope — e.g. a global
-- variable initialization).
-- Why from_usr can be NULL: implicit constructor calls from global/static
-- objects lack an enclosing function.
-- Why no UNIQUE constraint initially: duplicates can occur when the same
-- TU is indexed twice.  A UNIQUE index is created during data migration
-- AFTER deduplication.
CREATE TABLE IF NOT EXISTS refs (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL,
    to_usr       TEXT    NOT NULL,
    from_file    TEXT    NOT NULL,
    from_line    INTEGER NOT NULL,
    from_usr     TEXT,
    ref_kind     TEXT    NOT NULL,
    -- Provenance of the row (ADR-008): which reader produced it — 'c' for
    -- the C AST reader, 'assembly' for the assembly vector-table reader,
    -- 'runtime' for the NVIC pass, 'indirect' for synthesized edges.  A
    -- VALUE only: refs stay AST-proven (ADR-004) — certainty lives on
    -- call_sites and the fp tables, never here.
    source       TEXT    NOT NULL DEFAULT '',
    -- Compile context of the observation (rows/3).  '' = canonical.
    context_id   TEXT    NOT NULL DEFAULT '',
    -- Position of the reference inside the construct that holds it, or NULL
    -- when it has none.  Three producers fill it: the assembly reader, for a
    -- slot of a vector table, and the C indexer, for an element of a
    -- positional one-dimensional array initializer.  Both mean an index
    -- counted from zero.
    --
    -- The third is the NVIC pass, for ref_kind='runtime_vector', and there
    -- it holds the IRQ NUMBER of the argument, not a position.  The slot is
    -- that number plus NVIC_USER_IRQ_OFFSET, which the reader takes from
    -- the macro table of the same index: adding it here would write a
    -- target constant into the data, and the number the build stated is
    -- the one worth keeping.
    --
    -- Note that idx_refs_unique, built during migration below, does not
    -- cover this column.  Two elements of one array that share a target, a
    -- line AND a from_usr therefore keep only the first.  A table at file
    -- scope has from_usr NULL, which SQLite treats as distinct, so a real
    -- vector table is not affected.
    slot_index   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_refs_to_usr   ON refs(config_hash, to_usr);
CREATE INDEX IF NOT EXISTS idx_refs_from_usr ON refs(config_hash, from_usr);
CREATE INDEX IF NOT EXISTS idx_refs_fromfile ON refs(config_hash, from_file);

-- ── indirect_call_sites: function pointer invocation tracking ───────────
-- Records every call through a function pointer (driver.onData(buf, len)).
-- Unlike refs, the callee is a FIELD_DECL or VAR_DECL — the actual function
-- called is resolved later by joining with fp_assignments (Phase 3).
-- Why a separate table: the callee has a different USR domain (field/variable)
-- than direct calls (function/method).  Mixing them in refs would require
-- nullable fields and complicate every query.
-- expr_text stores the source-code expression (e.g. "driver.onData") so
-- callers can see HOW the pointer was invoked.
-- fn_ptr_type stores the function pointer type string — used by Phase 3c
-- fallback when exact USR matching fails.
-- certainty / evidence / order_index (ADR-004, fp evidence slice):
-- 'exact' | 'possible' | 'unresolved', the evidence string backing the
-- call, and the positional index when the site sits inside an array of
-- invocations.  No UNIQUE key by design: one field can be invoked several
-- times from one line (documented at _refs.py insert path), so
-- delete-before-insert is the dedup mechanism.
CREATE TABLE IF NOT EXISTS indirect_call_sites (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL,
    from_file    TEXT    NOT NULL,
    from_line    INTEGER NOT NULL,
    from_usr     TEXT,
    expr_text    TEXT    NOT NULL DEFAULT '',
    target_usr   TEXT    NOT NULL,
    target_name  TEXT    NOT NULL DEFAULT '',
    fn_ptr_type  TEXT    NOT NULL DEFAULT '',
    context_id   TEXT    NOT NULL DEFAULT '',
    certainty    TEXT    NOT NULL DEFAULT '',
    evidence     TEXT    NOT NULL DEFAULT '',
    order_index  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ics_config_target ON indirect_call_sites(config_hash, target_usr);
CREATE INDEX IF NOT EXISTS idx_ics_config_file   ON indirect_call_sites(config_hash, from_file);
CREATE INDEX IF NOT EXISTS idx_ics_config_usr    ON indirect_call_sites(config_hash, from_usr);

-- ── fp_assignments: function pointer assignment records ─────────────────
-- Records both sides of "field = &function" so the linking phase can
-- connect fp_assignments.lhs_usr = indirect_call_sites.target_usr.
-- Why lhs and rhs columns: the LHS is the field/variable receiving the
-- pointer; the RHS is the function being assigned.  Both are needed for
-- the Phase 3 resolution: "which functions can be called through this field?"
-- method stores the assignment type: assignment, call_arg, var_init, init_list.
-- This distinction matters because call_arg assignments (callback(&fn, obj))
-- are resolved differently than direct assignments (field = &fn).
-- certainty / evidence / order_index (ADR-004): how sure the reader is that
-- this really is an assignment ('exact' | 'possible' | 'unresolved'), the
-- evidence string, and the positional index inside an initializer list.
-- rows/3: UNIQUE index idx_fp_assignments_unique (created right below)
-- includes context_id — the table itself had no key before, and the
-- INSERT OR IGNORE write path needs one to mean anything.
CREATE TABLE IF NOT EXISTS fp_assignments (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL,
    from_file    TEXT    NOT NULL,
    from_line    INTEGER NOT NULL,
    lhs_usr      TEXT    NOT NULL,
    lhs_name     TEXT    NOT NULL DEFAULT '',
    rhs_usr      TEXT    NOT NULL,
    rhs_name     TEXT    NOT NULL DEFAULT '',
    fn_ptr_type  TEXT    NOT NULL DEFAULT '',
    method       TEXT    NOT NULL,
    from_usr     TEXT,
    context_id   TEXT    NOT NULL DEFAULT '',
    certainty    TEXT    NOT NULL DEFAULT '',
    evidence     TEXT    NOT NULL DEFAULT '',
    order_index  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fp_assignments_unique
    ON fp_assignments(config_hash, from_file, from_line, lhs_usr, rhs_usr,
                      method, from_usr, context_id);
CREATE INDEX IF NOT EXISTS idx_fpa_config_lhs ON fp_assignments(config_hash, lhs_usr);
CREATE INDEX IF NOT EXISTS idx_fpa_config_rhs ON fp_assignments(config_hash, rhs_usr);
CREATE INDEX IF NOT EXISTS idx_fpa_config_file ON fp_assignments(config_hash, from_file);

-- ── embeddings: semantic search vectors ─────────────────────────────────
-- Stored as BLOB of float32 values packed with struct.pack('f', ...).
-- Why BLOB over TEXT: binary packing is 4 bytes per float vs ~10 bytes
-- for textual representation — 4x space savings for 1024-dim vectors.
-- Why ON DELETE CASCADE: when a symbol row is removed, its embeddings
-- are automatically cleaned up without needing a separate DELETE.
-- Why composite PK (symbol_id, chunk_index): supports multi-chunk
-- embeddings for models with output dimensions beyond SQLite's BLOB
-- size limits.  chunk_index = 0 for single-chunk embeddings.
-- Why content_hash: enables incremental re-embedding — a symbol is
-- re-embedded only when its source content changed.  Without this,
-- every `fw-context index` would regenerate all embeddings.
CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id    INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL DEFAULT 0,
    embedding    BLOB    NOT NULL,
    model        TEXT    NOT NULL,
    content_hash TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (symbol_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_symbol ON embeddings(symbol_id);

-- ── llm_analysis: pre-computed LLM symbol descriptions ──────────────────
-- Stores structured analysis (summary, inputs, outputs) generated by
-- an LLM during `fw-context index --analyze`.  1:1 with the symbols
-- table (symbol_id is PRIMARY KEY).
-- Why denormalized into symbols columns too: summary, inputs, and outputs
-- are also stored as columns on the symbols table for direct FTS5 search.
-- The llm_analysis table is the authoritative source; symbols columns
-- are a read-optimized copy updated during migration.
-- Why ON DELETE CASCADE: when a symbol is removed, its analysis must
-- also be removed — no orphan analysis rows.
CREATE TABLE IF NOT EXISTS llm_analysis (
    symbol_id    INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    summary      TEXT    NOT NULL DEFAULT '',
    inputs       TEXT    NOT NULL DEFAULT '',
    outputs      TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL,
    analyzed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    content_hash TEXT    NOT NULL DEFAULT ''
);


-- ── inheritance: C++ class hierarchy edges ──────────────────────────────
-- Each row is one base class of a derived class.  Multiple inheritance
-- produces multiple rows for the same derived_usr.
-- Why UNIQUE(config_hash, derived_usr, base_usr): the same inheritance
-- edge can be observed in multiple TUs — deduplication at INSERT time
-- is cheaper than post-processing.
-- Why access column: method override queries need to know visibility
-- (a private base class's public methods are not accessible).
-- Why is_virtual: virtual base classes in diamond inheritance are
-- only traversed once during BFS — ignoring virtual bases would create
-- duplicate ancestor paths.
CREATE TABLE IF NOT EXISTS inheritance (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL REFERENCES build_configs(config_hash),
    derived_usr  TEXT    NOT NULL,
    base_usr     TEXT    NOT NULL,
    access       TEXT    NOT NULL DEFAULT 'public',
    is_virtual   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(config_hash, derived_usr, base_usr)
);
CREATE INDEX IF NOT EXISTS idx_inheritance_derived ON inheritance(config_hash, derived_usr);
CREATE INDEX IF NOT EXISTS idx_inheritance_base    ON inheritance(config_hash, base_usr);

-- ── overrides: virtual method dispatch tracking ─────────────────────────
-- Each row records that derived_usr overrides base_usr.  Built as a
-- post-processing step after inheritance chains are indexed — the indexer
-- walks each class hierarchy and matches methods by name + signature.
-- Why a separate table from inheritance: inheritance is a class-level
-- relationship; overrides is a method-level relationship.  Mixing them
-- would require nullable columns and make both queries slower.
-- UNIQUE prevents the same override being recorded from multiple TUs.
CREATE TABLE IF NOT EXISTS overrides (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL REFERENCES build_configs(config_hash),
    derived_usr  TEXT    NOT NULL,
    base_usr     TEXT    NOT NULL,
    UNIQUE(config_hash, derived_usr, base_usr)
);
CREATE INDEX IF NOT EXISTS idx_overrides_derived ON overrides(config_hash, derived_usr);
CREATE INDEX IF NOT EXISTS idx_overrides_base    ON overrides(config_hash, base_usr);

-- ── macros: preprocessor macro definitions ──────────────────────────────
-- Collected via CXCursor_MacroDefinition during TU parsing.
-- rows/3 (T03 C4 / ADR-011): file_id is NULLABLE — a macro defined on the
-- command line (-DNAME=value) has no file row.  def_source names where it
-- came from: 'file' (in-source #define), 'command_line' (-D), or
-- 'builtin' (compiler-provided).  The UNIQUE key therefore includes
-- context_id and is enforced by the table constraint for file macros,
-- plus a PARTIAL unique index for command-line macros (a -D name is
-- unique per context; the partial index is not an ON CONFLICT target —
-- the command-line writer dedupes before insert).
-- Why expanded_value: populated by clang -dM -E — gives the preprocessor-
-- resolved value, which is different from the raw #define body (e.g.,
-- concatenation and stringification are applied).
-- Why is_function_like: distinguishes object-like from function-like
-- macros — needed because function-like macros participate in search
-- differently (they accept arguments).
-- Why line NOT NULL even for command-line macros: the -D writer stores 0;
-- the column keys file macros, and a nullable line would blur that.
CREATE TABLE IF NOT EXISTS macros (
    id               INTEGER PRIMARY KEY,
    config_hash      TEXT    NOT NULL REFERENCES build_configs(config_hash),
    file_id          INTEGER REFERENCES files(id),
    name             TEXT    NOT NULL,
    value            TEXT    NOT NULL DEFAULT '',
    expanded_value   TEXT    NOT NULL DEFAULT '',
    line             INTEGER NOT NULL,
    is_function_like INTEGER NOT NULL DEFAULT 0,
    context_id       TEXT    NOT NULL DEFAULT '',
    def_source       TEXT    NOT NULL DEFAULT '',
    UNIQUE(config_hash, file_id, line, context_id)
);
CREATE INDEX IF NOT EXISTS idx_macros_name ON macros(name, config_hash);
CREATE INDEX IF NOT EXISTS idx_macros_file ON macros(file_id);
-- Command-line macro identity: one -D name per (config, context).  NULL
-- file_id rows are distinct under the table constraint (SQLite UNIQUE
-- treats NULLs as distinct), so this partial index is what keeps them
-- single.  NOT an ON CONFLICT target — partial indexes cannot be.
CREATE UNIQUE INDEX IF NOT EXISTS idx_macros_unique_cmdline
    ON macros(config_hash, context_id, name) WHERE file_id IS NULL;

-- ── macros_fts: full-text search over macro definitions ─────────────────
-- Separate from symbols_fts because macros have a different content model
-- (name + value + expanded_value vs name + signature + docstring + source).
-- External content FTS5 with content='macros' — non-duplicating storage.
CREATE VIRTUAL TABLE IF NOT EXISTS macros_fts USING fts5(
    name,
    value,
    expanded_value,
    content='macros',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS macros_ai AFTER INSERT ON macros BEGIN
    INSERT INTO macros_fts(rowid, name, value, expanded_value)
    VALUES (new.id, new.name, new.value, new.expanded_value);
END;

CREATE TRIGGER IF NOT EXISTS macros_ad AFTER DELETE ON macros BEGIN
    INSERT INTO macros_fts(macros_fts, rowid, name, value, expanded_value)
    VALUES ('delete', old.id, old.name, old.value, old.expanded_value);
END;

CREATE TRIGGER IF NOT EXISTS macros_au AFTER UPDATE ON macros BEGIN
    INSERT INTO macros_fts(macros_fts, rowid, name, value, expanded_value)
    VALUES ('delete', old.id, old.name, old.value, old.expanded_value);
    INSERT INTO macros_fts(rowid, name, value, expanded_value)
    VALUES (new.id, new.name, new.value, new.expanded_value);
END;

-- ── hotspot_cache: pre-computed caller-count rankings ───────────────────
-- Stores caller_count for each symbol so find_hotspots() returns instantly.
-- Why a cache table instead of a live query: the live query requires a
-- COUNT(*) GROUP BY over the entire refs table (~1M+ rows).  Pre-computing
-- at index time eliminates the 2-5 second latency on every hotspot query.
-- Why UNIQUE(config_hash, symbol_id): one caller count per symbol per build.
-- Why ON DELETE CASCADE: when a symbol is removed, its cache entry is
-- automatically cleaned up.
CREATE TABLE IF NOT EXISTS hotspot_cache (
    id           INTEGER PRIMARY KEY,
    config_hash  TEXT    NOT NULL REFERENCES build_configs(config_hash),
    symbol_id    INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    caller_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(config_hash, symbol_id)
);
CREATE INDEX IF NOT EXISTS idx_hotspot_cache_config ON hotspot_cache(config_hash);

-- ── memory_regions: the MEMORY command of the linker script ─────────────
-- One row per region the linker script of this build declares.  The index
-- has no other source for a memory map: it is not in compile_commands.json
-- and no translation unit holds it.
--
-- Why keyed on config_hash: the map belongs to ONE build, not to a project.
-- Measured on the Zephyr project, which holds nine configurations in one
-- database: `app` starts at flash 372736, `mcuboot` at 110592, and
-- `app_flpr` declares no flash region at all.  A map stored per project
-- would be wrong for eight of the nine.
--
-- Why the raw text AND a number: the script writes an expression, and its
-- form differs by platform.  An mbed script writes `LENGTH = 0xefe00`, a
-- Zephyr script writes `LENGTH = ((673792) - 0xe6)`, which mixes decimal
-- and hexadecimal.  The text is always what the file says; the number is
-- filled in only when the expression is constant arithmetic, and stays
-- NULL for an expression that names a symbol such as
-- `ORIGIN(RAM) + LENGTH(RAM)`.  The index does not evaluate a symbol.
--
-- Why no `end` column: origin_value + length_value is the end, and storing
-- a derived value invites the two to disagree.
--
-- Why UNIQUE(config_hash, name): a MEMORY command names each region once.
--
-- CRITICAL_TABLE — and this annotation is load-bearing, not decoration.
-- CURRENT_SCHEMA_VERSION is a HASH of the column set, thus a schema change
-- can make it SMALLER.  `ensure_schema` compares with `<`: a stored version
-- ABOVE the current one takes the `elif` branch, which re-stamps the version
-- and never runs _SCHEMA.  `_ensure_migrated_columns` saves every ADD COLUMN
-- unconditionally, but a new TABLE has no such net.
--
-- Measured: adding this table moved the version from 1739388653 down to
-- 275729191, so every existing index re-stamped itself and never created
-- the table.  A full reindex of the second Mbed project then walked all 449
-- translation units and died on the last step with "no such table:
-- memory_regions".  The self-healing block runs on every open and closes
-- that hole.
CREATE TABLE IF NOT EXISTS memory_regions (
    id            INTEGER PRIMARY KEY,
    config_hash   TEXT    NOT NULL REFERENCES build_configs(config_hash),
    name          TEXT    NOT NULL,
    attributes    TEXT    NOT NULL DEFAULT '',
    origin        TEXT    NOT NULL,
    length        TEXT    NOT NULL,
    origin_value  INTEGER,
    length_value  INTEGER,
    file_path     TEXT    NOT NULL DEFAULT '',
    line          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(config_hash, name)
);
-- CRITICAL_TABLE — the extraction stops capturing when the parentheses of
-- the table close, thus the index needs a marker of its own.  It travels
-- with the table because a self-healed table without its index answers
-- correctly and slowly, and the next reader would have no way to tell
-- which of the two states the database is in.
--
-- NOTE for the next editor: never write the two words that open a table
-- definition inside a comment in this string.  `_parse_expected_columns`
-- looks for them with a regex, and a comment that holds them makes the
-- parser read the following statement as a table body.  Measured: this
-- very comment did it, and the fingerprint then held ONE column for
-- memory_regions instead of ten — so a later column would not have moved
-- the schema version at all.
CREATE INDEX IF NOT EXISTS idx_memory_regions_config ON memory_regions(config_hash);
"""

# ── Self-healing critical-tables block ───────────────────────────────────
# Executed on every connection open.  CREATE TABLE IF NOT EXISTS is
# idempotent — it takes microseconds when the table already exists.
# This guarantees that tables survive a crashed migration or partial
# index where some tables were created and others were not.
#
# Why auto-generated from _SCHEMA annotations: tables annotated with
# ``-- CRITICAL_TABLE`` in the comment immediately preceding their
# CREATE statement are automatically included.  This eliminates the
# need to manually synchronize two lists of table names.
#
# A table is critical if its loss would cause query failures rather
# than degraded results.  Tables like embeddings and hotspot_cache
# are NOT critical — they can be re-built from the core tables.


def _build_critical_tables() -> str:
    """Extract CRITICAL_TABLE-annotated CREATE statements from _SCHEMA.

    Every CREATE TABLE / CREATE INDEX whose preceding comment line
    contains ``CRITICAL_TABLE`` is included in the self-healing block.

    Why comment-annotation instead of a separate list: automatic
    extraction ensures the self-healing block is always up to date.
    When a developer adds a table, they only need to add the
    ``-- CRITICAL_TABLE`` comment — the extraction code finds it.
    """
    lines = _SCHEMA.split("\n")
    parts: list[str] = []
    capture = False
    paren_depth = 0
    for line in lines:
        stripped = line.strip()
        if "CRITICAL_TABLE" in stripped and stripped.startswith("--"):
            capture = True
            continue
        if capture:
            if stripped.upper().startswith("CREATE TABLE"):
                parts.append(line)
                paren_depth = line.count("(") - line.count(")")
            elif stripped.upper().startswith("CREATE INDEX"):
                parts.append(line)
            elif paren_depth > 0:
                parts.append(line)
                paren_depth += line.count("(") - line.count(")")
                if paren_depth == 0:
                    capture = False
            elif stripped == "" or stripped.startswith("--"):
                # Blank lines and comments between CRITICAL_TABLE marker
                # and the actual CREATE statement — keep capturing.
                pass
            else:
                capture = False
    result = "\n".join(parts).rstrip("\n") + "\n"
    return result


_CRITICAL_TABLES = _build_critical_tables()


CURRENT_SCHEMA_VERSION = _derive_schema_version(_SCHEMA, _MIGRATION_ADD_COLUMNS)

def missing_columns(conn: sqlite3.Connection) -> list[str]:
    """Return ``table.column`` of each column of this version that the database does not have.

    Only `open_db` of an index run adds the columns of
    ``_MIGRATION_ADD_COLUMNS``.  The MCP server reads the database without
    a migration, thus an index that an older version wrote lacks them, and
    a read of such a column fails with ``IndexError``.  The callers check
    this before they read a row, and name the old schema in place of the
    failure.

    WHY the columns and not CURRENT_SCHEMA_VERSION: the version is a hash
    and has no direction.  An index of a newer version has each column of
    this one, and a server that is older than the index must still answer
    (``client_restart_required``).  The ``_schema_bump_`` columns only move
    the version, and no code reads them.  A table that the database does
    not have is not checked: each version since the columns came creates
    every table.
    """
    missing: list[str] = []
    for table, columns in _MIGRATED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # nosec B608 — table names come from this module
        if present:
            missing.extend(
                f"{table}.{column}" for column in sorted(columns)
                if column not in present and not column.startswith("_schema_bump_")
            )
    return missing


def _run_data_migrations(conn: sqlite3.Connection) -> None:
    """Run idempotent data backfills under SAVEPOINT and FTS5 rebuild.

    Data backfills (file_path, name_tokens, llm_analysis denormalization)
    run inside a SAVEPOINT so any failure rolls back to the pre-migration
    state.  The FTS5 rebuild and embeddings migration run outside the
    SAVEPOINT because ``executescript()`` implicitly issues COMMIT.

    Why idempotent: this function runs on every ``open_db()`` call, not
    just on first use.  Each step checks whether it has work to do (e.g.,
    ``WHERE file_path = ''``) before executing.  This is cheaper than
    maintaining a separate migration-tracking table.

    Why SAVEPOINT over BEGIN/COMMIT: the caller holds a connection-level
    transaction.  SAVEPOINT nests within it, so a backfill failure only
    rolls back its own work, not the caller's transaction.

    Why FTS5 rebuild after SAVEPOINT: the rebuild function uses
    executescript() which issues an implicit COMMIT — SQLite does not
    allow this inside an active SAVEPOINT.
    """
    from fw_context_mcp.indexer.db import split_tokens

    conn.execute("SAVEPOINT data_migration")
    try:
        # ── file_path backfill ──────────────────────────────────────
        # Symbols indexed before file_path was added have empty strings.
        # Backfill from files.path via the file_id foreign key.
        # Why denormalized: file_path on every symbol avoids a JOIN to
        # files on every query — the most common query patterns
        # (search by name, filter by path) need it thousands of times.
        if conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE file_path = ''"
        ).fetchone()[0] > 0:
            conn.execute(
                """UPDATE symbols SET file_path = COALESCE(
                    (SELECT f.path FROM files f WHERE f.id = symbols.file_id), ''
                ) WHERE file_path = ''"""
            )
            conn.execute("RELEASE data_migration")
            conn.execute("SAVEPOINT data_migration")

        # ── name_tokens backfill ─────────────────────────────────────
        # name_tokens stores CamelCase/snake_case split tokens for FTS5
        # search.  Old indexes lack this column.  Backfill using the
        # split_tokens() function which decomposes identifiers into
        # searchable word tokens (e.g., "FileSystemInit" → "file system init").
        # Why stored: computing tokens at query time is too slow for FTS5
        # matching — FTS5 needs pre-tokenized text.
        # Why RELEASE + new SAVEPOINT between steps: long-running UPDATE
        # within a single SAVEPOINT can bloat the rollback journal.
        # Releasing periodically keeps memory usage bounded.
        if conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE name_tokens = ''"
        ).fetchone()[0] > 0:
            conn.executemany(
                "UPDATE symbols SET name_tokens = ? WHERE id = ?",
                (
                    (split_tokens(r["name"], r["qualified_name"]), r["id"])
                    for r in conn.execute(
                        "SELECT id, name, qualified_name FROM symbols WHERE name_tokens = ''"
                    )
                ),
            )
            conn.execute("RELEASE data_migration")
            conn.execute("SAVEPOINT data_migration")

        # ── LLM analysis denormalization ──────────────────────────────
        # summary/inputs/outputs are stored on both llm_analysis (authoritative)
        # and symbols (read-optimized copy).  This backfill copies existing
        # LLM analysis into the symbols columns so FTS5 search works
        # immediately after migration — no re-analysis needed.
        # Why denormalized: including these fields in symbols_fts means
        # search_code queries can match LLM-generated descriptions without
        # joining llm_analysis.
        if conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE summary = ''"
        ).fetchone()[0] > 0:
            conn.execute(
                """UPDATE symbols SET
                    summary = COALESCE(
                        (SELECT a.summary FROM llm_analysis a
                         WHERE a.symbol_id = symbols.id), ''),
                    inputs = COALESCE(
                        (SELECT a.inputs FROM llm_analysis a
                         WHERE a.symbol_id = symbols.id), ''),
                    outputs = COALESCE(
                        (SELECT a.outputs FROM llm_analysis a
                         WHERE a.symbol_id = symbols.id), '')
                WHERE id IN (SELECT symbol_id FROM llm_analysis)"""
            )
            conn.execute("RELEASE data_migration")
            conn.execute("SAVEPOINT data_migration")


        conn.execute("RELEASE data_migration")
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK TO data_migration")
        except sqlite3.OperationalError:
            pass  # SAVEPOINT may have already been released or never created
        raise

    # ── FTS5 rebuild (post-SAVEPOINT — executescript() issues COMMIT) ──
    # FTS5 tables can get out of sync when triggers were dropped during
    # bulk indexing but not recreated due to a crash.  Rebuilding here
    # guarantees a fresh, consistent FTS index on every open.
    conn.execute("DROP TABLE IF EXISTS file_analysis")
    from fw_context_mcp.indexer.db import rebuild_fts

    rebuild_fts(conn)

    # ── Deduplicate refs + create unique index ─────────────────────
    # Duplicate refs can accumulate when the same TU is indexed multiple
    # times (e.g., reindex after a header change).  The UNIQUE index
    # prevents future duplicates; this step cleans up existing ones.
    # Why MIN(rowid): keeps the first-inserted row — deterministic
    # and avoids choosing arbitrarily between identical duplicates.
    # rows/3: the key includes context_id — per-context rows are distinct
    # observations.  An index left over from rows/2 (no context_id) is
    # dropped and rebuilt by _run_structural_migration before this point,
    # so a surviving rows/2-shaped index can only appear on a DB whose
    # structural pass was interrupted — recreated here from scratch.
    idx_cols = [
        r["name"]
        for r in conn.execute("PRAGMA index_info(idx_refs_unique)").fetchall()
    ] if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_refs_unique'"
    ).fetchone() else []
    if idx_cols and "context_id" not in idx_cols:
        conn.execute("DROP INDEX idx_refs_unique")
        idx_cols = []
    if not idx_cols:
        dup_count = conn.execute(
            """SELECT COUNT(*) FROM refs WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM refs
                GROUP BY config_hash, to_usr, from_file, from_line, from_usr, ref_kind, context_id
            )"""
        ).fetchone()[0]
        if dup_count > 0:
            log.info("Deduplicating %d duplicate ref rows", dup_count)
            conn.execute(
                """DELETE FROM refs WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM refs
                    GROUP BY config_hash, to_usr, from_file, from_line, from_usr, ref_kind, context_id
                )"""
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_refs_unique "
            "ON refs(config_hash, to_usr, from_file, from_line, from_usr, ref_kind, context_id)"
        )

    # ── Embeddings composite PK migration ───────────────────────────
    # Delegates to _ensure_embeddings_schema — idempotent, safe to call
    # here (version-gated path) and from ensure_schema (unconditional).
    _ensure_embeddings_schema(conn)


def _ensure_embeddings_schema(conn: sqlite3.Connection) -> None:
    """Ensure the embeddings table has composite PK (symbol_id, chunk_index).

    Idempotent — checks PRAGMA table_info before rebuilding.  Safe to call
    unconditionally on every ``open_db()``, unlike the version-gated
    ``_run_data_migrations`` which only runs on schema version mismatch.

    Why separate from _run_data_migrations: when the schema bump marker
    was added before this migration code, databases that went through
    the version bump without running this rebuild were left with a
    single-column PK — missing ``chunk_index``.
    """
    if not _table_exists(conn, "embeddings"):
        return
    emb_cols = [
        r[1]
        for r in conn.execute(
            "PRAGMA table_info(embeddings)"
        ).fetchall()
    ]
    if "chunk_index" in emb_cols:
        return
    log.info(
        "Migrating embeddings table to composite PK "
        "(symbol_id, chunk_index)..."
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS embeddings_v2 (
                symbol_id    INTEGER NOT NULL
                    REFERENCES symbols(id) ON DELETE CASCADE,
                chunk_index  INTEGER NOT NULL DEFAULT 0,
                embedding    BLOB    NOT NULL,
                model        TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
                    DEFAULT (datetime('now')),
                PRIMARY KEY (symbol_id, chunk_index)
            )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO embeddings_v2
                (symbol_id, chunk_index, embedding, model, updated_at)
            SELECT symbol_id, 0, embedding, model, updated_at
            FROM embeddings"""
        )
        conn.execute("DROP TABLE IF EXISTS embeddings")
        conn.execute(
            "ALTER TABLE embeddings_v2 RENAME TO embeddings"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_symbol ON embeddings(symbol_id)"
        )
        conn.commit()
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


def drop_fts_triggers(conn: sqlite3.Connection) -> None:
    """Drop all FTS5 content-sync triggers for symbols, files, and macros.

    Called before bulk indexing to eliminate per-row FTS index overhead.
    When indexing 50,000+ symbols, each INSERT/UPDATE fires an FTS trigger
    that tokenizes and indexes the new text — this adds 30-50% to total
    indexing time.  Dropping the triggers defers FTS population until
    all TUs are processed, then ``rebuild_fts()`` populates everything
    in a single pass.

    The triggers are recreated by `rebuild_fts()` which calls
    ``CREATE TRIGGER IF NOT EXISTS`` — idempotent and safe to call
    after a crash.
    """
    conn.execute("DROP TRIGGER IF EXISTS symbols_ai")
    conn.execute("DROP TRIGGER IF EXISTS symbols_ad")
    conn.execute("DROP TRIGGER IF EXISTS symbols_au")
    conn.execute("DROP TRIGGER IF EXISTS files_ai")
    conn.execute("DROP TRIGGER IF EXISTS files_ad")
    conn.execute("DROP TRIGGER IF EXISTS files_au")
    conn.execute("DROP TRIGGER IF EXISTS macros_ai")
    conn.execute("DROP TRIGGER IF EXISTS macros_ad")
    conn.execute("DROP TRIGGER IF EXISTS macros_au")


# ── rows/2 → rows/3 structural migration (T14 SLICE-B, ADR-005) ──────────────
#
# ADR-005: ONE consolidated schema bump; old databases are NEVER silently
# migrated into the new per-context SEMANTICS.  What this pass does is a
# crash-safe STRUCTURAL copy: every pre-context row lands in the canonical
# context (context_id = '') under the rebuilt per-context keys, so the DB
# is readable and self-consistent — while build_configs.row_format stays
# 'fw-context-rows/2', which is the persistent marker that makes
# get_active_build report reindex_needed until a real reindex runs.
#
# Why shape-based detection and not the version hash: CURRENT_SCHEMA_VERSION
# is a HASH of the column set, not an ordinal — an old hash can compare
# greater than the new one, so `user_version < CURRENT` is unreliable across
# this bump.  The presence of symbols.context_id is the ground truth.


# New columns the rebuilt tables gain, with the literal that fills them for
# migrated rows.  Everything else in the new DDL already existed rows/2.
_ROWS3_COLUMN_DEFAULTS: dict[str, dict[str, str]] = {
    "symbols": {"context_id": "''", "section": "''", "attr_flags": "''"},
    "files": {"context_id": "''"},
    "macros": {"context_id": "''", "def_source": "''"},
}


def _migration_column_defaults() -> dict[str, dict[str, str]]:
    """{table: {column: default literal}} parsed from _MIGRATION_ADD_COLUMNS.

    Lets the structural copy fill a column an ancient DB never had (e.g.
    files.content_hash) with the same default the ALTER would have used.
    """
    out: dict[str, dict[str, str]] = {}
    for stmt in _MIGRATION_ADD_COLUMNS:
        m = re.match(
            r"ALTER TABLE (\w+) ADD COLUMN (\w+)\s+(.*)",
            stmt.strip(),
            re.IGNORECASE,
        )
        if not m:
            continue
        table, col, rest = m.group(1), m.group(2), m.group(3)
        dm = re.search(r"DEFAULT\s+('.*?'|[\w.+-]+)", rest, re.IGNORECASE)
        out.setdefault(table, {})[col] = dm.group(1) if dm else "NULL"
    return out


def _extract_create_table(table: str, new_name: str | None = None) -> str:
    """Pull one CREATE TABLE statement out of _SCHEMA.

    *new_name* renames the statement — the rebuild helpers create the
    new-shape table under a temporary name, copy, drop the old one, rename.
    Raises when the table has no CREATE in _SCHEMA: a silent CREATE of
    nothing would fail later in a far less readable place.
    """
    m = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table}\s*(\(.*?\n\);)",
        _SCHEMA,
        re.DOTALL,
    )
    if not m:
        raise RuntimeError(f"table {table!r} has no CREATE statement in _SCHEMA")
    body = m.group(1)
    if new_name is None:
        return f"CREATE TABLE IF NOT EXISTS {table} {body}"
    return f"CREATE TABLE IF NOT EXISTS {new_name} {body}"


def _needs_structural_migration(conn: sqlite3.Connection) -> bool:
    """True when *conn* holds a pre-per-context symbols table (rows/2 or older).

    The single authoritative shape probe: symbols is the first table every
    slice rebuilds, so its column set is the migration's ground truth.
    """
    if not _table_exists(conn, "symbols"):
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    return "context_id" not in cols


def _rebuild_table_for_rows3(conn: sqlite3.Connection, table: str) -> None:
    """Rebuild one table under its rows/3 keys, preserving ids and rows.

    Creates the new-shape table under a temporary name from the _SCHEMA
    statement itself (one source of truth — no duplicated DDL), copies the
    old rows with defaults for columns the old table lacked, then swaps.
    No-op when the table already carries its rows/3 columns — the helpers
    are independently idempotent, so a retry after a crash cannot double-
    rebuild or lose rows.
    """
    new_defaults = _ROWS3_COLUMN_DEFAULTS.get(table, {})
    shape_cols = set(new_defaults)
    old_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if shape_cols <= old_cols:
        return  # already rows/3
    tmp = f"{table}__rows3"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(_extract_create_table(table, tmp))
    new_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tmp})").fetchall()]
    defaults = dict(_migration_column_defaults().get(table, {}))
    defaults.update(new_defaults)
    select_exprs: list[str] = []
    for col in new_cols:
        if col in old_cols:
            select_exprs.append(f'"{col}"')
        elif col in defaults:
            select_exprs.append(defaults[col])
        else:
            raise RuntimeError(
                f"structural migration: column {table}.{col} exists in neither "
                "the old table nor the default map — refusing to guess"
            )
    col_list = ", ".join(f'"{c}"' for c in new_cols)
    conn.execute(
        f'INSERT INTO {tmp} ({col_list}) '
        f'SELECT {", ".join(select_exprs)} FROM "{table}"'
    )
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE {tmp} RENAME TO "{table}"')
    log.info("structural migration: rebuilt %s with per-context keys", table)


def _rebuild_symbols_per_context(conn: sqlite3.Connection) -> None:
    """symbols: UNIQUE (config_hash, usr) → (config_hash, usr, context_id)."""
    _rebuild_table_for_rows3(conn, "symbols")


def _rebuild_files_per_context(conn: sqlite3.Connection) -> None:
    """files: UNIQUE (config_hash, path) → (config_hash, path, context_id)."""
    _rebuild_table_for_rows3(conn, "files")


def _rebuild_macros_nullable_file(conn: sqlite3.Connection) -> None:
    """macros: file_id becomes nullable (command-line -D macros, ADR-011),
    the UNIQUE key gains context_id, and def_source arrives."""
    _rebuild_table_for_rows3(conn, "macros")


def _add_missing_rows3_columns(conn: sqlite3.Connection) -> None:
    """Add the rows/3 columns of the NOT-rebuilt tables (refs, fp tables).

    These tables change no UNIQUE key in the table body — an ALTER ADD
    COLUMN reaches them.  Skipped per column when present: idempotent.
    """
    for stmt in _MIGRATION_ADD_COLUMNS:
        m = re.match(
            r"ALTER TABLE (refs|indirect_call_sites|fp_assignments) ADD COLUMN ",
            stmt.strip(),
            re.IGNORECASE,
        )
        if not m:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _index_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    """Column list of an index, or [] when the index does not exist."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone():
        return []
    return [
        r["name"]
        for r in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    ]


def _rebuild_per_context_indexes(conn: sqlite3.Connection) -> None:
    """Dedup + (re)create the rows/3 unique indexes the write paths rely on.

    Runs inside the copy transaction: an index left in the rows/2 shape (or
    missing, as fp_assignments' was) would let the INSERT OR IGNORE paths
    duplicate rows on the very next index run.
    """
    # refs: idx_refs_unique gains context_id.
    if _table_exists(conn, "refs"):
        idx_cols = _index_columns(conn, "idx_refs_unique")
        if idx_cols and "context_id" not in idx_cols:
            conn.execute("DROP INDEX idx_refs_unique")
            idx_cols = []
        if not idx_cols:
            conn.execute(
                """DELETE FROM refs WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM refs
                    GROUP BY config_hash, to_usr, from_file, from_line,
                             from_usr, ref_kind, context_id
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_refs_unique "
                "ON refs(config_hash, to_usr, from_file, from_line, "
                "from_usr, ref_kind, context_id)"
            )
    # fp_assignments: gains its first unique key (the INSERT OR IGNORE write
    # path was a no-op without one).
    if _table_exists(conn, "fp_assignments"):
        idx_cols = _index_columns(conn, "idx_fp_assignments_unique")
        if idx_cols and "context_id" not in idx_cols:
            conn.execute("DROP INDEX idx_fp_assignments_unique")
            idx_cols = []
        if not idx_cols:
            conn.execute(
                """DELETE FROM fp_assignments WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM fp_assignments
                    GROUP BY config_hash, from_file, from_line, lhs_usr,
                             rhs_usr, method, from_usr, context_id
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_fp_assignments_unique "
                "ON fp_assignments(config_hash, from_file, from_line, "
                "lhs_usr, rhs_usr, method, from_usr, context_id)"
            )
    # macros: the command-line partial unique index (the file-macro key is a
    # table constraint of the rebuilt table).
    if _table_exists(conn, "macros"):
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_macros_unique_cmdline "
            "ON macros(config_hash, context_id, name) WHERE file_id IS NULL"
        )


def _seed_canonical_build_contexts(conn: sqlite3.Connection) -> None:
    """Register the canonical context (context_id='', entry_index=0) per build.

    One canonical row per build_configs row: the registered identity the
    migrated rows belong to.  Replaces a half-shaped build_contexts (e.g.
    one a crashed earlier attempt or a poisoned DB left behind) — the table
    is empty on every DB this migration legitimately runs on, because
    per-context rows and their contexts are written by later slices.
    """
    expected = {
        "id", "config_hash", "context_id", "entry_index", "main_file",
        "compile_dir", "canonical_argv", "response_hashes", "output_name",
        "defines_json", "flags_hash",
    }
    have: set[str] | None = None
    if _table_exists(conn, "build_contexts"):
        have = {r[1] for r in conn.execute("PRAGMA table_info(build_contexts)").fetchall()}
    if have is None or not expected <= have:
        conn.execute("DROP TABLE IF EXISTS build_contexts")
        conn.execute(_extract_create_table("build_contexts"))
    conn.execute(
        """INSERT OR IGNORE INTO build_contexts (config_hash, context_id, entry_index)
           SELECT config_hash, '', 0 FROM build_configs"""
    )


def _run_structural_migration(conn: sqlite3.Connection) -> None:
    """Copy a rows/2 (or older) database into the rows/3 shape.

    Phase 1 (ONE transaction, FKs off): rebuild symbols/files/macros under
    the per-context keys, ALTER the refs/fp tables, rebuild the unique
    indexes, seed canonical build_contexts rows.  A crash rolls the whole
    phase back — the DB stays old-shaped and old-stamped, never
    half-migrated-but-claimed-current.

    Phase 2 (after the commit): executescript(_SCHEMA) restores every
    index, trigger and FTS table the rebuilds dropped with their tables;
    the standard data-migration pass backfills anything older DBs still
    lack; the FTS indexes of the two rebuilt content tables are rebuilt.
    user_version is stamped LAST, only once the shape is complete.

    Deliberately does NOT touch build_configs.row_format: the stale
    rows/2 stamp is the ADR-005 marker that keeps the DB in
    reindex_needed until a real reindex runs.
    """
    log.info(
        "rows/2 -> rows/3 structural migration: copying existing rows into "
        "the canonical context (context_id=''); the build keeps its "
        "rows/2 row_format and stays in reindex_needed until reindexed"
    )
    conn.execute("PRAGMA foreign_keys = OFF")  # no-op inside a transaction — flip first
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _rebuild_files_per_context(conn)
            _rebuild_symbols_per_context(conn)
            _rebuild_macros_nullable_file(conn)
            _add_missing_rows3_columns(conn)
            _rebuild_per_context_indexes(conn)
            _seed_canonical_build_contexts(conn)
            conn.commit()
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    # Phase 2 — outside the copy transaction.  executescript() commits any
    # pending transaction, which is why it must not run inside phase 1.
    conn.executescript(_SCHEMA)
    _ensure_migrated_columns(conn)
    _run_data_migrations(conn)
    from fw_context_mcp.indexer.db import (  # lazy: avoids a circular import
        rebuild_files_fts,
        rebuild_macros_fts,
    )
    rebuild_files_fts(conn)
    rebuild_macros_fts(conn)
    conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    conn.commit()
    log.info("rows/2 -> rows/3 structural migration complete")


_ROW_FORMAT_FAULTS: tuple[tuple[int, str], ...] = (
    (0, "the stored text keeps inactive #ifdef branches, thus a body or a "
        "file can show code that does not compile"),
    (1, "a block comment in the stored text of a file lost its closing "
        "marker, thus live code below it reads as commented out"),
    (2, "the stored rows answer less than they should: a definition that "
        "begins and ends on one line has no body, no macro is marked "
        "function-like, no instance links to its template, and the value "
        "of a macro holds its parameter list glued to its replacement text"),
    (3, "a build with a GCC compiler was parsed with guessed toolchain "
        "headers and the macros of clang: an ESP32 or AVR index holds host "
        "headers from /usr/include and takes the #if branches of the host, "
        "an ARM index saw __GNUC__ 4, and the expanded value of a macro can "
        "be empty"),
    (4, "the stored text of each file that an assembly unit reads has blank "
        "directive lines, a short skipped block reads as live code, and a "
        "header that C code also reads lost its C-only lines: search_content "
        "and read_file can miss a #define or #include there"),
)


def row_format_effect(stored: str) -> str:
    """Say what an index whose text carries *stored* gets wrong.

    The answer names the OLDEST fault the text still has, because that is
    the one that misleads the reader most.  Text of an unreadable or absent
    format gets the oldest answer for the same reason: nothing says it is
    free of any fault.
    """
    number = _row_format_number(stored)
    for last_faulty, effect in _ROW_FORMAT_FAULTS:
        if number is None or number <= last_faulty:
            return effect
    return "the stored rows carry a meaning this version does not describe"
