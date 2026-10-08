"""M6 shadow store schema — Phase I shared-fact tables (Architecture Contract FROZEN).

These tables are SHADOW tables — they run alongside the existing v2
whole-TU store (schema.py). They are populated during indexing but
NOT used by production queries until shadow differential testing
(§34) proves QUERY_PARITY_FAILURES == 0.

See: .dsh-control/contracts/M6_ARCHITECTURE_CONTRACT.md (FROZEN)

Design principles (§2 of the contract):
1. Canonical fact identity (prototype_a.py canon functions)
2. Interned BuildSets (frozenset → buildset_id)
3. Physical fact bundles grouped by (fact_kind, buildset_id)
4. Reachability-based GC (no age-only deletion) — §M1
5. Three non-collapsible concepts: identity / presence / certificate

Fact-category matrix (§1 of the contract):
  Symbol, Function, Variable, Type, Field, EnumConst, Call, Inherits — Phase R ✅
  Reference (6 ref_kinds), ConditionalState, Body, FileContent — Phase I (I4/I5/I8)

Tables:
  m6_buildsets        — interned BuildSets (buildset_id, builds_hash, build_names_json)
  m6_shared_facts     — canonical facts (fact_canon, fact_kind, payload_json, buildset_id)
  m6_fact_bundles     — physical bundles (bundle_id, fact_kind, buildset_id, fact_count)
  m6_world_builds     — World→Builds many-to-many map (§M1.1, GC reachability)
  m6_world_fact_refs  — per-world fact membership (legacy, will be deprecated by m6_world_builds)
  m6_certificates     — dependency certificates for materialization reuse (§M3)
  m6_body_facts       — function/source body text facts (I4)
  m6_file_content     — ifdef-filtered file content facts (I4)
"""
from __future__ import annotations

M6_SCHEMA_VERSION = 2  # bumped: +m6_world_builds, +m6_file_content, fact_kind enum expanded

M6_DDL_STATEMENTS: tuple[str, ...] = (
    # ── Interned BuildSets ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS m6_buildsets (
      buildset_id     INTEGER PRIMARY KEY,
      builds_hash     TEXT NOT NULL UNIQUE,    -- SHA-256 over sorted build_name list
      build_count     INTEGER NOT NULL,
      build_names_json TEXT NOT NULL,           -- JSON array of build names
      created_at      TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_buildsets_hash ON m6_buildsets(builds_hash)
    """,

    # ── Canonical shared facts ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS m6_shared_facts (
      fact_id         INTEGER PRIMARY KEY,
      fact_canon      TEXT NOT NULL UNIQUE,    -- canonical identity string
      fact_kind       TEXT NOT NULL,            -- Symbol|Function|Call|Variable|Type|Field|EnumConst|Inherits|Reference|ConditionalState|Body|FileContent
      buildset_id     INTEGER NOT NULL,        -- → m6_buildsets
      payload_json    TEXT,                     -- fact-specific payload (signature, type, etc.)
      first_seen_at   TEXT,
      FOREIGN KEY(buildset_id) REFERENCES m6_buildsets(buildset_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_facts_canon ON m6_shared_facts(fact_canon)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_facts_kind ON m6_shared_facts(fact_kind)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_facts_buildset ON m6_shared_facts(buildset_id)
    """,

    # ── Physical fact bundles (optimization: group by kind+buildset) ────────
    """
    CREATE TABLE IF NOT EXISTS m6_fact_bundles (
      bundle_id       INTEGER PRIMARY KEY,
      fact_kind       TEXT NOT NULL,
      buildset_id     INTEGER NOT NULL,
      fact_count      INTEGER NOT NULL,
      fact_ids_json   TEXT NOT NULL,            -- JSON array of fact_ids in this bundle
      created_at      TEXT,
      UNIQUE(fact_kind, buildset_id),
      FOREIGN KEY(buildset_id) REFERENCES m6_buildsets(buildset_id)
    )
    """,

    # ── World→Builds map (§M1.1 — GC reachability foundation) ───────────────
    # A World encompasses one or more Builds; a Build may belong to multiple
    # Worlds. Per-build refcount = COUNT(world_id) WHERE world is active.
    # Fact reachability = fact.BuildSet ∩ {builds with refcount > 0} ≠ ∅.
    """
    CREATE TABLE IF NOT EXISTS m6_world_builds (
      world_id        INTEGER NOT NULL,         -- → snapshot_builds.world_id
      build_name      TEXT NOT NULL,             -- build name (e.g., "nrf52840", "stage0")
      PRIMARY KEY(world_id, build_name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_world_builds_build ON m6_world_builds(build_name)
    """,

    # ── Per-world fact membership (legacy GC path; will be replaced by
    #    m6_world_builds + BuildSet intersection per §M1.3) ──────────────────
    """
    CREATE TABLE IF NOT EXISTS m6_world_fact_refs (
      world_id        INTEGER NOT NULL,         -- → snapshot_builds.world_id
      fact_id         INTEGER NOT NULL,         -- → m6_shared_facts.fact_id
      PRIMARY KEY(world_id, fact_id),
      FOREIGN KEY(fact_id) REFERENCES m6_shared_facts(fact_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_world_refs_world ON m6_world_fact_refs(world_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_world_refs_fact ON m6_world_fact_refs(fact_id)
    """,

    # ── Dependency certificates (§M3 — materialization reuse) ──────────────
    # Five legs: L0 (file bytes), L1 (include closure), L2 (macro state),
    # MacroEnv (stateful macros: __COUNTER__/__DATE__/__TIME__),
    # L3 (toolchain: -std, -fexceptions, -frtti).
    # fact_set_digest is the defense-in-depth correctness check (§M3.4).
    """
    CREATE TABLE IF NOT EXISTS m6_certificates (
      cert_id             INTEGER PRIMARY KEY,
      source_blob_sha     TEXT NOT NULL,        -- L0: file bytes hash
      ncc_digest_sha      TEXT NOT NULL,        -- L1: include closure hash
      closure_digest_sha  TEXT NOT NULL,        -- L2: macro state hash
      macro_env_digest    TEXT NOT NULL,        -- MacroEnv: stateful macro expansion results
      toolchain_id        TEXT NOT NULL,        -- L3: toolchain flags hash
      cert_hash           TEXT NOT NULL UNIQUE, -- SHA-256 over all 5 legs
      fact_set_digest     TEXT,                 -- digest over all facts produced (completeness check, §M3.4)
      fact_count          INTEGER,
      created_at          TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_certs_hash ON m6_certificates(cert_hash)
    """,

    # ── Body/source text facts (I4 — ifdef-filtered per build) ─────────────
    """
    CREATE TABLE IF NOT EXISTS m6_body_facts (
      body_id         INTEGER PRIMARY KEY,
      symbol_usr      TEXT NOT NULL,            -- the symbol this body belongs to
      buildset_id     INTEGER NOT NULL,         -- → m6_buildsets
      body_text       TEXT NOT NULL,             -- the ifdef-filtered source body text
      body_hash       TEXT NOT NULL,            -- SHA-256 over body_text (canonical identity)
      line_start      INTEGER,
      line_end        INTEGER,
      first_seen_at   TEXT,
      FOREIGN KEY(buildset_id) REFERENCES m6_buildsets(buildset_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_body_usr ON m6_body_facts(symbol_usr)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_body_hash ON m6_body_facts(body_hash)
    """,

    # ── File content facts (I4 — ifdef-filtered full file content) ──────────
    """
    CREATE TABLE IF NOT EXISTS m6_file_content (
      content_id      INTEGER PRIMARY KEY,
      file_path       TEXT NOT NULL,             -- path relative to project root
      buildset_id     INTEGER NOT NULL,         -- → m6_buildsets
      content_text    TEXT NOT NULL,             -- the ifdef-filtered full file content
      content_hash    TEXT NOT NULL,            -- SHA-256 over content_text (canonical identity)
      line_count      INTEGER,
      first_seen_at   TEXT,
      FOREIGN KEY(buildset_id) REFERENCES m6_buildsets(buildset_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_file_content_path ON m6_file_content(file_path)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_file_content_hash ON m6_file_content(content_hash)
    """,

    # ── M6 store metadata ──────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS m6_store_metadata (
      key             TEXT PRIMARY KEY,
      value           TEXT NOT NULL
    )
    """,
)


def get_m6_schema_version() -> int:
    return M6_SCHEMA_VERSION


def init_m6_tables(conn) -> None:
    """Create M6 shadow tables in the given SQLite connection.

    Idempotent: uses CREATE TABLE IF NOT EXISTS, so existing tables are not
    recreated. The m6_store_metadata row marks this as a shadow store.
    """
    conn.executescript(";\n".join(M6_DDL_STATEMENTS))
    conn.execute(
        "INSERT OR REPLACE INTO m6_store_metadata(key, value) VALUES(?, ?)",
        ("m6_schema_version", str(M6_SCHEMA_VERSION))
    )
    conn.execute(
        "INSERT OR REPLACE INTO m6_store_metadata(key, value) VALUES(?, ?)",
        ("m6_store_status", "shadow")
    )
    conn.commit()
