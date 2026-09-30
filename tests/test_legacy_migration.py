"""AUD-R7 — the real rows/2 fixture: in-place migration preserves data (D7).

W0 (R7_*.log) established the flow of record against a REAL historical
rows/2 DB built by the d2666dc indexer:

1. HEAD read of the old DB → ``status="reindex_needed"`` with a
   ``row_format_mismatch`` reason — a read NEVER migrates silently (ADR-005).
2. HEAD incremental index → migrates IN PLACE rows/2 → rows/3
   (canonical rows preserved, per-context minting), then rewrites the
   active config → ``status="ready"`` with data preserved.

The checked-in fixture ``tests/fixtures/legacy_rows2/index.db`` is that
real DB (sha256 + counts + builder commit pinned in provenance.json — NOT
synthesized; the generator-test alternative was REJECTED in design
triage).  The five frozen-oracle tests in test_schema_migration_slice_b.py
consume the same fixture; this module pins the END-TO-END migration flow
the W0 evidence log describes.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import CURRENT_ROW_FORMAT

WORKTREE = Path(__file__).resolve().parents[1]
FIXTURE_DIR = WORKTREE / "tests" / "fixtures" / "legacy_rows2"
FIXTURE_DB = FIXTURE_DIR / "index.db"
PROVENANCE = FIXTURE_DIR / "provenance.json"


@pytest.fixture(scope="module")
def fixture_provenance() -> dict:
    assert FIXTURE_DB.exists(), (
        f"the checked-in rows/2 fixture is missing: {FIXTURE_DB}"
    )
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def _cli(args: list[str], cwd: Path, timeout: int = 240) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(WORKTREE / "src")
    return subprocess.run(
        [sys.executable, "-m", "fw_context_mcp.cli"] + args,
        cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
    )


def _reid_fixture_copy(dest: Path, project_id: str, root: Path) -> Path:
    """Copy the fixture DB and re-point its project identity at *root*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE_DB, dest)
    conn = sqlite3.connect(dest)
    try:
        with conn:
            conn.execute(
                "UPDATE projects SET project_id=?, root_path=?",
                (project_id, str(root)),
            )
            conn.execute(
                "UPDATE build_configs SET project_id=?, compile_commands_path=?",
                (project_id, str(root / "compile_commands.json")),
            )
    finally:
        conn.close()
    return dest


def _temp_legacy_project(tmp_path: Path) -> tuple[Path, Path]:
    """Temp project (real source + cc.json) with the fixture DB installed.

    Returns (project_root, index_db_path).  Uses ``fw-context init`` so the
    layout matches every other CLI-driven test (UUID4 project id, local.toml
    db_dir redirect).
    """
    proj = tmp_path / "legacy_proj"
    (proj / ".fw-context").mkdir(parents=True)
    (proj / "old_fixture.c").write_text(
        "static int old_fn(int v) {\n    return v + 1;\n}\n"
        "\nint caller_fn(int v) {\n    return old_fn(v);\n}\n",
        encoding="utf-8",
    )
    cc = [{"directory": str(proj), "file": "old_fixture.c",
           "arguments": ["gcc", "-std=c11", "-c", "old_fixture.c",
                         "-o", "old_fixture.o"]}]
    (proj / "compile_commands.json").write_text(json.dumps(cc), encoding="utf-8")
    index_dir = tmp_path / "index"
    (proj / ".fw-context" / "local.toml").write_text(
        '[index]\ndb_dir = "' + str(index_dir).replace("\\", "/") + '"\n',
        encoding="utf-8",
    )

    init = _cli(["init", "--project", str(proj)], cwd=proj)
    assert init.returncode == 0, f"init failed: {init.stdout}\n{init.stderr}"

    import tomllib

    cfg = tomllib.loads((proj / ".fw-context" / "config.toml").read_text(encoding="utf-8"))
    pid = cfg["project"]["id"]
    db_path = _reid_fixture_copy(index_dir / pid / "index.db", pid, proj)
    return proj, db_path


def test_fixture_provenance_sha256_and_shape(fixture_provenance: dict):
    """The checked-in fixture is exactly what provenance.json pins (D7)."""
    import hashlib

    data = FIXTURE_DB.read_bytes()
    assert hashlib.sha256(data).hexdigest() == fixture_provenance["sha256"], (
        "checked-in index.db does not match its provenance sha256 — the "
        "fixture must never be touched by hand"
    )
    assert fixture_provenance["row_format"] == "fw-context-rows/2"
    assert fixture_provenance["builder_commit"] == "d2666dc"
    assert fixture_provenance["schema_shape"]["call_sites_table_present"] is False, (
        "rows/2 predates the call_sites table — the fixture must keep that shape"
    )
    conn = sqlite3.connect(FIXTURE_DB)
    try:
        rf = conn.execute("SELECT row_format FROM build_configs").fetchone()[0]
        assert rf == "fw-context-rows/2"
    finally:
        conn.close()


@pytest.mark.libclang
class TestLegacyRows2Migration:
    def test_head_read_reports_row_format_mismatch_without_migrating(
        self, tmp_path: Path,
    ):
        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        proj, db_path = _temp_legacy_project(tmp_path)
        before = db_path.read_bytes()

        build = get_active_build(project_root=str(proj))
        assert build["status"] == "reindex_needed", (
            f"rows/2 DB must ask for a reindex, got {build.get('status')} "
            f"({build.get('reindex_reasons')})"
        )
        assert any("row_format_mismatch" in r
                   for r in build.get("reindex_reasons", [])), (
            f"the rows/2 mismatch must be named: {build.get('reindex_reasons')}"
        )
        # A read never migrates silently (ADR-005): the DB bytes are unchanged.
        assert db_path.read_bytes() == before, (
            "get_active_build must not rewrite the legacy DB"
        )

    def test_incremental_index_migrates_in_place_preserving_data(
        self, tmp_path: Path,
    ):
        from fw_context_mcp.mcp.handlers.maintenance import get_active_build

        proj, db_path = _temp_legacy_project(tmp_path)

        result = _cli(
            ["index", str(proj / "compile_commands.json"), "--project", str(proj),
             "--no-embeddings", "--no-analyze", "-v"],
            cwd=proj,
        )
        assert result.returncode == 0, (
            f"incremental index over a rows/2 DB failed:\n{result.stdout}\n{result.stderr}"
        )

        # IN PLACE: the same file path holds the migrated data — no sibling
        # copies (the -shm/-wal WAL sidecars are part of the same database).
        assert db_path.exists()
        siblings = [p.name for p in db_path.parent.iterdir()]
        strays = [n for n in siblings
                  if n.startswith("index.db")
                  and n not in ("index.db", "index.db-shm", "index.db-wal")]
        assert not strays, f"migration left stray DB copies: {strays}"

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT row_format, config_hash FROM build_configs"
            ).fetchall()
            assert any(r["row_format"] == CURRENT_ROW_FORMAT for r in rows), (
                f"no {CURRENT_ROW_FORMAT} build after the migration run: "
                f"{[tuple(r) for r in rows]}"
            )
            names = {r["name"] for r in conn.execute(
                "SELECT DISTINCT name FROM symbols").fetchall()}
            assert {"old_fn", "caller_fn"} <= names, (
                f"data must survive the migration: symbol names {sorted(names)}"
            )
            n_refs = conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
            assert n_refs >= 1, "refs must survive the migration"
        finally:
            conn.close()

        build = get_active_build(project_root=str(proj))
        assert build["status"] == "ready", (
            f"post-migration read must be ready, got {build.get('status')} "
            f"({build.get('reindex_reasons')})"
        )
        assert not build.get("reindex_needed"), build.get("reindex_reasons")
