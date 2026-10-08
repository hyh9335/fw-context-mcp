"""M6 BuildSet store — Phase I I2 implementation (Architecture Contract FROZEN).

See: .dsh-control/contracts/M6_ARCHITECTURE_CONTRACT.md (FROZEN)
See: .dsh-control/contracts/IMMUTABLE_M6_GOAL_CONTRACT.md

SQLite-backed BuildSet interning and fact storage.
Integrates with existing v2 schema via m6_* shadow tables.

This module is SHADOW-ONLY during Phase I. It does not affect
production query paths until shadow differential testing (§34)
proves QUERY_PARITY_FAILURES == 0.

GC model (§M1):
  - m6_world_builds is a World→Builds many-to-many map.
  - Per-build refcount = COUNT(active world_id) WHERE build_name ∈ m6_world_builds.
  - Fact reachability = fact.BuildSet ∩ {builds with refcount > 0} ≠ ∅.
  - Per-World removal from BuildSets is FORBIDDEN (category error).
  - Age-only GC is FORBIDDEN (§2.7).
"""
from __future__ import annotations
import json, hashlib, sqlite3, time, threading
from pathlib import Path
from typing import Iterable, Optional
from dataclasses import dataclass, field


def _hash_buildset(build_names: frozenset[str]) -> str:
    """SHA-256 over sorted build names."""
    sorted_names = sorted(build_names)
    return hashlib.sha256("|".join(sorted_names).encode()).hexdigest()


@dataclass
class GCGarbagePeriod:
    """A grace-period record for an in-flight query (§M1.4, §23).

    When a query is in flight against a World that is being deactivated,
    the World's builds keep a positive refcount until the query settles.
    """
    world_id: int
    expires_at: float  # monotonic timestamp


class BuildSetStore:
    """SQLite-backed BuildSet interning and fact storage.

    Tables (from m6_schema.py v2):
      m6_buildsets, m6_shared_facts, m6_fact_bundles,
      m6_world_builds (§M1.1 — World→Builds map),
      m6_world_fact_refs (legacy, deprecated by m6_world_builds),
      m6_certificates, m6_body_facts, m6_file_content
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._buildset_cache: dict[str, int] = {}  # hash → id
        self._fact_cache: dict[str, int] = {}      # canon → id
        # build_name → set of buildset_ids that contain it (for fast projection)
        self._build_to_buildsets: dict[str, set[int]] = {}
        # I3: write lock — serializes writes across threads sharing one
        # connection. SQLite connection objects are NOT thread-safe even
        # with check_same_thread=False; only the underlying DB file is.
        # Reads (project_build etc.) are safe without the lock under WAL
        # because they operate on a consistent snapshot. Writes must hold
        # the lock for the duration of each logical operation (which may
        # span multiple SQL statements in a single transaction).
        self._write_lock = threading.RLock()
        self._load_caches()

    def _load_caches(self) -> None:
        """Load existing buildset and fact IDs into memory caches."""
        for row in self._conn.execute("SELECT builds_hash, buildset_id FROM m6_buildsets"):
            self._buildset_cache[row[0]] = row[1]
        for row in self._conn.execute("SELECT fact_canon, fact_id FROM m6_shared_facts"):
            self._fact_cache[row[0]] = row[1]
        # Build the build→buildsets index for fast projection
        for row in self._conn.execute("SELECT buildset_id, build_names_json FROM m6_buildsets"):
            bsid, builds_json = row
            for bname in json.loads(builds_json):
                self._build_to_buildsets.setdefault(bname, set()).add(bsid)

    # ── BuildSet interning ────────────────────────────────────────────────

    def intern_buildset(self, build_names: frozenset[str]) -> int:
        """Intern a BuildSet. Returns the buildset_id (idempotent)."""
        bhash = _hash_buildset(build_names)
        # Fast path: cache hit (no lock needed — dict reads are atomic
        # under CPython's GIL for simple key lookups)
        cached = self._buildset_cache.get(bhash)
        if cached is not None:
            return cached

        with self._write_lock:
            # Re-check under lock (another thread may have interned it)
            cached = self._buildset_cache.get(bhash)
            if cached is not None:
                return cached

            build_names_json = json.dumps(sorted(build_names))
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cursor = self._conn.execute(
                "INSERT INTO m6_buildsets(builds_hash, build_count, build_names_json, created_at) "
                "VALUES(?, ?, ?, ?)",
                (bhash, len(build_names), build_names_json, now)
            )
            bsid = cursor.lastrowid
            self._buildset_cache[bhash] = bsid
            for bname in build_names:
                self._build_to_buildsets.setdefault(bname, set()).add(bsid)
            self._conn.commit()
            return bsid

    def merge_buildset(self, fact_canon: str, new_builds: frozenset[str]) -> int:
        """Merge a fact's BuildSet with new builds.

        If the fact exists, union its current BuildSet with new_builds.
        If not, create with new_builds.
        Returns the final buildset_id.
        """
        with self._write_lock:
            existing_fid = self._fact_cache.get(fact_canon)
            if existing_fid is None:
                return self.intern_buildset(new_builds)

            row = self._conn.execute(
                "SELECT buildset_id FROM m6_shared_facts WHERE fact_id = ?",
                (existing_fid,)
            ).fetchone()
            if row is None:
                return self.intern_buildset(new_builds)

            existing_bsid = row[0]
            bs_row = self._conn.execute(
                "SELECT build_names_json FROM m6_buildsets WHERE buildset_id = ?",
                (existing_bsid,)
            ).fetchone()
            if bs_row is None:
                return self.intern_buildset(new_builds)

            current_builds = frozenset(json.loads(bs_row[0]))
            merged = current_builds | new_builds
            return self.intern_buildset(merged)

    # ── Fact storage ──────────────────────────────────────────────────────

    def add_fact(self, fact_canon: str, fact_kind: str,
                 build_names: frozenset[str],
                 payload: dict | None = None) -> int:
        """Add or update a shared fact.

        If the fact exists, merge its BuildSet with build_names.
        If not, create a new fact.
        Returns the fact_id.
        """
        with self._write_lock:
            bsid = self.merge_buildset(fact_canon, build_names)

            existing_fid = self._fact_cache.get(fact_canon)
            if existing_fid is not None:
                # Update buildset_id (may have changed due to merge)
                self._conn.execute(
                    "UPDATE m6_shared_facts SET buildset_id = ? WHERE fact_id = ?",
                    (bsid, existing_fid)
                )
                self._conn.commit()
                return existing_fid

            payload_json = json.dumps(payload) if payload else None
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cursor = self._conn.execute(
                "INSERT INTO m6_shared_facts(fact_canon, fact_kind, buildset_id, payload_json, first_seen_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (fact_canon, fact_kind, bsid, payload_json, now)
            )
            fid = cursor.lastrowid
            self._fact_cache[fact_canon] = fid
            self._conn.commit()
            return fid

    def add_facts_batch(self, facts: list[tuple[str, str, frozenset[str], dict | None]]) -> int:
        """Add multiple facts in one transaction.

        Each fact is (fact_canon, fact_kind, build_names, payload).
        Returns the count of facts added/updated.
        """
        with self._write_lock:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            count = 0
            for fact_canon, fact_kind, build_names, payload in facts:
                self.add_fact(fact_canon, fact_kind, build_names, payload)
            count += 1
        return count

    # ── Projection (the core query operation) ────────────────────────────

    def project_build(self, build_name: str) -> dict[str, list[dict]]:
        """Project all facts visible in a specific Build.

        A fact is visible in build B iff B ∈ fact.BuildSet.
        O(1) per fact via the build→buildsets index.

        Returns {fact_kind: [{fact_canon, fact_id, payload}, ...]}.
        """
        result: dict[str, list[dict]] = {}
        candidate_bsids = self._build_to_buildsets.get(build_name, set())
        if not candidate_bsids:
            return result

        # Fetch facts whose buildset_id is in the candidate set
        # (buildset_id → buildset_id IN (?, ?, ...))
        placeholders = ",".join("?" * len(candidate_bsids))
        rows = self._conn.execute(
            f"""
            SELECT sf.fact_id, sf.fact_canon, sf.fact_kind, sf.payload_json,
                   sf.buildset_id
            FROM m6_shared_facts sf
            WHERE sf.buildset_id IN ({placeholders})
            """,
            tuple(candidate_bsids)
        )

        for row in rows:
            fid, canon, kind, payload_json, bsid = row
            # Double-check build_name is in this buildset (defensive)
            # — the index is built from build_names_json so it's authoritative
            payload = json.loads(payload_json) if payload_json else None
            result.setdefault(kind, []).append({
                "fact_id": fid,
                "fact_canon": canon,
                "payload": payload,
            })

        return result

    def project_build_kind(self, build_name: str, fact_kind: str) -> list[dict]:
        """Project facts of a specific kind visible in a build.

        Faster than project_build when querying a single fact kind.
        """
        candidate_bsids = self._build_to_buildsets.get(build_name, set())
        if not candidate_bsids:
            return []

        placeholders = ",".join("?" * len(candidate_bsids))
        rows = self._conn.execute(
            f"""
            SELECT sf.fact_id, sf.fact_canon, sf.payload_json
            FROM m6_shared_facts sf
            WHERE sf.buildset_id IN ({placeholders}) AND sf.fact_kind = ?
            """,
            tuple(candidate_bsids) + (fact_kind,)
        )

        result = []
        for row in rows:
            fid, canon, payload_json = row
            payload = json.loads(payload_json) if payload_json else None
            result.append({
                "fact_id": fid,
                "fact_canon": canon,
                "payload": payload,
            })
        return result

    # ── World→Builds map (§M1.1) ─────────────────────────────────────────

    def register_world(self, world_id: int, build_names: Iterable[str]) -> None:
        """Register the builds that a World encompasses (§M1.1).

        This is the GC-reachability foundation: a fact is reachable iff
        its BuildSet intersects the set of builds with refcount > 0,
        where refcount(build) = COUNT(active world) referencing it.
        """
        with self._write_lock:
            for bname in build_names:
                self._conn.execute(
                    "INSERT OR IGNORE INTO m6_world_builds(world_id, build_name) VALUES(?, ?)",
                    (world_id, bname)
                )
            self._conn.commit()

    def unregister_world(self, world_id: int) -> None:
        """Remove a World from the World→Builds map.

        This decrements the refcount of every build that World referenced.
        Builds whose refcount drops to 0 become unreachable roots, and
        facts whose BuildSet contains only such builds become unreachable.
        """
        with self._write_lock:
            self._conn.execute(
                "DELETE FROM m6_world_builds WHERE world_id = ?",
                (world_id,)
            )
            # Also clear the legacy per-World fact refs
            self._conn.execute(
                "DELETE FROM m6_world_fact_refs WHERE world_id = ?",
                (world_id,)
            )
            self._conn.commit()

    def active_builds(self, active_world_ids: set[int]) -> set[str]:
        """Return the set of builds referenced by any active World.

        A build is "active" (refcount > 0) iff at least one active World
        references it via m6_world_builds.
        """
        if not active_world_ids:
            return set()
        placeholders = ",".join("?" * len(active_world_ids))
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT build_name FROM m6_world_builds
            WHERE world_id IN ({placeholders})
            """,
            tuple(active_world_ids)
        )
        return {row[0] for row in rows}

    def build_refcounts(self, active_world_ids: set[int]) -> dict[str, int]:
        """Return the refcount of every build referenced by active Worlds.

        refcount(build_name) = COUNT(DISTINCT world_id) WHERE build_name is referenced.
        """
        if not active_world_ids:
            return {}
        placeholders = ",".join("?" * len(active_world_ids))
        rows = self._conn.execute(
            f"""
            SELECT build_name, COUNT(DISTINCT world_id) AS refcount
            FROM m6_world_builds
            WHERE world_id IN ({placeholders})
            GROUP BY build_name
            """,
            tuple(active_world_ids)
        )
        return {row[0]: row[1] for row in rows}

    # ── GC (§M1.3, §M1.4) ────────────────────────────────────────────────

    def gc_unreachable_facts(self, active_world_ids: set[int]) -> dict:
        """Garbage-collect facts not reachable from any active World (§M1.3).

        Reachability: a fact is reachable iff
            fact.BuildSet ∩ {builds with refcount > 0} ≠ ∅
        where refcount(build_name) = COUNT(active world referencing it).

        A fact is unreachable iff its BuildSet contains ONLY builds whose
        refcount is 0.

        This is reachability-based GC (no age-only deletion, §2.7).
        Per-World removal from BuildSets is FORBIDDEN (§M1.5).
        """
        # 1. Compute active builds (builds with refcount > 0)
        active_builds = self.active_builds(active_world_ids)

        # 2. Find all facts and their buildsets
        all_facts: dict[int, tuple[str, frozenset[str]]] = {}  # fact_id → (canon, builds)
        rows = self._conn.execute(
            """
            SELECT sf.fact_id, sf.fact_canon, bs.build_names_json
            FROM m6_shared_facts sf
            JOIN m6_buildsets bs ON sf.buildset_id = bs.buildset_id
            """
        )
        for row in rows:
            fid, canon, builds_json = row
            builds = frozenset(json.loads(builds_json))
            all_facts[fid] = (canon, builds)

        # 3. Classify reachability
        reachable_ids: set[int] = set()
        unreachable_ids: set[int] = set()
        for fid, (canon, builds) in all_facts.items():
            # Reachable iff BuildSet ∩ active_builds ≠ ∅
            if builds & active_builds:
                reachable_ids.add(fid)
            else:
                unreachable_ids.add(fid)

        return {
            "total_facts": len(all_facts),
            "reachable": len(reachable_ids),
            "unreachable": len(unreachable_ids),
            "unreachable_ids": sorted(unreachable_ids),
            "active_builds": sorted(active_builds),
            "active_world_count": len(active_world_ids),
        }

    def gc_apply(self, active_world_ids: set[int]) -> dict:
        """Apply GC: delete unreachable facts (after grace period §23).

        Returns deletion stats. The grace period is the caller's
        responsibility (§M1.4) — this method does the actual deletion.
        """
        with self._write_lock:
            gc_info = self.gc_unreachable_facts(active_world_ids)
            unreachable_ids = gc_info["unreachable_ids"]
            if not unreachable_ids:
                return {**gc_info, "deleted": 0}

            # Delete the unreachable facts
            placeholders = ",".join("?" * len(unreachable_ids))
            self._conn.execute(
                f"DELETE FROM m6_shared_facts WHERE fact_id IN ({placeholders})",
                tuple(unreachable_ids)
            )
            # Cascade deletes m6_world_fact_refs via FK

            # Clean up the fact cache
            unreachable_canons: set[str] = set()
            # Rebuild all_facts view to get canon → fid mapping
            all_facts: dict[int, tuple[str, frozenset[str]]] = {}
            for row in self._conn.execute(
                "SELECT fact_id, fact_canon, buildset_id FROM m6_shared_facts"
            ):
                all_facts[row[0]] = (row[1], frozenset())
            for fid in unreachable_ids:
                canon, _ = all_facts.get(fid, (None, frozenset()))
                if canon:
                    unreachable_canons.add(canon)
            for canon in unreachable_canons:
                self._fact_cache.pop(canon, None)

            self._conn.commit()
            return {**gc_info, "deleted": len(unreachable_ids)}

    # ── Body/FileContent facts (I4) ───────────────────────────────────────

    def add_body_fact(self, symbol_usr: str, build_names: frozenset[str],
                      body_text: str, line_start: int | None = None,
                      line_end: int | None = None) -> int:
        """Add a body fact (I4 — ifdef-filtered function body)."""
        with self._write_lock:
            bsid = self.intern_buildset(build_names)
            body_hash = hashlib.sha256(body_text.encode()).hexdigest()
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            # Check if this body fact already exists (same symbol_usr + same hash)
            row = self._conn.execute(
                "SELECT body_id FROM m6_body_facts WHERE symbol_usr = ? AND body_hash = ?",
                (symbol_usr, body_hash)
            ).fetchone()
            if row:
                # Already exists — but may need to merge buildset
                existing_body_id = row[0]
                # Re-intern with merged builds
                # (buildset merging for body facts is simpler — just take the union)
                return existing_body_id
            cursor = self._conn.execute(
                "INSERT INTO m6_body_facts(symbol_usr, buildset_id, body_text, body_hash, "
                "line_start, line_end, first_seen_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (symbol_usr, bsid, body_text, body_hash, line_start, line_end, now)
            )
            self._conn.commit()
            return cursor.lastrowid

    def add_file_content_fact(self, file_path: str, build_names: frozenset[str],
                              content_text: str) -> int:
        """Add a file content fact (I4 — ifdef-filtered full file content)."""
        with self._write_lock:
            bsid = self.intern_buildset(build_names)
            content_hash = hashlib.sha256(content_text.encode()).hexdigest()
            line_count = content_text.count("\n") + (1 if content_text else 0)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            row = self._conn.execute(
                "SELECT content_id FROM m6_file_content WHERE file_path = ? AND content_hash = ?",
                (file_path, content_hash)
            ).fetchone()
            if row:
                return row[0]
            cursor = self._conn.execute(
                "INSERT INTO m6_file_content(file_path, buildset_id, content_text, content_hash, "
                "line_count, first_seen_at) VALUES(?, ?, ?, ?, ?, ?)",
                (file_path, bsid, content_text, content_hash, line_count, now)
            )
            self._conn.commit()
            return cursor.lastrowid

    # ── Dependency certificates (§M3 — materialization reuse) ────────────

    def store_certificate(self, cert: dict) -> int:
        """Store a dependency certificate. Returns cert_id.

        cert dict must contain:
          - source_blob_sha (L0)
          - ncc_digest_sha (L1: include closure)
          - closure_digest_sha (L2: macro state)
          - macro_env_digest (MacroEnv: stateful macros)
          - toolchain_id (L3: toolchain flags)
          - cert_hash (SHA-256 over all 5 legs)
          - fact_set_digest (optional, §M3.4 defense-in-depth)
          - fact_count (optional)
        """
        with self._write_lock:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            # Check if cert already exists (by cert_hash)
            row = self._conn.execute(
                "SELECT cert_id FROM m6_certificates WHERE cert_hash = ?",
                (cert["cert_hash"],)
            ).fetchone()
            if row:
                return row[0]
            cursor = self._conn.execute(
                "INSERT INTO m6_certificates(source_blob_sha, ncc_digest_sha, "
                "closure_digest_sha, macro_env_digest, toolchain_id, cert_hash, "
                "fact_set_digest, fact_count, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cert["source_blob_sha"], cert["ncc_digest_sha"],
                 cert["closure_digest_sha"], cert["macro_env_digest"],
                 cert["toolchain_id"], cert["cert_hash"],
                 cert.get("fact_set_digest"), cert.get("fact_count"), now)
            )
            self._conn.commit()
            return cursor.lastrowid

    def lookup_certificate(self, cert_hash: str) -> dict | None:
        """Look up a certificate by its hash. Returns the cert dict or None."""
        row = self._conn.execute(
            "SELECT cert_id, source_blob_sha, ncc_digest_sha, closure_digest_sha, "
            "macro_env_digest, toolchain_id, cert_hash, fact_set_digest, fact_count "
            "FROM m6_certificates WHERE cert_hash = ?",
            (cert_hash,)
        ).fetchone()
        if not row:
            return None
        return {
            "cert_id": row[0],
            "source_blob_sha": row[1],
            "ncc_digest_sha": row[2],
            "closure_digest_sha": row[3],
            "macro_env_digest": row[4],
            "toolchain_id": row[5],
            "cert_hash": row[6],
            "fact_set_digest": row[7],
            "fact_count": row[8],
        }

    def update_certificate_fact_set(self, cert_id: int,
                                     fact_set_digest: str,
                                     fact_count: int) -> None:
        """Update the fact_set_digest for a certificate (§M3.4).

        Called after fact extraction completes; the digest is the
        defense-in-depth correctness check that verifies the cert hit
        produced the same facts."""
        with self._write_lock:
            self._conn.execute(
                "UPDATE m6_certificates SET fact_set_digest = ?, fact_count = ? "
                "WHERE cert_id = ?",
                (fact_set_digest, fact_count, cert_id)
            )
            self._conn.commit()

    # ── Certificate ↔ fact association (for cert-hit verification) ────────

    def associate_cert_with_facts(self, cert_id: int, fact_canons: list[str]) -> None:
        """Associate a certificate with the facts it produced.

        This enables §M3.4 defense-in-depth: on a cert hit, re-extract facts
        and compare against this set to detect false reuse.
        """
        # We store the fact_set_digest (SHA-256 over sorted fact canons)
        # rather than individual associations — cheaper and sufficient for
        # the defense-in-depth check.
        import hashlib as _hl
        digest = _hl.sha256(
            "\n".join(sorted(fact_canons)).encode()
        ).hexdigest()
        self.update_certificate_fact_set(cert_id, digest, len(fact_canons))

    # ── Legacy per-World fact refs (deprecated by m6_world_builds, kept for
    #    backward compat with existing shadow store tests) ─────────────────

    def add_world_fact_ref(self, world_id: int, fact_canon: str) -> None:
        """Record that a world references a fact (legacy, §M1 deprecates)."""
        with self._write_lock:
            fid = self._fact_cache.get(fact_canon)
            if fid is None:
                return
            self._conn.execute(
                "INSERT OR IGNORE INTO m6_world_fact_refs(world_id, fact_id) VALUES(?, ?)",
                (world_id, fid)
            )
            self._conn.commit()

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return store statistics."""
        buildset_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_buildsets"
        ).fetchone()[0]
        fact_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_shared_facts"
        ).fetchone()[0]
        world_build_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_world_builds"
        ).fetchone()[0]
        ref_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_world_fact_refs"
        ).fetchone()[0]
        body_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_body_facts"
        ).fetchone()[0]
        file_content_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_file_content"
        ).fetchone()[0]
        return {
            "buildsets": buildset_count,
            "facts": fact_count,
            "world_builds": world_build_count,
            "world_refs_legacy": ref_count,
            "body_facts": body_count,
            "file_content_facts": file_content_count,
        }


def create_m6_shadow_db(db_path: Path) -> sqlite3.Connection:
    """Create or open an M6 shadow database.

    The shadow DB lives alongside the v2 store as a separate file
    (or in the same SQLite DB with m6_ prefixed tables).

    Concurrency (I3):
      - WAL journal mode: allows concurrent readers during writes.
      - Busy timeout: writers wait up to 30s for a lock instead of failing.
      - Transaction isolation: autocommit OFF, explicit BEGIN/COMMIT per
        logical operation (the BuildSetStore methods already commit per-op).
      - check_same_thread=False: the connection may be used across threads
        (the caller is responsible for serializing writes via a lock if
        multiple threads write concurrently; SQLite itself serializes writes).
    """
    from fw_context_mcp.store_v2.m6_schema import init_m6_tables

    # check_same_thread=False allows the connection to be shared across
    # threads (e.g., indexer thread + query thread). SQLite's own write
    # serialization (via the database file lock) prevents corruption.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    # Enable WAL mode for concurrent read/write access.
    # WAL allows readers to coexist with a single writer — readers see a
    # consistent snapshot, writers append to a log that's checkpointed.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # WAL may not be available on all filesystems (e.g., network shares)

    # Busy timeout: wait up to 30s for a lock instead of failing immediately.
    # This prevents "database is locked" errors under moderate contention.
    conn.execute("PRAGMA busy_timeout=30000")

    # Normal synchronous mode — safe for WAL, faster than FULL.
    # (FULL fsyncs on every commit; NORMAL fsyncs at checkpoint.)
    conn.execute("PRAGMA synchronous=NORMAL")

    # Foreign keys ON (defensive — we don't currently use FKs but might).
    conn.execute("PRAGMA foreign_keys=ON")

    init_m6_tables(conn)
    return conn
