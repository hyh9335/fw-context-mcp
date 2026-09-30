"""store_v2.schema4a — Slice-4A additive v2 DDL (SLICE3_DDL pattern).

Everything here is ``CREATE ... IF NOT EXISTS`` over the ALREADY-FROZEN
Slice-0/1/2 surfaces.  No frozen table is touched, no legacy schema changes
(task §45/§48).  Applied by :func:`ensure_slice4a_schema`, which callers
invoke exception-isolated so a DDL failure can never break legacy indexing.

Adds exactly three surfaces:

* ``idx_tu_cert_workkey`` — A3 candidate-lookup index over the WorkKey pair
  ``(source_blob_sha, l1_sha)`` on ``tu_certificates``.  The WorkKey is
  candidate SELECTION only, never reuse authority (task §31).
* ``cert_dir_attestations`` — the A5′ capture face: per-certificate
  directory attestation rows (normcase key + realpath + ``st_dev``/``st_ino``
  pin + folded/sorted name-kind listing digest).  ``NULL`` pins mean "pin
  unavailable" and admission falls through (fail closed, R3 §3.2).
* ``reuse_admissions`` — diagnostic/audit provenance for every admission
  attempt (task §23).  NEVER semantic truth; on MISS it is the only
  admission-specific write before the full path runs.
"""

from __future__ import annotations

import sqlite3

SLICE4A_DDL = (
    # ── A3: WorkKey candidate index (selection only, task §31) ──────────────
    """
    CREATE INDEX IF NOT EXISTS idx_tu_cert_workkey
      ON tu_certificates(source_blob_sha, l1_sha)
    """,
    # ── A5′: per-certificate directory attestations (capture face) ─────────
    """
    CREATE TABLE IF NOT EXISTS cert_dir_attestations (
      cert_id       INTEGER NOT NULL,
      dir_ordinal   INTEGER NOT NULL,
      kind          TEXT NOT NULL,
      dir_normcase  TEXT NOT NULL,
      dir_realpath  TEXT NOT NULL,
      st_dev        INTEGER,
      st_ino        INTEGER,
      listing_sha   TEXT NOT NULL,
      entry_count   INTEGER NOT NULL,
      captured_at   TEXT NOT NULL,
      UNIQUE(cert_id, dir_normcase, kind)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cert_dir_attestations_cert
      ON cert_dir_attestations(cert_id)
    """,
    # ── §23: admission provenance (diagnostic, never semantic truth) ───────
    """
    CREATE TABLE IF NOT EXISTS reuse_admissions (
      admission_id     INTEGER PRIMARY KEY,
      ts               TEXT NOT NULL,
      run_id           TEXT NOT NULL DEFAULT '',
      tu_context_id    TEXT NOT NULL DEFAULT '',
      main_file        TEXT NOT NULL DEFAULT '',
      config_hash      TEXT NOT NULL DEFAULT '',
      admitted         INTEGER NOT NULL,
      fallback_reason  TEXT NOT NULL DEFAULT '',
      cert_id          INTEGER,
      state_key        TEXT NOT NULL DEFAULT '',
      state_generation INTEGER,
      binding_status   TEXT NOT NULL DEFAULT '',
      checks_json      TEXT NOT NULL DEFAULT '{}',
      evidence_json    TEXT NOT NULL DEFAULT '{}',
      timing_json      TEXT NOT NULL DEFAULT '{}',
      legacy_gate      TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reuse_admissions_ctx
      ON reuse_admissions(tu_context_id, ts)
    """,
)


def ensure_slice4a_schema(conn: sqlite3.Connection) -> None:
    """Idempotent Slice-4A additive DDL (Slice-0/1/2 surfaces untouched)."""
    for stmt in SLICE4A_DDL:
        conn.execute(stmt)
    conn.commit()
