"""store_v2.publish — Slice-1 immutable SemanticState publication.

Implements the Phase-3 lifecycle machine (D2 §4/§5, SEMANTIC_STATE_LIFECYCLE §4)
over the frozen Slice-0 DDL, plus the minimal Slice-1 additive surface
(see SLICE1_SCHEMA_DELTA.md — the Slice-0 ``schema.py`` is untouched; these
tables are created idempotently here):

* ``slice1_meta``         — Slice-1 marker rows (per-store nonce, format tag).
* ``parser_generations``  — DIAGNOSTIC registry (identity → components → the
  deterministic generation; SLICE1_CLASS_C_DIGEST_SPEC §2).
* ``builtin_predefine_cache`` — §30-sanctioned cache keyed by the FULLY
  certified capture identity (compiler output, never project content).
* ``tu_certificates``     — per-TU certification records (incl. poison
  dispositions — INV-6 keeps poisoned keys OUT of ``semantic_states``; Slice 1
  has no world row yet, so the disposition lives here until Slice 2).
* ``slice1_state_cold``   — normalized cold payload per (key, generation).
* ``integrity_events``    — high-severity W-1 tripwire record.

Publication semantics (one-shot, D2 §4 + exp1 OBSERVED parity):

* ONE ``BEGIN IMMEDIATE`` transaction per publication: state row
  (``publication='published'``) + hot facts + cold payload.  Crash before
  commit ⇒ nothing published (orphan-free; SQLite atomicity).
* Convergence: an existing (key, generation) row with the SAME
  ``completeness_sha`` ⇒ no-op success (converged).  First-writer-wins on the
  ROW; determinism makes loser payloads equal by construction (R-T1 premise).
* W-1 tripwire (REQUIRED): existing row with a DIFFERENT digest ⇒ FAIL
  CLOSED — no merge, no overwrite, existing row untouched, high-severity
  integrity event recorded.
* A 'staging' row is the claim surface (D2 §5.2 owner lease): same digest +
  free/expired lease ⇒ completed (staged→published) in one transaction;
  active foreign lease ⇒ retriable ``staged_active``; different digest ⇒
  integrity event + refuse (stale claims never adopt divergent facts).
* ``quarantined`` ⇒ refuse + event (re-publication blocked until explicit
  identity repair, D2 §5.2).
* SQLITE_BUSY ⇒ bounded retry with backoff (E2).  ``PRAGMA synchronous=FULL``
  on every Slice-1 write connection.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .schema import V2_DB_FILENAME

SLICE1_FORMAT = "fw-context-store/2+slice1"

_SLICE1_DDL = (
    """
    CREATE TABLE IF NOT EXISTS slice1_meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parser_generations (
      identity_sha TEXT PRIMARY KEY,
      generation INTEGER NOT NULL UNIQUE,
      indexer_generation INTEGER NOT NULL,
      libclang_id TEXT NOT NULL,
      capture_binary_id TEXT NOT NULL,
      builtin_digest TEXT NOT NULL,
      builtin_count INTEGER NOT NULL,
      target_triple TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS builtin_predefine_cache (
      capture_key TEXT PRIMARY KEY,
      builtin_digest TEXT NOT NULL,
      builtin_count INTEGER NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tu_certificates (
      cert_id INTEGER PRIMARY KEY,
      cert_version TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('certified','poisoned')),
      poison_reason TEXT NOT NULL DEFAULT '',
      outcome TEXT NOT NULL DEFAULT '',
      tusemantic_key TEXT NOT NULL DEFAULT '',
      parser_generation INTEGER NOT NULL DEFAULT 0,
      context_id TEXT NOT NULL DEFAULT '',
      config_hash TEXT NOT NULL DEFAULT '',
      main_file TEXT NOT NULL,
      language TEXT NOT NULL DEFAULT '',
      l0_sha TEXT NOT NULL DEFAULT '',
      l1_sha TEXT NOT NULL DEFAULT '',
      l2_sha TEXT NOT NULL DEFAULT '',
      l3_sha TEXT NOT NULL DEFAULT '',
      ncc_serial TEXT NOT NULL DEFAULT '',
      source_blob_sha TEXT NOT NULL DEFAULT '',
      forced_legs TEXT NOT NULL DEFAULT '[]',
      closure TEXT NOT NULL DEFAULT '[]',
      macro_env_digest TEXT NOT NULL DEFAULT '',
      macro_env_count INTEGER NOT NULL DEFAULT 0,
      environment_digest TEXT NOT NULL DEFAULT '',
      parser_identity TEXT NOT NULL DEFAULT '{}',
      fact_set_digest TEXT NOT NULL DEFAULT '',
      timing_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS slice1_state_cold (
      tusemantic_key TEXT NOT NULL,
      parser_generation INTEGER NOT NULL,
      payload_format TEXT NOT NULL,
      payload TEXT NOT NULL,
      payload_digest TEXT NOT NULL,
      UNIQUE(tusemantic_key, parser_generation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS integrity_events (
      event_id INTEGER PRIMARY KEY,
      ts TEXT NOT NULL,
      kind TEXT NOT NULL,
      tusemantic_key TEXT NOT NULL,
      parser_generation INTEGER NOT NULL,
      existing_digest TEXT NOT NULL DEFAULT '',
      candidate_digest TEXT NOT NULL DEFAULT '',
      detail TEXT NOT NULL DEFAULT ''
    )
    """,
)


def ensure_slice1_schema(conn: sqlite3.Connection) -> None:
    """Idempotent Slice-1 additive DDL (Slice-0 ``schema.py`` untouched)."""
    for stmt in _SLICE1_DDL:
        conn.execute(stmt)
    conn.commit()


def open_v2_store_for_write(v2_path: Path):
    """Create-or-open the shadow v2 store, Slice-1 enabled, ``synchronous=FULL``."""
    from .store import ShadowStore
    path = Path(v2_path)
    if path.exists():
        store = ShadowStore.open(path)
    else:
        store = ShadowStore.create(path)
    conn = store._conn
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=FULL")
    ensure_slice1_schema(conn)
    # Slice-4A additive surfaces (idx_tu_cert_workkey, cert_dir_attestations,
    # reuse_admissions) — exception-isolated so a DDL failure can never
    # break Slice-1/2 or legacy indexing (SLICE3_DDL pattern).
    try:
        from .schema4a import ensure_slice4a_schema
        ensure_slice4a_schema(conn)
    except Exception:                                     # pragma: no cover
        pass
    return store


def store_nonce(store) -> str:
    """Per-STORE random nonce (poison discipline [SKC]§1.4)."""
    conn = store._conn
    row = conn.execute("SELECT value FROM slice1_meta WHERE key='store_nonce'") \
        .fetchone()
    if row:
        return row["value"]
    nonce = secrets.token_hex(16)
    conn.executemany(
        "INSERT OR IGNORE INTO slice1_meta(key, value) VALUES(?, ?)",
        [("store_nonce", nonce), ("format", SLICE1_FORMAT)])
    conn.commit()
    return nonce


def record_parser_identity(store, identity) -> None:
    """Diagnostics-only registry row (deterministic generation precomputed)."""
    conn = store._conn
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT OR IGNORE INTO parser_generations(identity_sha, generation, "
        "indexer_generation, libclang_id, capture_binary_id, builtin_digest, "
        "builtin_count, target_triple, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (identity.identity_sha, identity.generation, identity.indexer_generation,
         identity.libclang_id, identity.capture_binary_id, identity.builtin_digest,
         identity.builtin_count, identity.target_triple, now))
    conn.commit()


def insert_certificate_row(store, cert, *, outcome: str,
                           fact_set_digest: str,
                           dir_attestations: list | None = None) -> int:
    conn = store._conn
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    kr = cert.key_result
    cur = conn.execute(
        "INSERT INTO tu_certificates(cert_version, status, poison_reason, "
        "outcome, tusemantic_key, parser_generation, context_id, config_hash, "
        "main_file, language, l0_sha, l1_sha, l2_sha, l3_sha, ncc_serial, "
        "source_blob_sha, forced_legs, closure, macro_env_digest, "
        "macro_env_count, environment_digest, parser_identity, fact_set_digest, "
        "timing_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
        "?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cert.cert_version, cert.status, cert.poison_reason, outcome,
         cert.tusemantic_key, cert.parser_generation, cert.context_id,
         cert.config_hash, cert.main_file_display, cert.language,
         kr.L0 if kr else "", kr.L1 if kr else "", kr.L2 if kr else "",
         kr.L3 if kr else "", cert.ncc_serial, cert.source_blob_sha,
         json.dumps(list(cert.forced_legs)), json.dumps(cert.closure_rows()),
         cert.macro_env_digest, cert.macro_env_count,
         cert.inputs.environment_digest if cert.inputs else "",
         json.dumps(cert.parser_identity.as_dict()), fact_set_digest,
         json.dumps(cert.timings), now))
    if dir_attestations:
        # Slice-4A A5′ capture face — SAME transaction as the cert row;
        # rows were computed with cert_id=0, patch to the real id here.
        try:
            cert_id = int(cur.lastrowid)
            patched = []
            for r in dir_attestations:
                r = dict(r)
                r["cert_id"] = cert_id
                patched.append(r)
            from .attest import record_dir_attestations
            record_dir_attestations(store, patched)
        except Exception:                                 # pragma: no cover
            # Attestation persistence is fail-closed, never fatal.
            pass
    conn.commit()
    return int(cur.lastrowid)


def record_integrity_event(store, *, kind: str, key: str, generation: int,
                           existing_digest: str, candidate_digest: str,
                           detail: str) -> int:
    conn = store._conn
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute(
        "INSERT INTO integrity_events(ts, kind, tusemantic_key, "
        "parser_generation, existing_digest, candidate_digest, detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (now, kind, key, generation, existing_digest, candidate_digest, detail))
    conn.commit()
    return int(cur.lastrowid)


@dataclass
class PublishOutcome:
    outcome: str        # published_new | converged | staged_active |
                        # refused_conflict | refused_quarantined | busy
    state_id: int | None
    detail: str = ""


# ── one-shot publication (the production path) ───────────────────────────────

_BUSY_RETRIES = 4
_BUSY_BACKOFF_S = (0.05, 0.1, 0.2, 0.4)


def publish_one_shot(store, *, cert, facts, cold_payload, fact_digest: str,
                     owner: str) -> PublishOutcome:
    """State row + hot facts + cold payload in ONE short transaction."""
    conn = store._conn
    key = cert.tusemantic_key
    generation = cert.parser_generation
    kr = cert.key_result
    cold_digest = _cold_digest(cold_payload)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    last_busy: Exception | None = None
    for attempt in range(_BUSY_RETRIES):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT state_id, publication, completeness_sha, owner_lease "
                    "FROM semantic_states WHERE tusemantic_key=? AND "
                    "parser_generation=?", (key, generation)).fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO semantic_states(tusemantic_key, "
                        "parser_generation, key_format, l0_source_blob_sha, "
                        "l1_ncc_digest_sha, l2_closure_digest_sha, "
                        "l3_env_digest_sha, publication, completeness_sha, "
                        "digest_algorithm, fact_row_count, fact_schema_version, "
                        "payload_format, payload_ref, payload_digest_sha, "
                        "owner_lease, path_spelling_sensitive, created_at, "
                        "created_by_run) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
                        "?,?,?,?,?,?,?)",
                        (key, generation, cert.inputs.key_format, kr.L0, kr.L1,
                         kr.L2, kr.L3, "published", fact_digest,
                         "fwctx-fsd/v1+sha256", facts.fact_row_count, 1,
                         "normalized", None, cold_digest, None,
                         1 if cert.path_spelling_sensitive else 0, now, owner))
                    state_id = int(cur.lastrowid)
                    _insert_facts(conn, state_id, facts, key, generation,
                                  cold_payload, cold_digest)
                    conn.commit()
                    return PublishOutcome("published_new", state_id)

                publication = row["publication"]
                existing_digest = row["completeness_sha"] or ""
                state_id = int(row["state_id"])
                if publication == "published":
                    if existing_digest == fact_digest:
                        conn.commit()          # converged: no writes made
                        return PublishOutcome("converged", state_id,
                                              "identical digest")
                    _record_event_in_txn(conn, kind="same_key_different_digest",
                                         key=key, generation=generation,
                                         existing=existing_digest,
                                         candidate=fact_digest,
                                         detail="W-1 tripwire: refuse + keep")
                    conn.commit()
                    return PublishOutcome("refused_conflict", state_id,
                                          "same key, different fact_set_digest")
                if publication == "quarantined":
                    _record_event_in_txn(
                        conn, kind="publish_quarantined_refused", key=key,
                        generation=generation, existing=existing_digest,
                        candidate=fact_digest,
                        detail="quarantined state blocks publication")
                    conn.commit()
                    return PublishOutcome("refused_quarantined", state_id,
                                          "quarantined")
                # staging row = the claim surface (D2 §5.2)
                lease = json.loads(row["owner_lease"] or "{}")
                mine = lease.get("owner") == owner
                expired = float(lease.get("deadline", 0)) <= time.time()
                if not (mine or expired):
                    conn.commit()
                    return PublishOutcome("staged_active", state_id,
                                          "foreign active claim")
                if existing_digest != fact_digest:
                    _record_event_in_txn(
                        conn, kind="staged_claim_digest_mismatch", key=key,
                        generation=generation, existing=existing_digest,
                        candidate=fact_digest,
                        detail="stale claim holds divergent facts — refuse")
                    conn.commit()
                    return PublishOutcome("refused_conflict", state_id,
                                          "staged digest mismatch")
                conn.execute(
                    "DELETE FROM slice1_state_cold WHERE tusemantic_key=? AND "
                    "parser_generation=?", (key, generation))
                conn.execute(
                    "DELETE FROM semantic_symbols_core WHERE state_id=?",
                    (state_id,))
                conn.execute("UPDATE semantic_states SET publication='published', "
                             "owner_lease=NULL, completeness_sha=?, "
                             "fact_row_count=?, payload_digest_sha=?, "
                             "path_spelling_sensitive=?, "
                             "created_by_run=COALESCE(created_by_run, ?) "
                             "WHERE state_id=? AND publication='staging'",
                             (fact_digest, facts.fact_row_count, cold_digest,
                              1 if cert.path_spelling_sensitive else 0,
                              owner, state_id))
                _insert_facts(conn, state_id, facts, key, generation,
                              cold_payload, cold_digest)
                conn.commit()
                return PublishOutcome("published_new", state_id,
                                      "staged claim completed")
            except BaseException:
                conn.rollback()
                raise
        except sqlite3.OperationalError as exc:
            last_busy = exc
            if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                time.sleep(_BUSY_BACKOFF_S[min(attempt, len(_BUSY_BACKOFF_S) - 1)])
                continue
            raise
    return PublishOutcome("busy", None, f"sqlite busy after retries: {last_busy}")


def _cold_digest(cold_payload: dict) -> str:
    from .facts import cold_payload_digest
    return cold_payload_digest(cold_payload)


def _insert_facts(conn, state_id: int, facts, key: str, generation: int,
                  cold_payload: dict, cold_digest: str) -> None:
    conn.executemany(
        "INSERT INTO semantic_symbols_core(state_id, local_key, raw_usr, name, "
        "kind, is_definition, is_virtual, is_pure_virtual, is_template, "
        "signature) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(state_id, *row) for row in facts.symbols])
    conn.executemany(
        "INSERT INTO semantic_edges(state_id, local_key, edge_ordinal, "
        "edge_class, to_local_key, to_raw_usr, to_name, certainty, "
        "resolution_kind) VALUES(?,?,?,?,?,?,?,?,?)",
        [(state_id, *row) for row in facts.edges])
    conn.executemany(
        "INSERT INTO semantic_condition_index(state_id, region_ordinal, "
        "parent_region_ordinal, directive, condition, file_blob_sha) "
        "VALUES(?,?,?,?,?,?)",
        [(state_id, *row) for row in facts.conditions])
    conn.executemany(
        "INSERT INTO state_file_table(state_id, file_ordinal, role, blob_sha) "
        "VALUES(?,?,?,?)",
        [(state_id, *row) for row in facts.file_table])
    # F3 (Slice 6C-F3): store per-symbol source positions (non-keyed facts,
    # INV-3).  Old states without positions simply have 0 rows here —
    # historical retrieval fails explicitly (returns "no positions") rather
    # than reading live disk.
    if getattr(facts, 'positions', None):
        conn.executemany(
            "INSERT INTO semantic_state_positions(state_id, file_ordinal, "
            "fact_ordinal, symbol_local_key, line, col, end_line, end_col) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(state_id, *row) for row in facts.positions])
    # G5 (Dead-Code): supplementary Tier-A facts — GNU attr_flags + section
    # per symbol, and rootless edges (ctor_root, implicit_construct with
    # from_usr=None).  Deterministic from the parse, NOT in fact_set_digest.
    if getattr(facts, 'symbol_attrs', None):
        conn.executemany(
            "INSERT INTO semantic_symbol_attrs(state_id, local_key, "
            "attr_flags, section) VALUES(?,?,?,?)",
            [(state_id, *row) for row in facts.symbol_attrs])
    if getattr(facts, 'root_edges', None):
        for ordinal, (edge_class, to_local, to_raw_usr) in enumerate(
                facts.root_edges, start=1):
            conn.execute(
                "INSERT INTO semantic_state_roots(state_id, root_ordinal, "
                "edge_class, to_local_key, to_raw_usr) VALUES(?,?,?,?,?)",
                (state_id, ordinal, edge_class, to_local, to_raw_usr))
    conn.execute(
        "INSERT OR REPLACE INTO slice1_state_cold(tusemantic_key, "
        "parser_generation, payload_format, payload, payload_digest) "
        "VALUES(?,?,?,?,?)",
        (key, generation, "normalized",
         json.dumps(cold_payload, sort_keys=True, separators=(",", ":")),
         cold_digest))


def _record_event_in_txn(conn, *, kind: str, key: str, generation: int,
                         existing: str, candidate: str, detail: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT INTO integrity_events(ts, kind, tusemantic_key, "
        "parser_generation, existing_digest, candidate_digest, detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (now, kind, key, generation, existing, candidate, detail))


def publish_staged_completion(store, *, key: str, generation: int,
                              fact_digest: str, fact_row_count: int,
                              owner: str) -> PublishOutcome:
    """Complete a staging claim into a published state (claim holder only).

    Digest tripwire: a completion whose digest differs from the claim's is
    refused with an integrity event (stale claims never adopt divergent
    facts)."""
    conn = store._conn
    for attempt in range(_BUSY_RETRIES):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT state_id, publication, completeness_sha, owner_lease "
                    "FROM semantic_states WHERE tusemantic_key=? AND "
                    "parser_generation=?", (key, generation)).fetchone()
                if row is None:
                    conn.commit()
                    return PublishOutcome("refused_conflict", None,
                                          "no such claim")
                if row["publication"] != "staging":
                    conn.commit()
                    if row["publication"] == "published" and \
                            row["completeness_sha"] == fact_digest:
                        return PublishOutcome("converged", int(row["state_id"]))
                    return PublishOutcome("refused_conflict",
                                          int(row["state_id"]),
                                          f"state is {row['publication']}")
                lease = json.loads(row["owner_lease"] or "{}")
                if lease.get("owner") != owner and \
                        float(lease.get("deadline", 0)) > time.time():
                    conn.commit()
                    return PublishOutcome("staged_active", int(row["state_id"]),
                                          "foreign active claim")
                if (row["completeness_sha"] or "") != fact_digest:
                    _record_event_in_txn(
                        conn, kind="staged_claim_digest_mismatch", key=key,
                        generation=generation,
                        existing=row["completeness_sha"] or "",
                        candidate=fact_digest,
                        detail="completion digest differs from claim")
                    conn.commit()
                    return PublishOutcome("refused_conflict",
                                          int(row["state_id"]),
                                          "claim digest mismatch")
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                conn.execute(
                    "UPDATE semantic_states SET publication='published', "
                    "owner_lease=NULL, fact_row_count=? WHERE state_id=? AND "
                    "publication='staging'",
                    (fact_row_count, row["state_id"]))
                conn.commit()
                return PublishOutcome("published_new", int(row["state_id"]))
            except BaseException:
                conn.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                time.sleep(_BUSY_BACKOFF_S[min(attempt,
                                               len(_BUSY_BACKOFF_S) - 1)])
                continue
            raise
    return PublishOutcome("busy", None, "sqlite busy after retries")


# ── staging / claim surface (D2 §5.2; S1-T11/T12 exercises) ─────────────────

def stage_claim(store, *, key: str, generation: int, key_format: str,
                l0: str, l1: str, l2: str, l3: str, fact_digest: str,
                owner: str, lease_seconds: float = 120.0) -> PublishOutcome:
    """Create (or converge onto) a staging claim for (key, generation)."""
    conn = store._conn
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for attempt in range(_BUSY_RETRIES):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT state_id, publication, completeness_sha, owner_lease "
                    "FROM semantic_states WHERE tusemantic_key=? AND "
                    "parser_generation=?", (key, generation)).fetchone()
                lease = json.dumps({"owner": owner,
                                    "deadline": time.time() + lease_seconds,
                                    "heartbeat_at": time.time()})
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO semantic_states(tusemantic_key, "
                        "parser_generation, key_format, l0_source_blob_sha, "
                        "l1_ncc_digest_sha, l2_closure_digest_sha, "
                        "l3_env_digest_sha, publication, completeness_sha, "
                        "digest_algorithm, fact_row_count, "
                        "fact_schema_version, payload_format, owner_lease, "
                        "path_spelling_sensitive, created_at, created_by_run) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, generation, key_format, l0, l1, l2, l3,
                         "staging", fact_digest, "fwctx-fsd/v1+sha256",
                         None, 1, "normalized", lease, 0, now, owner))
                    conn.commit()
                    return PublishOutcome("staged_new", int(cur.lastrowid))
                if row["publication"] == "published":
                    conn.commit()
                    if row["completeness_sha"] == fact_digest:
                        return PublishOutcome("converged", int(row["state_id"]),
                                              "already published, same digest")
                    _record_event_in_txn(
                        conn, kind="stage_claim_conflict", key=key,
                        generation=generation,
                        existing=row["completeness_sha"] or "",
                        candidate=fact_digest,
                        detail="stage claim vs published digest mismatch")
                    return PublishOutcome("refused_conflict",
                                          int(row["state_id"]),
                                          "published digest differs")
                if row["publication"] == "quarantined":
                    conn.commit()
                    return PublishOutcome("refused_quarantined",
                                          int(row["state_id"]), "quarantined")
                # existing staging claim: owner-conditional takeover
                old = json.loads(row["owner_lease"] or "{}")
                if old.get("owner") != owner and \
                        float(old.get("deadline", 0)) > time.time():
                    conn.commit()
                    return PublishOutcome("staged_active", int(row["state_id"]),
                                          "foreign active claim")
                conn.execute(
                    "UPDATE semantic_states SET owner_lease=?, "
                    "completeness_sha=? WHERE state_id=? AND "
                    "publication='staging'",
                    (lease, fact_digest, row["state_id"]))
                conn.commit()
                return PublishOutcome("staged_adopted", int(row["state_id"]))
            except BaseException:
                conn.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                time.sleep(_BUSY_BACKOFF_S[min(attempt,
                                               len(_BUSY_BACKOFF_S) - 1)])
                continue
            raise
    return PublishOutcome("busy", None, "sqlite busy after retries")


def heartbeat_claim(store, *, key: str, generation: int, owner: str,
                    lease_seconds: float = 120.0) -> bool:
    """Owner-conditional lease renewal; False when the claim is not ours."""
    conn = store._conn
    row = conn.execute(
        "SELECT state_id, owner_lease FROM semantic_states WHERE "
        "tusemantic_key=? AND parser_generation=? AND publication='staging'",
        (key, generation)).fetchone()
    if row is None:
        return False
    lease = json.loads(row["owner_lease"] or "{}")
    if lease.get("owner") != owner:
        return False
    cur = conn.execute(
        "UPDATE semantic_states SET owner_lease=? WHERE state_id=? AND "
        "publication='staging'",
        (json.dumps({"owner": owner, "deadline": time.time() + lease_seconds,
                     "heartbeat_at": time.time()}), row["state_id"]))
    conn.commit()
    return (cur.rowcount or 0) > 0


def get_published_state(store, *, key: str, generation: int):
    """Test-inspection helper: the published row ONLY (INV-8 gate shape)."""
    row = store._conn.execute(
        "SELECT * FROM semantic_states WHERE tusemantic_key=? AND "
        "parser_generation=? AND publication='published'",
        (key, generation)).fetchone()
    return row


def integrity_event_count(store, *, key: str, generation: int) -> int:
    return int(store._conn.execute(
        "SELECT count(*) FROM integrity_events WHERE tusemantic_key=? AND "
        "parser_generation=?", (key, generation)).fetchone()[0])
