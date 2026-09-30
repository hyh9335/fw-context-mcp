"""store_v2.store — Slice-0 shadow v2 store bootstrap (explicit-create only).

M-C two-store decision (`MIGRATION_STRATEGY.md`): the v2 store is a PHYSICALLY
SEPARATE file — ``<db_dir>/<project_id>/index-v2.db`` (§4 criterion 5, DESIGN).
The legacy ``index.db`` is never opened, referenced, or modified by this
module. No migration, no dual write, no legacy adoption, no cutover.

Store header (M-C §7, DESIGN): a ``store_metadata`` singleton table carrying
``store_format = 'fw-context-store/2'`` and an ORDINAL ``store_version``
(never a column-set hash), ``min_reader_generation``, ``created_by``.
* ``store_format`` mismatch → refuse with the product's
  ``client_restart_required`` vocabulary (newer index + older reader = the
  reader's problem).
* Downgrade = refuse, never re-stamp: an open against a store whose ordinal
  ``store_version`` differs from this build's raises :class:`StoreVersionError`
  for a NEWER store (down-stamp hazard fixed conceptually at M-C §7) and
  :class:`StoreGenerationError` for an older one; Slice 0 never rewrites the
  version row in either direction.

Creation trigger: ONLY an explicit Slice-0 shadow/test path (flag-gated;
``cfg.index.shadow_v2_enabled``). With v2 disabled nothing in this package
runs: no v2 DB, no v2 WAL, no v2 lock, no v2 metadata write, no legacy
behavior change (T19).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import (
    DDL_STATEMENTS,
    INDEX_STATEMENTS,
    METADATA_SEED,
    SCHEMA_GENERATION,
    STORE_FORMAT,
    STORE_VERSION,
    V2_DB_FILENAME,
)

__all__ = [
    "ShadowStore",
    "StoreFormatError",
    "StoreVersionError",
    "StoreGenerationError",
    "v2_db_path",
]


class StoreFormatError(RuntimeError):
    """The file is not a fw-context v2 store (store_format mismatch)."""


class StoreVersionError(RuntimeError):
    """The store was written by a NEWER generation — refuse, never re-stamp."""


class StoreGenerationError(RuntimeError):
    """The store is OLDER than this build's store_version (no silent restamp)."""


def v2_db_path(db_dir: Path, project_id: str) -> Path:
    """M-C path convention: ``<db_dir>/<project_id>/index-v2.db``.

    Mirrors the legacy resolution rule
    (``cfg.index.db_dir / project_id / "index.db"``, ~20 call sites) with the
    M-C v2 filename — the two stores can never collide.
    """
    return Path(db_dir) / project_id / V2_DB_FILENAME


class ShadowStore:
    """Explicit-create/open handle for the Slice-0 v2 shadow store.

    Slice 0 creates STRUCTURE only: no world rows, no semantic facts, no
    publication, no retention/GC, no query path. The store is disposable as a
    whole (delete the file = complete rollback; PHASE4 task §18).
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self._conn = conn
        self.path = path

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def create(cls, path: Path | str) -> "ShadowStore":
        """Create a NEW v2 store (must not already exist as a non-empty file).

        Applies the Slice-0 DDL, seeds ``store_metadata``, and commits once.
        Parent directories are created on demand.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            existing = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            if existing:
                conn.rollback()
                raise StoreFormatError(
                    f"refusing to create: {path} already contains tables "
                    f"(open it instead)")
            for stmt in DDL_STATEMENTS:
                conn.execute(stmt)
            for idx in INDEX_STATEMENTS:
                conn.execute(idx)
            conn.executemany(
                "INSERT INTO store_metadata(key, value) VALUES(?, ?)",
                [(k, v) for k, v in METADATA_SEED])
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        return cls(conn, path)

    @classmethod
    def open(cls, path: Path | str) -> "ShadowStore":
        """Open an EXISTING v2 store with the full version gate.

        Raises StoreFormatError / StoreVersionError / StoreGenerationError;
        never modifies stored metadata (refuse-don't-restamp).
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"v2 store not found: {path}")
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        meta = cls._read_metadata(conn)
        fmt = meta.get("store_format")
        if fmt != STORE_FORMAT:
            conn.close()
            raise StoreFormatError(
                f"store_format mismatch: found {fmt!r}, expected {STORE_FORMAT!r} "
                f"(client_restart_required: newer index + older reader = the "
                f"reader's problem)")
        try:
            found_version = int(meta.get("store_version", ""))
        except (TypeError, ValueError):
            conn.close()
            raise StoreFormatError(
                f"store_version missing/non-ordinal in {path} (M-C §7: ordinal, "
                f"never a column-set hash)")
        if found_version > STORE_VERSION:
            conn.close()
            raise StoreVersionError(
                f"store_version {found_version} > build's {STORE_VERSION}: "
                f"refuse-don't-restamp (client_restart_required)")
        if found_version < STORE_VERSION:
            conn.close()
            raise StoreGenerationError(
                f"store_version {found_version} < build's {STORE_VERSION}: "
                f"no silent re-stamp at Slice 0 (explicit upgrade lands later)")
        return cls(conn, path)

    @staticmethod
    def _read_metadata(conn: sqlite3.Connection) -> dict[str, str]:
        try:
            rows = conn.execute("SELECT key, value FROM store_metadata").fetchall()
        except sqlite3.OperationalError as exc:
            raise StoreFormatError(
                f"no store_metadata header (not a v2 store): {exc}") from exc
        return {r["key"]: r["value"] for r in rows}

    # ── introspection (Slice-0 test surface; NO production read path) ────────
    def metadata(self) -> dict[str, str]:
        """Full store header (M-C §7 fields + schema generation anchors)."""
        return self._read_metadata(self._conn)

    def table_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def index_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
            "ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def row_count(self, table: str) -> int:
        """Row count of one table (validated against the known DDL set)."""
        from .schema import DDL_STATEMENTS  # local import: test-surface only
        import re
        known = False
        for stmt in DDL_STATEMENTS:
            m = re.search(r"CREATE TABLE (\w+)\s*\(", stmt, re.S)
            if m and m.group(1) == table:
                known = True
                break
        if not known:
            raise ValueError(f"unknown table {table!r}")
        return int(self._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def verify_schema_generation(self) -> int:
        """schema_generation anchor (PHASE4 task §27) as stored at creation."""
        meta = self.metadata()
        return int(meta["schema_generation"])

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ShadowStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
