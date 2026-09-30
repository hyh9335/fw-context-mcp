"""Slice-0 schema tests — T21 state-local symbol/edge ownership, A-1..A-4
schema surfaces, identity-not-rowid, generation anchor, version gate,
immutability preparation (INV-1 shape), and poison exclusion (INV-6)."""

from __future__ import annotations

import sqlite3

import pytest

from fw_context_mcp.store_v2 import (
    SCHEMA_GENERATION,
    STORE_FORMAT,
    STORE_VERSION,
    ShadowStore,
    StoreFormatError,
    StoreGenerationError,
    StoreVersionError,
    v2_db_path,
)
from fw_context_mcp.store_v2.schema import DDL_STATEMENTS, METADATA_SEED

KEY_A = "aa" * 32
KEY_B = "bb" * 32


@pytest.fixture()
def store(tmp_path):
    s = ShadowStore.create(tmp_path / "proj" / "index-v2.db")
    yield s
    s.close()


def _add_state(conn, key: str, pg: int = 1) -> int:
    cur = conn.execute(
        "INSERT INTO semantic_states(tusemantic_key, parser_generation, key_format,"
        " l0_source_blob_sha, l1_ncc_digest_sha, l2_closure_digest_sha,"
        " l3_env_digest_sha, publication, completeness_sha, digest_algorithm,"
        " fact_schema_version, payload_format)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, pg, "fwctx-tukb/v1.l2closure", "a" * 64, "b" * 64, "c" * 64,
         "d" * 64, "published", "e" * 64, "sha256", 1, "hybrid"))
    return cur.lastrowid


def _add_symbol(conn, state_id: int, local_key: int, usr: str) -> None:
    conn.execute(
        "INSERT INTO semantic_symbols_core(state_id, local_key, raw_usr, name,"
        " kind, is_definition) VALUES(?,?,?,?,?,?)",
        (state_id, local_key, usr, f"sym{local_key}", "function", 1))


# ══════════════════════ creation & header ════════════════════════════════════

def test_create_seeds_header_and_tables(store):
    meta = store.metadata()
    assert meta["store_format"] == STORE_FORMAT
    assert meta["store_version"] == str(STORE_VERSION)
    assert meta["schema_generation"] == str(SCHEMA_GENERATION)
    assert meta["key_format_default"] == "fwctx-tukb/v1.l2closure"
    assert meta["created_by"] == "fw-context slice0"
    assert int(meta["min_reader_generation"]) >= 1
    # no promotion/cutover marker row may pre-exist (J-13)
    assert not any("promot" in k or "cutover" in k or "legacy" in k
                   for k in meta)
    tables = set(store.table_names())
    for required in ("store_metadata", "repository_namespaces", "snapshots",
                     "builds", "snapshot_builds", "world_poisons", "file_blobs",
                     "snapshot_files", "tu_bindings", "tu_context_details",
                     "world_claims", "namespace_claims", "retention_pins",
                     "gc_epochs", "source_artifacts", "build_rollups",
                     "semantic_states", "semantic_symbols_core",
                     "semantic_edges", "semantic_condition_index",
                     "state_file_table", "semantic_state_positions",
                     "tu_branch_states", "tu_symbol_visibility",
                     "tu_symbol_positions", "tu_private_payloads",
                     "canon_paths", "cert_rows", "binding_file_map"):
        assert required in tables, required
    # deliberate absences (scoped decisions)
    assert "symbol_group_members" not in tables   # §15.3: NOT Slice 0
    assert "semantic_macros" not in tables        # B7 quarantine: no hot home
    assert "state_deltas" not in tables           # S-D rejected


def test_no_update_or_delete_sql_in_schema_source():
    """INV-1 shape: no UPDATE/DELETE statement exists anywhere in the DDL or
    index set (published states are immutable; corrections = NEW key)."""
    blob = "\n".join(DDL_STATEMENTS)
    assert "UPDATE " not in blob and "DELETE FROM" not in blob


def test_create_refuses_existing_store(tmp_path):
    p = tmp_path / "proj" / "index-v2.db"
    ShadowStore.create(p).close()
    with pytest.raises(StoreFormatError):
        ShadowStore.create(p)


def test_open_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        ShadowStore.open(tmp_path / "missing" / "index-v2.db")


def test_db_path_convention_is_separate_from_legacy(tmp_path):
    """M-C: physically separate store `<db_dir>/<project_id>/index-v2.db`;
    legacy `index.db` is never collocated under the same filename."""
    p = v2_db_path(tmp_path, "myproj")
    assert p == tmp_path / "myproj" / "index-v2.db"
    assert p.name != "index.db"


# ══════════════════════ T21 — state-local ownership ══════════════════════════

def test_t21_same_usr_two_states_no_aliasing(store):
    """Raw USR is an attribute: the SAME USR in two states must never alias —
    an edge of state B cannot resolve into state A's symbols."""
    conn = store._conn
    sa = _add_state(conn, KEY_A, pg=1)
    sb = _add_state(conn, KEY_B, pg=1)
    _add_symbol(conn, sa, 1, "usr:uart_init")
    _add_symbol(conn, sa, 2, "usr:uart_send")
    _add_symbol(conn, sb, 1, "usr:uart_init")     # same raw USR, other state
    # from-side must resolve within the same state
    conn.execute("INSERT INTO semantic_edges(state_id, local_key, edge_ordinal,"
                 " edge_class, to_local_key, certainty) VALUES(?,?,?,?,?,?)",
                 (sa, 2, 1, "call", 1, "exact"))
    # a state-B edge pointing at a key that exists ONLY in state A: impossible
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO semantic_edges(state_id, local_key,"
                     " edge_ordinal, edge_class, to_local_key, certainty)"
                     " VALUES(?,?,?,?,?,?)",
                     (sb, 1, 1, "call", 2, "exact"))
    # dangling endpoint (NULL) is allowed by design (§16.1)
    conn.execute("INSERT INTO semantic_edges(state_id, local_key, edge_ordinal,"
                 " edge_class, to_local_key, to_raw_usr, certainty)"
                 " VALUES(?,?,?,?,?,?,?)",
                 (sb, 1, 2, "ref", None, "usr:external_fn", "unresolved"))
    conn.commit()
    rows = conn.execute("SELECT count(*) FROM semantic_edges").fetchone()[0]
    assert rows == 2


def test_t21_from_endpoint_must_exist_in_own_state(store):
    conn = store._conn
    sa = _add_state(conn, KEY_A)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO semantic_edges(state_id, local_key,"
                     " edge_ordinal, edge_class) VALUES(?,?,?,?)",
                     (sa, 99, 1, "call"))          # no symbol (sa, 99)


def test_identity_pair_unique_not_rowid(store):
    """INV-3: identity is the (tusemantic_key, parser_generation) value pair —
    rowids are handles; the same key under a new generation is a NEW state;
    the same (key, generation) can never be inserted twice."""
    conn = store._conn
    s1 = _add_state(conn, KEY_A, pg=1)
    s2 = _add_state(conn, KEY_A, pg=2)      # OK: different generation
    assert s1 != s2
    with pytest.raises(sqlite3.IntegrityError):
        _add_state(conn, KEY_A, pg=1)       # exact pair collision
    conn.commit()


def test_symbol_local_key_ordinal_not_usr(store):
    """§15.1: local_key is the canonical-serialization ordinal; the same USR
    twice in one state is allowed ONLY as distinct ordinals (never aliased by
    rowid), and identity never involves raw USR/paths/lines."""
    conn = store._conn
    sa = _add_state(conn, KEY_A)
    _add_symbol(conn, sa, 1, "usr:duplicate")
    _add_symbol(conn, sa, 2, "usr:duplicate")   # same USR, distinct local keys
    conn.commit()
    n = conn.execute("SELECT count(*) FROM semantic_symbols_core").fetchone()[0]
    assert n == 2


def test_generation_anchor_composite_fk(store):
    """DECISION rows 8/13: a binding may only reference an EXISTING
    (tusemantic_key, parser_generation) pair — generation-mismatched bindings
    are structurally impossible."""
    conn = store._conn
    _add_state(conn, KEY_A, pg=7)
    conn.execute("INSERT INTO snapshot_builds(namespace_id, snapshot_id,"
                 " build_identity_sha, world_status) VALUES('ns','snap','bf','active')")
    wid = conn.execute("SELECT world_id FROM snapshot_builds").fetchone()[0]
    conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                 " canon_source_path, source_blob_sha, ncc_digest_sha,"
                 " state_key, state_generation, binding_status, disposition)"
                 " VALUES(?,?,?,?,?,?,?,?,?)",
                 (wid, "tu-1", "src/main.c", "a" * 64, "b" * 64,
                  KEY_A, 7, "bound", "ok"))
    # mismatched generation → refused
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                     " canon_source_path, source_blob_sha, ncc_digest_sha,"
                     " state_key, state_generation, binding_status, disposition)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (wid, "tu-2", "src/main.c", "a" * 64, "b" * 64,
                      KEY_A, 8, "bound", "ok"))
    # unknown key → refused
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                     " canon_source_path, source_blob_sha, ncc_digest_sha,"
                     " state_key, state_generation, binding_status, disposition)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (wid, "tu-4", "src/main.c", "a" * 64, "b" * 64,
                      KEY_B, 7, "bound", "ok"))
    # unbound (NULL pair) → allowed
    conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                 " canon_source_path, source_blob_sha, ncc_digest_sha,"
                 " binding_status, disposition) VALUES(?,?,?,?,?,?,?)",
                 (wid, "tu-3", "src/x.c", "a" * 64, "b" * 64, "staging", "ok"))
    conn.commit()


# ══════════════════════ amendments A-1..A-4 schema surfaces ══════════════════

def test_a1_cert_provenance_columns_stored(store):
    """A-1: provenance legs (spelling, resolved path, role) are STORED per
    binding row and appear in NO semantic_states key column."""
    conn = store._conn
    cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_states)")}
    # the A-2 marker is a GATE FLAG, not a keyed path leg
    assert not any("path" in c and c != "path_spelling_sensitive" for c in cols)
    cert_cols = {r[1] for r in conn.execute("PRAGMA table_info(cert_rows)")}
    assert {"include_spelling", "resolved_path_id", "role", "rnf",
            "resolved_blob_sha"} <= cert_cols


def test_a2_path_spelling_sensitive_marker(store):
    conn = store._conn
    sa = _add_state(conn, KEY_A)
    conn.execute("UPDATE semantic_states SET path_spelling_sensitive=1"
                 " WHERE state_id=?", (sa,))
    v = conn.execute("SELECT path_spelling_sensitive FROM semantic_states"
                     " WHERE state_id=?", (sa,)).fetchone()[0]
    assert v == 1
    conn.rollback()


def test_a3_positions_state_scoped_once(store):
    """A-3: shared positions are keyed by (state, file_ordinal, fact_ordinal) —
    once per state, never per binding; the per-binding position table is the
    derived PQ cache."""
    conn = store._conn
    sa = _add_state(conn, KEY_A)
    _add_symbol(conn, sa, 1, "usr:sym1")
    conn.execute("INSERT INTO state_file_table(state_id, file_ordinal, role,"
                 " blob_sha) VALUES(?,?,?,?)", (sa, 1, "main", "a" * 64))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO state_file_table(state_id, file_ordinal,"
                     " role, blob_sha) VALUES(?,?,?,?)", (sa, 1, "main", "b" * 64))
    conn.execute("INSERT INTO semantic_state_positions(state_id, file_ordinal,"
                 " fact_ordinal, symbol_local_key, line, col, end_line, end_col)"
                 " VALUES(?,?,?,?,?,?,?,?)", (sa, 1, 1, 1, 3, 1, 8, 1))
    conn.commit()
    pq = {r[1] for r in conn.execute("PRAGMA table_info(tu_symbol_positions)")}
    assert {"binding_id", "local_key", "canon_file_path"} <= pq


def test_a4_defining_file_binding_derived(store):
    """A-4: the same file_ordinal of the SAME state may map to DIFFERENT canon
    paths in different bindings' file maps — no state-level path attribute."""
    conn = store._conn
    conn.execute("INSERT INTO canon_paths(namespace_id, path) VALUES('ns','a/src/driver.c')")
    conn.execute("INSERT INTO canon_paths(namespace_id, path) VALUES('ns','b/src/driver.c')")
    pa = conn.execute("SELECT path_id FROM canon_paths WHERE path='a/src/driver.c'").fetchone()[0]
    pb = conn.execute("SELECT path_id FROM canon_paths WHERE path='b/src/driver.c'").fetchone()[0]
    conn.execute("INSERT INTO snapshot_builds(namespace_id, snapshot_id,"
                 " build_identity_sha, world_status) VALUES('ns','snap','bf','active')")
    wid = conn.execute("SELECT world_id FROM snapshot_builds").fetchone()[0]
    conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                 " canon_source_path, source_blob_sha, ncc_digest_sha,"
                 " binding_status, disposition) VALUES(?,?,?,?,?,?,?)",
                 (wid, "tu-a", "a/src/driver.c", "a" * 64, "b" * 64,
                  "staging", "ok"))
    conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                 " canon_source_path, source_blob_sha, ncc_digest_sha,"
                 " binding_status, disposition) VALUES(?,?,?,?,?,?,?)",
                 (wid, "tu-b", "b/src/driver.c", "a" * 64, "b" * 64,
                  "staging", "ok"))
    ba = conn.execute("SELECT binding_id FROM tu_bindings WHERE tu_context_id='tu-a'").fetchone()[0]
    bb = conn.execute("SELECT binding_id FROM tu_bindings WHERE tu_context_id='tu-b'").fetchone()[0]
    conn.execute("INSERT INTO binding_file_map(binding_id, file_ordinal,"
                 " canon_path_id, blob_sha) VALUES(?,?,?,?)", (ba, 1, pa, "c" * 64))
    conn.execute("INSERT INTO binding_file_map(binding_id, file_ordinal,"
                 " canon_path_id, blob_sha) VALUES(?,?,?,?)", (bb, 1, pb, "c" * 64))
    conn.commit()
    n = conn.execute("SELECT count(*) FROM binding_file_map").fetchone()[0]
    assert n == 2


def test_macro_expanded_value_has_no_home(store):
    """B7 quarantine: no column named expanded_value exists anywhere; macro
    value detail is a cold-payload channel (payload_ref/payload_digest_sha)."""
    conn = store._conn
    for t in store.table_names():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        assert "expanded_value" not in cols, t
    cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_states)")}
    assert {"payload_format", "payload_ref", "payload_digest_sha"} <= cols


def test_publication_check_and_staging_lease(store):
    conn = store._conn
    for pub in ("staging", "published", "quarantined"):
        sa = _add_state(conn, KEY_A + pub[:4], pg=1)
        conn.execute("UPDATE semantic_states SET publication=? WHERE state_id=?",
                     (pub, sa))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE semantic_states SET publication='legacy'"
                     " WHERE tusemantic_key=?", (KEY_A + "stag",))
    conn.rollback()


def test_world_status_has_unfinalized(store):
    conn = store._conn
    conn.execute("INSERT INTO snapshot_builds(namespace_id, snapshot_id,"
                 " build_identity_sha, world_status) VALUES('ns','s','b','unfinalized')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO snapshot_builds(namespace_id, snapshot_id,"
                     " build_identity_sha, world_status) VALUES('ns','s2','b2','running')")
    conn.rollback()


def test_poison_exclusion_inv6_shape(store):
    """INV-6: poisoned keys have a per-world home (world_poisons +
    tu_private_payloads) and NO column on semantic_states."""
    conn = store._conn
    cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_states)")}
    assert not any("poison" in c for c in cols)
    wp = {r[1] for r in conn.execute("PRAGMA table_info(world_poisons)")}
    assert {"world_id", "component", "nonce", "poison_tag"} <= wp


def test_world_cascade_delete_local_only(store):
    """FKs cascade INTO world-scoped tables; deleting a world never touches
    shared semantic_states (shared deletion is GC-driven, Slice 5)."""
    conn = store._conn
    sa = _add_state(conn, KEY_A)
    conn.execute("INSERT INTO snapshot_builds(namespace_id, snapshot_id,"
                 " build_identity_sha, world_status) VALUES('ns','snap','bf','active')")
    wid = conn.execute("SELECT world_id FROM snapshot_builds").fetchone()[0]
    conn.execute("INSERT INTO tu_bindings(world_id, tu_context_id,"
                 " canon_source_path, source_blob_sha, ncc_digest_sha,"
                 " binding_status, disposition) VALUES(?, 'tu-x', 'src/x.c',"
                 " 'a'*64, 'b'*64, 'staging', 'ok')", (wid,))
    conn.execute("INSERT INTO world_poisons(world_id, component, nonce,"
                 " poison_tag) VALUES(?,?,?,?)", (wid, "rsp", "n", "p" * 32))
    conn.commit()
    conn.execute("DELETE FROM snapshot_builds WHERE world_id=?", (wid,))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM tu_bindings").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM world_poisons").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM semantic_states").fetchone()[0] == 1


# ══════════════════════ version gate (M-C §7) ════════════════════════════════

def _write_meta(path: Path, key: str, value: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE store_metadata SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()


def test_version_gate_refuse_newer_without_restamp(tmp_path):
    p = tmp_path / "proj" / "index-v2.db"
    ShadowStore.create(p).close()
    _write_meta(p, "store_version", str(STORE_VERSION + 1))
    with pytest.raises(StoreVersionError) as ei:
        ShadowStore.open(p)
    assert "client_restart_required" in str(ei.value)
    conn = sqlite3.connect(str(p))
    v = conn.execute("SELECT value FROM store_metadata WHERE key='store_version'"
                     ).fetchone()[0]
    conn.close()
    assert v == str(STORE_VERSION + 1)      # refuse-don't-restamp


def test_version_gate_refuse_older(tmp_path):
    p = tmp_path / "proj" / "index-v2.db"
    ShadowStore.create(p).close()
    _write_meta(p, "store_version", str(STORE_VERSION - 1))
    with pytest.raises(StoreGenerationError):
        ShadowStore.open(p)


def test_format_gate(tmp_path):
    p = tmp_path / "proj" / "index-v2.db"
    ShadowStore.create(p).close()
    _write_meta(p, "store_format", "sqlite")
    with pytest.raises(StoreFormatError) as ei:
        ShadowStore.open(p)
    assert "client_restart_required" in str(ei.value)
