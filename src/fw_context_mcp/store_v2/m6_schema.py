"""M6 shadow store schema — Phase I shared-fact tables.

These tables are SHADOW tables — they run alongside the existing v2
whole-TU store (schema.py). They are populated during indexing but
NOT used by production queries until shadow differential testing
(§34) proves QUERY_PARITY_FAILURES == 0.

Design principles:
1. Canonical fact identity (prototype_a.py canon functions)
2. Interned BuildSets (frozenset → buildset_id)
3. Physical fact bundles grouped by (fact_kind, buildset_id)
4. Reachability-based GC (no age-only deletion)
5. Three non-collapsible concepts: identity / presence / certificate

Tables:
  m6_buildsets        — interned BuildSets (buildset_id, builds_hash, build_names_json)
  m6_shared_facts     — canonical facts (fact_canon, fact_kind, payload_json, buildset_id)
  m6_fact_bundles     — physical bundles (bundle_id, fact_kind, buildset_id, fact_count)
  m6_world_fact_refs  — per-world fact membership (world_id, fact_canon) for GC reachability
  m6_certificates     — dependency certificates for materialization reuse (§17)
  m6_body_facts       — function/source body text facts (separate from structural facts)
"""
from __future__ import annotations

M6_SCHEMA_VERSION = 1

M6_DDL_STATEMENTS: tuple[str, ...] = (
    # ── Interned BuildSets ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS m6_buildsets (
      buildset_id     INTEGER PRIMARY KEY,
      builds_hash     TEXT NOT NULL UNIQUE,    -- SHA-256 over sorted build_id list
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
      fact_kind       TEXT NOT NULL,            -- Symbol|Function|Call|Variable|Type|Field|EnumConst|Body
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

    # ── Per-world fact membership (for GC reachability) ─────────────────────
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

    # ── Dependency certificates (for materialization reuse, §17) ───────────
    """
    CREATE TABLE IF NOT EXISTS m6_certificates (
      cert_id             INTEGER PRIMARY KEY,
      source_blob_sha     TEXT NOT NULL,        -- L0
      ncc_digest_sha      TEXT NOT NULL,        -- L1
      closure_digest_sha  TEXT NOT NULL,        -- L2
      macro_env_digest    TEXT NOT NULL,        -- macro environment digest
      toolchain_id        TEXT NOT NULL,        -- toolchain identity
      cert_hash           TEXT NOT NULL UNIQUE, -- SHA-256 over all legs
      fact_set_digest     TEXT,                 -- digest over all facts produced (completeness check)
      fact_count          INTEGER,
      created_at          TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_m6_certs_hash ON m6_certificates(cert_hash)
    """,

    # ── Body/source text facts (separate from structural facts) ─────────────
    """
    CREATE TABLE IF NOT EXISTS m6_body_facts (
      body_id         INTEGER PRIMARY KEY,
      symbol_usr      TEXT NOT NULL,            -- the symbol this body belongs to
      buildset_id     INTEGER NOT NULL,         -- → m6_buildsets
      body_text       TEXT NOT NULL,             -- the source body text
      body_hash       TEXT NOT NULL,            -- SHA-256 over body_text
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
    """Create M6 shadow tables in the given SQLite connection."""
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
