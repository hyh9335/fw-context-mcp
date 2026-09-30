"""store_v2.schema — Slice-0 v2 shadow store DDL (structure only).

Implements the Phase-3 decided schema concepts — D1 common skeleton
(`TARGET_SCHEMA_CONCEPT.md` §2), the S-C hybrid umbrella hot layout (§4.3),
per-world Tier-B fact rows (§4.4), per-binding certificate storage (§5), and
the DECISION's G2 amendments ("Land at Slice 0: TARGET_SCHEMA_CONCEPT is
amended by this decision; the DECISION doc is the canonical normative source"):

* generation anchor: ``tu_bindings(state_key, state_generation)`` is a
  composite FK onto ``semantic_states(tusemantic_key, parser_generation)``
  (DECISION rows 8/13 posture; the audit's G2 hazard — a generation-mismatched
  binding — is structurally impossible).
* ``world_status`` gains the DECISION's explicit ``'unfinalized'`` state
  (audit G2: the sketch lacked it).
* Amendment A-2's ``path_spelling_sensitive`` marker on ``semantic_states``
  (DECISION L52: the gate blocks cross-spelling reuse — execution at bind time
  is Slice 2/4; the marker + gate contract land here).
* Amendment A-3 surfaces: ``state_file_table`` + ``semantic_state_positions``
  (shared, once per ``(state, file_ordinal)``, never keyed) alongside the D1
  per-binding PQ cache ``tu_symbol_positions`` (derived, drop-safe).
* Amendment A-4 surface: ``binding_file_map`` (per-binding file_ordinal →
  canon path), never a state attribute.

DELIBERATELY ABSENT at Slice 0 (each absence is a scoped decision, not an
omission — see SLICE0_SCHEMA_MAPPING.md):
* ``symbol_group_members`` (§6/§15.3) — cross-version logical-symbol grouping
  is NOT part of Slice 0 (PHASE4 task §12).
* ``semantic_macros`` and every S-A-only fact table (§4.1) — under the S-C
  umbrella macro definition rows live in the state's cold payload (Slice 1+);
  in particular ``expanded_value`` has NO column anywhere in the v2 schema
  (B7 quarantine: MACRO_EXPANDED_VALUE_DECISION.md §5/D; cold-bundle-only
  representation, no query behavior, no reuse eligibility).
* ``state_deltas`` (§9, S-D appendix) — S-D is rejected.
* Any promotion/cutover marker row — the exact store_metadata slot is unnamed
  (J-13); the marker flip belongs to Slice 7 and must not be pre-empted by an
  invented slot.

This module creates STRUCTURE ONLY: no semantic fact is ever populated by
Slice 0 (PHASE4 task §7), and nothing here runs unless the shadow v2 store is
explicitly created by the flag-gated test path (T19).
"""

from __future__ import annotations

SCHEMA_GENERATION = 1
SCHEMA_GENERATION_NOTE = (
    "Slice-0 skeleton: identity primitives + inert shadow schema. "
    "Structural generations may coexist; no migration logic exists yet "
    "(PHASE4 task §27)."
)

#: M-C §7 v2 store header values.
STORE_FORMAT = "fw-context-store/2"
STORE_VERSION = 1                 # ORDINAL, never a column-set hash (M-C §7)
MIN_READER_GENERATION = 1
CREATED_BY = "fw-context slice0"

#: Default ``key_format`` (DECISION L52 domain separation; D1 §3 example value).
DEFAULT_KEY_FORMAT = "fwctx-tukb/v1.l2closure"
DEFAULT_DIGEST_ALGORITHM = "sha256"

#: The store file name (M-C §4 criterion 5 DESIGN: ``<db_dir>/<project_id>/index-v2.db``).
V2_DB_FILENAME = "index-v2.db"

_STATE_FK = (
    "FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE"
)

DDL_STATEMENTS: tuple[str, ...] = (
    # ── §2.0 store header (M-C §7) ──────────────────────────────────────────
    """
    CREATE TABLE store_metadata (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    # ── §2.1 namespaces — grouping/routing only [Tier B] ────────────────────
    """
    CREATE TABLE repository_namespaces (
      namespace_id       TEXT PRIMARY KEY,     -- opaque routing token (derived from RepositoryKey)
      repository_key_sha TEXT NOT NULL,        -- digest over canonical remotes → root commits → UNKNOWN [S1]
      derivation_detail  TEXT,                 -- JSON: versioned inputs, for INV-9 explainability
      legacy_store_token TEXT,                 -- legacy project_id; a namespace MAY span stores
      created_at         TEXT,
      created_by         TEXT
    )
    """,
    # ── §2.2 snapshots — SourceSnapshot [Tier B] ────────────────────────────
    """
    CREATE TABLE snapshots (
      snapshot_id        TEXT NOT NULL,        -- H(namespace_tag, base_commit|∅, dirty_delta_digest) [S1]
      namespace_id       TEXT NOT NULL,
      namespace_tag      TEXT NOT NULL,        -- 'git' | 'tree'  (identity recomputable from worktree)
      base_commit        TEXT,                 -- canonical root commit, NULL for bare tree
      dirty_delta_digest TEXT,                 -- NULL = clean tree
      provenance_branch  TEXT,                 -- annotation ONLY (never identity)
      provenance_tags    TEXT,                 -- JSON annotations
      verification       TEXT NOT NULL,        -- 'verified' | 'UNVERIFIED' (F-9: fail-closed marker)
      created_at         TEXT,
      created_by         TEXT,
      UNIQUE(namespace_id, snapshot_id)
    )
    """,
    # ── §2.3 builds — BuildIdentity, namespace-scoped [Tier B] ──────────────
    """
    CREATE TABLE builds (
      build_id               INTEGER PRIMARY KEY,   -- handle (INV-3 contract)
      namespace_id           TEXT NOT NULL,
      build_identity_sha     TEXT NOT NULL,    -- H(toolchain, parser_salt, pg, env, declared_env, builder_family)
      toolchain_identity     TEXT NOT NULL,    -- kind/version/target triple
      parser_salt            TEXT NOT NULL,    -- non-empty guard (R-K1(b)/H-6)
      parser_generation      INTEGER NOT NULL, -- anchored generation this build runs under
      environment_digest_sha TEXT NOT NULL,
      declared_env_salt      TEXT NOT NULL,
      declared_env_provenance TEXT NOT NULL,   -- JSON: who declared, what was certified (class-C legs)
      builder_family         TEXT NOT NULL,
      created_at             TEXT,
      UNIQUE(namespace_id, build_identity_sha)
    )
    """,
    # ── §2.4 snapshot_builds — the WORLD row [Tier B] ───────────────────────
    """
    CREATE TABLE snapshot_builds (
      world_id           INTEGER PRIMARY KEY,  -- handle
      namespace_id       TEXT NOT NULL,
      snapshot_id        TEXT NOT NULL,
      build_identity_sha TEXT NOT NULL,
      world_status       TEXT NOT NULL
                         CHECK(world_status IN ('unfinalized','active','superseded','retired',
                                                'failed','retained')),
                                         -- DECISION G2 amendment: explicit 'unfinalized' state
                                         -- M5-4: 'failed' for incomplete/abandoned Worlds (§13);
                                         --        'retained' for explicitly retained Worlds (§8)
      run_provenance     TEXT,             -- JSON, world-local; absolute paths allowed HERE (R-BD4)
      created_at         TEXT,
      UNIQUE(namespace_id, snapshot_id, build_identity_sha)   -- world identity triple (INV-3)
    )
    """,
    """
    CREATE TABLE world_poisons (              -- graph §3.5 + D2 §5.1; per-(world, component) nonce [SKC §1.4]
      world_id    INTEGER NOT NULL,
      component   TEXT NOT NULL,              -- which leg poisoned the key (c1 blob, rsp, cert, …)
      nonce       TEXT NOT NULL,              -- random, persisted with the world; nonce'd keys never shared
      poison_tag  TEXT NOT NULL,
      detected_at TEXT,
      UNIQUE(world_id, component),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE
    )
    """,
    # ── §2.5 file_blobs + snapshot_files — DEDUP-1 substrate + membership ───
    """
    CREATE TABLE file_blobs (
      blob_sha     TEXT PRIMARY KEY,          -- SHA-256 over file bytes (any origin)
      byte_size    INTEGER NOT NULL,
      origin_class TEXT,                      -- annotation: 'project'|'system'|'generated'|'forced'
      content      BLOB,                      -- bytes; oversized payloads may use the D2 §4.3 side-file channel
      first_seen_at TEXT
    )
    """,
    """
    CREATE TABLE snapshot_files (             -- SnapshotMembership: partial canon_path → FileBlob
      namespace_id TEXT NOT NULL,
      snapshot_id  TEXT NOT NULL,
      canon_path   TEXT NOT NULL,             -- project-relative canonical (INV-4)
      blob_sha     TEXT NOT NULL,
      completeness TEXT NOT NULL,             -- 'delta_certificate' | 'lazy_partial'
      UNIQUE(namespace_id, snapshot_id, canon_path),
      FOREIGN KEY(blob_sha) REFERENCES file_blobs(blob_sha) ON DELETE CASCADE
    )
    """,
    # ── §2.6 tu_bindings — membership + presentation INPUTS [Tier B, INV-2] ─
    """
    CREATE TABLE tu_bindings (
      binding_id        INTEGER PRIMARY KEY,  -- handle
      world_id          INTEGER NOT NULL,
      tu_context_id     TEXT NOT NULL,        -- per-world parse-action identity (SAFE_REUSE G4 erratum)
      canon_source_path TEXT NOT NULL,        -- project-relative (INV-4)
      source_blob_sha   TEXT NOT NULL,
      ncc_digest_sha    TEXT NOT NULL,
      state_key         TEXT,                 -- → semantic_states.tusemantic_key (NULL while unbound)
      state_generation  INTEGER,              -- → semantic_states.parser_generation
      binding_status    TEXT NOT NULL,        -- 'bound'|'staging'|'poisoned'|'failed'
      disposition       TEXT NOT NULL,        -- 'ok' | 'poisoned'   (graph §3.5)
      poison_component  TEXT,                 -- → world_poisons.component when disposition='poisoned'
      cert_digest_sha   TEXT,                 -- digest over this binding's certificate rows (fast eq check)
      UNIQUE(world_id, tu_context_id),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE,
      FOREIGN KEY(state_key, state_generation)
        REFERENCES semantic_states(tusemantic_key, parser_generation)
        -- G2 generation anchor: a binding can only reference an EXISTING
        -- (key, generation) pair — a generation-mismatched binding (D6 row 13)
        -- is structurally impossible. NULL columns = unbound (FK not enforced).
    )
    """,
    """
    CREATE TABLE tu_context_details (         -- per-world, diffable Tier-B
      binding_id           INTEGER PRIMARY KEY,
      ncc_serial           TEXT,              -- canonical NCC serialization, spelling present, per-world
      rsp_record           TEXT,              -- rsp uncertainty, expanded flags (fail-closed markers)
      presentation_context TEXT                -- JSON: per-world spelling context; machine-local OK here
    )
    """,
    # ── §2.7 D2-allocated mechanisms (created reserved/unwritten at Slice 0) ─
    """
    CREATE TABLE world_claims (               -- D2 S3 world claim (Slice 5 machinery; reserved)
      world_id INTEGER PRIMARY KEY,
      owner_id TEXT NOT NULL, lease_deadline TEXT, heartbeat_at TEXT,
      mode TEXT NOT NULL, acquired_at TEXT
    )
    """,
    """
    CREATE TABLE namespace_claims (           -- D2 S4 namespace claim (reserved)
      namespace_id TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL, lease_deadline TEXT, heartbeat_at TEXT,
      mode TEXT NOT NULL, acquired_at TEXT
    )
    """,
    """
    CREATE TABLE retention_pins (             -- P1 root set (D2 §3.2 R2; reserved — Slice 5)
      pin_id INTEGER PRIMARY KEY,
      world_id INTEGER NOT NULL,
      reason TEXT NOT NULL, created_by TEXT, created_at TEXT,
      UNIQUE(world_id, reason),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE gc_epochs (                  -- optional amortization knob (D2 §2.4; reserved)
      epoch INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT
    )
    """,
    """
    CREATE TABLE source_artifacts (           -- Tier-A, content-addressed [R-A1; first-write-wins]
      artifact_key TEXT PRIMARY KEY,        -- H(declared input tuple + producer provenance)
      artifact_kind TEXT NOT NULL,          -- 'llm_analysis' | …
      declared_inputs TEXT NOT NULL,        -- JSON, versioned
      producer_provenance TEXT NOT NULL,
      payload BLOB, payload_digest TEXT, created_at TEXT
    )
    """,
    """
    CREATE TABLE build_rollups (              -- per-world Tier-B, NEVER shared (D2 §3.1)
      world_id INTEGER NOT NULL, rollup_kind TEXT NOT NULL,   -- memory_regions|entry_point|linker_script|diagnostics
      payload TEXT NOT NULL,                -- JSON
      UNIQUE(world_id, rollup_kind),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE
    )
    """,
    # ── §3 semantic_states — the shared Tier-T anchor ───────────────────────
    """
    CREATE TABLE semantic_states (
      state_id            INTEGER PRIMARY KEY,  -- SURROGATE HANDLE only (§1 contract, INV-3)
      tusemantic_key      TEXT NOT NULL,        -- full-width digest over L0..L3 (KB-C composition)
      parser_generation   INTEGER NOT NULL,     -- identity co-column (UNIQUE below); anchored at bind
      key_format          TEXT NOT NULL,        -- 'fwctx-tukb/v1.l2closure' — digest-domain separation
      l0_source_blob_sha  TEXT NOT NULL,
      l1_ncc_digest_sha   TEXT NOT NULL,
      l2_closure_digest_sha TEXT NOT NULL,      -- KB-C: c3-content + c4-CONTENT-seq + c5 + key_format
                                                --   (DECISION L52); parser_generation rides L3;
                                                --   c1/cert paths EXCLUDED (A-1) — they live in cert_rows
      l3_env_digest_sha   TEXT NOT NULL,        -- full L3: toolchain+parser salts, parser_generation, env
                                                --   (DECISION L52: parser_generation/environment/toolchain ride L3)
      publication         TEXT NOT NULL
                          CHECK(publication IN ('staging','published','quarantined')),
                                                -- D2 §5; INV-8 visibility gate lands Slice 1
      completeness_sha    TEXT NOT NULL,        -- fact_set_digest over the canonical serialization (D2 §6)
      digest_algorithm    TEXT NOT NULL,
      fact_row_count      INTEGER,
      fact_schema_version INTEGER NOT NULL,
      payload_format      TEXT NOT NULL
                          CHECK(payload_format IN ('normalized','bundle','hybrid')),
                                                -- 'normalized' (S-A) | 'bundle' (S-B) | 'hybrid' (S-C)
      payload_ref         TEXT,                 -- cold bundle/side-file ref (D2 §4.3 channel) when used
      payload_digest_sha  TEXT,                 -- cold bundle digest (S-C cold side)
      owner_lease         TEXT,                 -- staging-only owner (D2 §5.2); NULL when published
      path_spelling_sensitive INTEGER NOT NULL DEFAULT 0,
                                                -- A-2 (DECISION L52): parse is path-spelling-sensitive ⇒
                                                --   cross-spelling binding refusal at bind time (Slice 2/4)
      created_at TEXT, created_by_run TEXT,
      last_unreferenced_at TEXT,            -- ADVISORY cache hint only (INV-10; D2 §2.5)
      quarantine_note TEXT,                 -- verified-integrity-failure evidence trail (D2 §5.1)
      UNIQUE(tusemantic_key, parser_generation)   -- THE identity (INV-3) + racer convergence arbiter (D2 §4.4)
    )
    """,
    # Poisoned keys NEVER appear here (INV-6): world_poisons + tu_private_payloads.
    # A published state is immutable (INV-1): no UPDATE path exists at Slice 0 and
    # none may be added that mutates published fact content (corrections = NEW key).
    # ── §4.3 S-C umbrella HOT layout (query/join surface) ───────────────────
    """
    CREATE TABLE semantic_symbols_core (
      state_id INTEGER NOT NULL, local_key INTEGER NOT NULL,
                                        -- SemanticStateLocalSymbolKey: 1-based ordinal in the state's
                                        -- canonical payload serialization (§15.1) — NEVER a rowid,
                                        -- raw USR, path spelling, or line number
      raw_usr TEXT,                     -- ATTRIBUTE only (graph §3.1): never a global/semantic primary key
      name TEXT, kind TEXT, is_definition INTEGER,
      is_virtual INTEGER, is_pure_virtual INTEGER, is_template INTEGER, signature TEXT,
      UNIQUE(state_id, local_key),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE semantic_edges (
      state_id INTEGER NOT NULL, local_key INTEGER NOT NULL, edge_ordinal INTEGER NOT NULL,
      edge_class TEXT NOT NULL,         -- ref|call|indirect|fp_assign|inherit|override
      to_local_key INTEGER,             -- resolved endpoint WITHIN THE SAME state, or NULL when dangling
      to_raw_usr TEXT,                  -- unresolved/cross-state target: ATTRIBUTE (graph §3.2), never a pointer
      to_name TEXT, certainty TEXT, resolution_kind TEXT,
      UNIQUE(state_id, local_key, edge_ordinal),
      FOREIGN KEY(state_id, local_key) REFERENCES semantic_symbols_core(state_id, local_key) ON DELETE CASCADE,
      FOREIGN KEY(state_id, to_local_key) REFERENCES semantic_symbols_core(state_id, local_key)
        -- §16.1 state-local endpoint model: an edge can only resolve inside its OWN
        -- state; an accidental cross-state alias is structurally impossible (T21).
        -- NULL to_local_key = unresolved/cross-state → attribute legs only (FK not enforced).
    )
    """,
    """
    CREATE TABLE semantic_condition_index (
      state_id INTEGER NOT NULL, region_ordinal INTEGER NOT NULL,
      parent_region_ordinal INTEGER, directive TEXT, condition TEXT, file_blob_sha TEXT,
      UNIQUE(state_id, region_ordinal),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE
    )
    """,
    # COLD: one immutable bundle per state via semantic_states.payload_ref /
    # payload_digest_sha (bodies, file content, macro value detail incl. the
    # B7-quarantined expanded_value channel, docstrings, condition-branch detail
    # text). Item-level pointers ((blob_offset, blob_len) on hot rows) are
    # Slice-1 serializer details — derived, rebuildable, never identity.
    # ── A-3 / §15.4 state-scoped file table + shared positions ──────────────
    """
    CREATE TABLE state_file_table (           -- §15.4: state-local file table (file_ordinal → role, blob)
      state_id INTEGER NOT NULL, file_ordinal INTEGER NOT NULL,
      role TEXT NOT NULL,                 -- payload-defined role ('main'|'header'|…)
      blob_sha TEXT NOT NULL,
      UNIQUE(state_id, file_ordinal),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE semantic_state_positions (   -- A-3: parse-output positions, ONCE per (state, file_ordinal),
      state_id INTEGER NOT NULL,          --   non-keyed facts (INV-3/FOM: no position is any identity);
      file_ordinal INTEGER NOT NULL,      --   valid because under KB-C equal blob ⇒ equal positions across
      fact_ordinal INTEGER NOT NULL,      --   all bindings of one state (adjudication A-3 flip-guard note)
      symbol_local_key INTEGER,           -- optional endpoint within the SAME state
      line INTEGER, col INTEGER, end_line INTEGER, end_col INTEGER,
      PRIMARY KEY(state_id, file_ordinal, fact_ordinal),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE,
      FOREIGN KEY(state_id, symbol_local_key) REFERENCES semantic_symbols_core(state_id, local_key)
    )
    """,
    # ── G5 (Dead-Code): Tier-A supplementary facts for reachability ────────
    # These are deterministic from the parse (same source + same flags ⇒
    # same attrs), so they are NOT part of fact_set_digest.  They are
    # populated alongside semantic_symbols_core during publish and read by
    # the dead-code adapter for root discovery.
    """
    CREATE TABLE semantic_symbol_attrs (     -- G5: GNU attr_flags + linker section per symbol
      state_id INTEGER NOT NULL, local_key INTEGER NOT NULL,
      attr_flags TEXT NOT NULL DEFAULT '',   -- "used,retain,constructor" — sorted, comma-joined
      section TEXT NOT NULL DEFAULT '',      -- linker section from __attribute__((section("...")))
      UNIQUE(state_id, local_key),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE,
      FOREIGN KEY(state_id, local_key) REFERENCES semantic_symbols_core(state_id, local_key)
    )
    """,
    """
    CREATE TABLE semantic_state_roots (      -- G5: rootless edges (ctor_root, implicit_construct with from_usr=None)
      state_id INTEGER NOT NULL, root_ordinal INTEGER NOT NULL,
      edge_class TEXT NOT NULL,             -- ctor_root | implicit_construct | alias
      to_local_key INTEGER,                 -- resolved target within state, or NULL
      to_raw_usr TEXT NOT NULL,             -- target USR (always present)
      UNIQUE(state_id, root_ordinal),
      FOREIGN KEY(state_id) REFERENCES semantic_states(state_id) ON DELETE CASCADE
    )
    """,
    # ── §4.4 per-world Tier-B fact rows (contract-mandated duplication) ─────
    """
    CREATE TABLE tu_branch_states (
      binding_id INTEGER NOT NULL, region_ordinal INTEGER NOT NULL, branch_ordinal INTEGER NOT NULL,
      taken INTEGER NOT NULL,             -- -1|0|1   R-T3/R-BD2: per-world, NEVER shared
      UNIQUE(binding_id, region_ordinal, branch_ordinal),
      FOREIGN KEY(binding_id) REFERENCES tu_bindings(binding_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE tu_symbol_visibility (
      binding_id INTEGER NOT NULL, local_key INTEGER NOT NULL, visibility TEXT,
      UNIQUE(binding_id, local_key),      -- R-BD5
      FOREIGN KEY(binding_id) REFERENCES tu_bindings(binding_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE tu_symbol_positions (        -- PQ CACHE: DERIVED from (binding blob + state structure),
      binding_id INTEGER NOT NULL,        --   drop-safe rebuildable, NEVER identity (D1 §4.4)
      local_key INTEGER NOT NULL,
      canon_file_path TEXT, line INTEGER, col INTEGER, end_line INTEGER, end_col INTEGER,
      PRIMARY KEY(binding_id, local_key),
      FOREIGN KEY(binding_id) REFERENCES tu_bindings(binding_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE tu_private_payloads (        -- poisoned/failed TUs' facts; world-local FOREVER (INV-6)
      binding_id INTEGER NOT NULL, world_id INTEGER NOT NULL,
      payload_format TEXT, payload BLOB, payload_digest TEXT, reason TEXT,
      UNIQUE(binding_id),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE
    )
    """,
    # ── §4.7 world_extra_files — non-TU files captured per-World for historical integrity
    # Assembly files (.s/.S), linker scripts (.ld), and other non-TU artifacts that
    # queries must read from immutable indexed content, NOT live disk state.
    # Without this, get_vector_table and similar tools would violate historical
    # World semantics by reading current filesystem content.
    """
    CREATE TABLE world_extra_files (          -- non-TU files captured per-World (G10 historical integrity)
      world_id    INTEGER NOT NULL,
      canon_path  TEXT NOT NULL,               -- project-relative canonical path
      file_role   TEXT NOT NULL,               -- 'assembly' | 'linker_script' | 'other'
      blob_sha    TEXT NOT NULL,              -- SHA-256 over file bytes
      content     BLOB,                       -- raw file bytes (immutable snapshot)
      byte_size   INTEGER NOT NULL,
      captured_at TEXT NOT NULL,
      PRIMARY KEY(world_id, canon_path),
      FOREIGN KEY(world_id) REFERENCES snapshot_builds(world_id) ON DELETE CASCADE
    )
    """,
    # ── §5 dependency / resolution certificate storage (per-binding, diffable) ─
    """
    CREATE TABLE canon_paths (
      path_id INTEGER PRIMARY KEY,
      namespace_id TEXT, path TEXT NOT NULL,
      UNIQUE(namespace_id, path)          -- project-relative canon paths (INV-4); dictionary
    )
    """,
    """
    CREATE TABLE cert_rows (
      binding_id INTEGER NOT NULL,        -- per-world-context (A-1 consequence; graph row DCF cert)
      directive_ordinal INTEGER NOT NULL,
      include_spelling TEXT NOT NULL,     -- as written in the source (intrinsic to the blob)
      resolved_blob_sha TEXT,             -- → file_blobs; NULL when unresolved
      resolved_path_id INTEGER,           -- → canon_paths; canon-PROVENANCE leg: STORED, NOT key-digested (A-1)
      role TEXT NOT NULL,                 -- direct|system|extern-c|framework|forced
      rnf INTEGER NOT NULL DEFAULT 0,     -- RESOLVED-NOT-FOUND marker (R-T4 fail-closed gate)
      UNIQUE(binding_id, directive_ordinal),
      FOREIGN KEY(binding_id) REFERENCES tu_bindings(binding_id) ON DELETE CASCADE,
      FOREIGN KEY(resolved_blob_sha) REFERENCES file_blobs(blob_sha)
    )
    """,
    # ── A-4: per-binding file map — defining-file canon path is BINDING-derived ─
    """
    CREATE TABLE binding_file_map (           -- A-4/§15.4: per-world file map, never a state attribute
      binding_id INTEGER NOT NULL,
      file_ordinal INTEGER NOT NULL,
      canon_path_id INTEGER,              -- → canon_paths (per-binding defining-file canon path)
      blob_sha TEXT NOT NULL,
      UNIQUE(binding_id, file_ordinal),
      FOREIGN KEY(binding_id) REFERENCES tu_bindings(binding_id) ON DELETE CASCADE,
      FOREIGN KEY(canon_path_id) REFERENCES canon_paths(path_id)
    )
    """,
)

# §6 index strategy — only the §13 access-path indexes; nothing speculative
# (INV-7). symbol_group_members (derived grouping) is NOT created at Slice 0.
INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX idx_tu_bindings_world ON tu_bindings(world_id)",
    "CREATE INDEX idx_tu_bindings_state ON tu_bindings(state_key, state_generation)",
    "CREATE INDEX idx_tu_bindings_path ON tu_bindings(canon_source_path)",
    "CREATE INDEX idx_cert_rows_blob ON cert_rows(resolved_blob_sha)",
    "CREATE INDEX idx_cert_rows_path ON cert_rows(resolved_path_id)",
    "CREATE INDEX idx_symbols_core_usr ON semantic_symbols_core(state_id, raw_usr)",
    "CREATE INDEX idx_world_extra_files ON world_extra_files(world_id, file_role)",
)

METADATA_SEED: tuple[tuple[str, str], ...] = (
    ("store_format", STORE_FORMAT),
    ("store_version", str(STORE_VERSION)),          # ORDINAL (M-C §7)
    ("min_reader_generation", str(MIN_READER_GENERATION)),
    ("schema_generation", str(SCHEMA_GENERATION)),
    ("key_format_default", DEFAULT_KEY_FORMAT),
    ("digest_algorithm", DEFAULT_DIGEST_ALGORITHM),
    ("created_by", CREATED_BY),
    # F1 provenance (P-A): stores CREATED by capture code with F3R position
    # semantics (1-based BYTE columns, half-open end, true end_column) carry
    # this marker.  A pre-F3R store (created before the marker existed) does
    # NOT gain it when later opened by newer code — conservative: its
    # pre-F3R position rows stay untrusted (slice6c_f1_search/
    # F1_POSITION_PROVENANCE.md).  This is NOT the Slice-7 promotion/cutover
    # marker (that slot remains unnamed, J-13).
    ("position_capture_semantics", "f3r/v1"),
    # NO promotion/cutover marker row: the exact slot is unnamed (J-13) and the
    # marker flip belongs to Slice 7 — inventing the slot here would pre-empt a
    # controller decision.
)


def store_metadata_row_missing() -> tuple[str, ...]:
    """Metadata keys that MUST be present after creation (test helper)."""
    return tuple(k for k, _ in METADATA_SEED)
