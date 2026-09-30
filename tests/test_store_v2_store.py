"""Slice-0 store bootstrap tests — explicit-create-only, metadata, indexes,
row-count guard, FK enforcement, path convention."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fw_context_mcp.store_v2 import (
    SCHEMA_GENERATION,
    STORE_FORMAT,
    STORE_VERSION,
    V2_DB_FILENAME,
    ShadowStore,
    StoreFormatError,
    v2_db_path,
)


@pytest.fixture()
def dbfile(tmp_path) -> Path:
    return tmp_path / "proj" / V2_DB_FILENAME


def test_create_open_roundtrip(dbfile):
    s = ShadowStore.create(dbfile)
    names = None
    try:
        assert s.path == dbfile
        assert s.verify_schema_generation() == SCHEMA_GENERATION
        meta = s.metadata()
        assert meta["store_format"] == STORE_FORMAT
        assert meta["store_version"] == str(STORE_VERSION)
        assert meta["digest_algorithm"] == "sha256"
        names = s.table_names()
    finally:
        s.close()
    s2 = ShadowStore.open(dbfile)
    try:
        assert s2.table_names() == names
    finally:
        s2.close()


def test_wal_side_files_belong_to_the_v2_store_only(dbfile):
    """When explicitly created, side files (-wal/-shm) live next to index-v2.db
    — never beside the legacy index.db (T19 complement)."""
    s = ShadowStore.create(dbfile)
    s._conn.execute("PRAGMA journal_mode=WAL")
    s._conn.execute("CREATE TABLE IF NOT EXISTS _wal_probe(x)")
    s._conn.commit()
    s.close()
    assert dbfile.exists()
    assert dbfile.name == "index-v2.db"
    # legacy filename is untouched anywhere in this tree
    assert not (dbfile.parent / "index.db").exists()


def test_create_is_the_only_writer_entrypoint(dbfile):
    """No implicit creation: open() never creates; nothing else writes."""
    with pytest.raises(FileNotFoundError):
        ShadowStore.open(dbfile)
    assert not dbfile.exists()
    ShadowStore.create(dbfile).close()
    assert dbfile.exists()


def test_foreign_keys_enforced_on_connection(dbfile):
    s = ShadowStore.create(dbfile)
    try:
        assert s._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        s.close()


def test_row_count_guard_rejects_unknown_table(dbfile):
    s = ShadowStore.create(dbfile)
    try:
        assert s.row_count("semantic_states") == 0
        with pytest.raises(ValueError):
            s.row_count("sqlite_master")
    finally:
        s.close()


def test_declared_indexes_present(dbfile):
    s = ShadowStore.create(dbfile)
    try:
        names = set(s.index_names())
        assert {"idx_tu_bindings_world", "idx_tu_bindings_state",
                "idx_tu_bindings_path", "idx_cert_rows_blob",
                "idx_cert_rows_path", "idx_symbols_core_usr"} <= names
    finally:
        s.close()


def test_metadata_seed_is_exactly_the_documented_set(dbfile):
    s = ShadowStore.create(dbfile)
    try:
        assert set(s.metadata()) == {
            "store_format", "store_version", "min_reader_generation",
            "schema_generation", "key_format_default", "digest_algorithm",
            "created_by",
            # F1 provenance (P-A): position_capture_semantics="f3r/v1" was
            # added to METADATA_SEED (schema.py:488) as the F3R position-capture
            # marker. The seed writes it; this test had not been updated to
            # include it — a stale-test drift, not a store bug. Updated to
            # match the actual documented seed (M3B C9 unblock).
            "position_capture_semantics",
        }
    finally:
        s.close()


def test_store_open_on_foreign_sqlite_file(tmp_path):
    p = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    with pytest.raises(StoreFormatError):
        ShadowStore.open(p)
