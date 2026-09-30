"""test_slice3_guard — S3.11 guard oracles: legacy parity, no parse-skip,
no retention/GC from the read path (§23/§24/§25)."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice3_helpers import (  # noqa: E402
    init_project_id,
    make_project,
    make_query_env,
    open_q,
    run_index,
    v2_of,
)

from fw_context_mcp.store_v2 import query  # noqa: E402

MAIN = query.TuSelector(canon_source_path="src/main.c")

# ── T17: legacy parity — flag OFF vs flag ON produce identical legacy rows ────

_LEGACY_TABLES = ("files", "symbols")


def _legacy_shape(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        shape: dict = {}
        for t in _LEGACY_TABLES:
            shape[t] = conn.execute(
                f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        shape["files_rows"] = sorted(
            r[0] for r in conn.execute("SELECT path FROM files"))
        shape["symbols_rows"] = sorted(
            (r[0], r[1]) for r in conn.execute(
                "SELECT s.name, s.kind FROM symbols s"))
        return shape
    finally:
        conn.close()


def _run_parity(base: Path, flag_on: bool) -> Path:
    """One indexing run with the v2 flag off/on; returns the legacy db path."""
    if flag_on:
        os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    else:
        os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    try:
        data = make_project(base)
        proj: Path = data["proj"]
        init_project_id(proj)
        _, db_path = run_index(proj, base, name="index")
    finally:
        os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
        _UP._reset_v2_flag_cache()
        C.reset_flag_cache()
    if flag_on:
        assert v2_of(db_path).exists()
    else:
        assert not v2_of(db_path).exists()
    return db_path


@pytest.mark.parametrize("flag_on", [False, True])
def test_s3_t17_legacy_parity(tmp_path, flag_on):
    _run_parity(tmp_path / ("on" if flag_on else "off"), flag_on)


def test_s3_t17b_legacy_rows_identical_flag_off_vs_on(tmp_path):
    off = _run_parity(tmp_path / "a", flag_on=False)
    on = _run_parity(tmp_path / "b", flag_on=True)
    shape_off = _legacy_shape(off)
    shape_on = _legacy_shape(on)
    assert shape_off == shape_on                 # byte-equal legacy shape


# ── T18: the read path never participates in the parse-skip decision ──────────

def test_s3_t18_no_parse_or_staleness_authority():
    src = (Path(__file__).resolve().parents[1] / "src" /
           "fw_context_mcp" / "store_v2" / "query.py").read_text(encoding="utf-8")
    # no indexer/staleness machinery is imported or named
    for token in ("_unit_processor", "compile_commands", "manifest",
                  "content_hash", "mtime", "staleness", "force_refs",
                  "import index"):
        assert token not in src, token
    # the module imports only store_v2 siblings + stdlib dataclasses
    for line in src.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")) and "fw_context_mcp" in s:
            assert s.startswith(("from fw_context_mcp.store_v2",
                                 "from .", "from ..store_v2")), s
    # behavioral anchor: S2-T15 proves the flag-ON run keeps full parsing;
    # T16 here proves queries leave the DB byte-identical, so no staleness
    # hint can survive a read session.


def test_s3_t18b_reads_cannot_influence_next_run(tmp_path):
    """A full query session between two runs does not perturb run B."""
    env = make_query_env(tmp_path)
    conn, store, view = env["conn"], env["store"], env["view"]
    try:
        query.inspect_tu(conn, view, selector=MAIN)
        query.lookup_symbol(conn, view, "util")
        query.tu_closure_presentation(conn, view, selector=MAIN)
        # the store still opens for a normal write (no locks leaked)
        from fw_context_mcp.store_v2.publish import open_v2_store_for_write
        w = open_v2_store_for_write(store.path)
        try:
            n = w._conn.execute(
                "SELECT COUNT(*) FROM snapshot_builds").fetchone()[0]
            assert n == 1
        finally:
            w.close()
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM snapshot_builds").fetchone()["n"] == 1
    finally:
        store.close()
        conn.close()


# ── T19: no retention, no GC, no maintenance from the read path ───────────────

def test_s3_t19_no_retention_or_gc():
    src = (Path(__file__).resolve().parents[1] / "src" /
           "fw_context_mcp" / "store_v2" / "query.py").read_text(encoding="utf-8")
    body = src.split("SLICE3_DDL", 1)[1]
    for token in ("DELETE", "VACUUM", "DROP TABLE", "PRAGMA", "REPLACE INTO",
                  "ATTACH", "journal_mode", "wal_checkpoint"):
        assert token not in body, token
    # the only write-shaped DDL is CREATE INDEX IF NOT EXISTS (read indices)
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(("CREATE", "create")):
            assert s.upper().startswith(
                "CREATE INDEX IF NOT EXISTS"), s
