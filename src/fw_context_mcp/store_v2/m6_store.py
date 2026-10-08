"""M6 BuildSet store — Phase I I2 implementation.

SQLite-backed BuildSet interning and fact storage.
Integrates with existing v2 schema via m6_* shadow tables.

This module is SHADOW-ONLY during Phase I. It does not affect
production query paths until shadow differential testing (§34)
proves QUERY_PARITY_FAILURES == 0.
"""
from __future__ import annotations
import json, hashlib, sqlite3, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterable, Optional


def _hash_buildset(build_names: frozenset[str]) -> str:
    """SHA-256 over sorted build names."""
    sorted_names = sorted(build_names)
    return hashlib.sha256("|".join(sorted_names).encode()).hexdigest()


class BuildSetStore:
    """SQLite-backed BuildSet interning and fact storage.
    
    Tables (from m6_schema.py):
      m6_buildsets, m6_shared_facts, m6_fact_bundles, m6_world_fact_refs
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._buildset_cache: dict[str, int] = {}  # hash → id
        self._fact_cache: dict[str, int] = {}      # canon → id
        self._load_caches()

    def _load_caches(self) -> None:
        """Load existing buildset and fact IDs into memory caches."""
        for row in self._conn.execute("SELECT builds_hash, buildset_id FROM m6_buildsets"):
            self._buildset_cache[row[0]] = row[1]
        for row in self._conn.execute("SELECT fact_canon, fact_id FROM m6_shared_facts"):
            self._fact_cache[row[0]] = row[1]

    def intern_buildset(self, build_names: frozenset[str]) -> int:
        """Intern a BuildSet. Returns the buildset_id."""
        bhash = _hash_buildset(build_names)
        cached = self._buildset_cache.get(bhash)
        if cached is not None:
            return cached
        
        # Insert new buildset
        build_names_json = json.dumps(sorted(build_names))
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = self._conn.execute(
            "INSERT INTO m6_buildsets(builds_hash, build_count, build_names_json, created_at) "
            "VALUES(?, ?, ?, ?)",
            (bhash, len(build_names), build_names_json, now)
        )
        bsid = cursor.lastrowid
        self._buildset_cache[bhash] = bsid
        self._conn.commit()
        return bsid

    def merge_buildset(self, fact_canon: str, new_builds: frozenset[str]) -> int:
        """Merge a fact's BuildSet with new builds.
        
        If the fact exists, union its current BuildSet with new_builds.
        If not, create with new_builds.
        Returns the final buildset_id.
        """
        existing_fid = self._fact_cache.get(fact_canon)
        if existing_fid is None:
            return self.intern_buildset(new_builds)
        
        # Look up the fact's current buildset_id
        row = self._conn.execute(
            "SELECT buildset_id FROM m6_shared_facts WHERE fact_id = ?",
            (existing_fid,)
        ).fetchone()
        if row is None:
            return self.intern_buildset(new_builds)
        
        existing_bsid = row[0]
        # Get current buildset's build names
        bs_row = self._conn.execute(
            "SELECT build_names_json FROM m6_buildsets WHERE buildset_id = ?",
            (existing_bsid,)
        ).fetchone()
        if bs_row is None:
            return self.intern_buildset(new_builds)
        
        current_builds = frozenset(json.loads(bs_row[0]))
        merged = current_builds | new_builds
        return self.intern_buildset(merged)

    def add_fact(self, fact_canon: str, fact_kind: str,
                 build_names: frozenset[str],
                 payload: dict | None = None) -> int:
        """Add or update a shared fact.
        
        If the fact exists, merge its BuildSet with build_names.
        If not, create a new fact.
        Returns the fact_id.
        """
        # Merge buildset
        bsid = self.merge_buildset(fact_canon, build_names)
        
        # Check if fact already exists
        existing_fid = self._fact_cache.get(fact_canon)
        if existing_fid is not None:
            # Update buildset_id (may have changed due to merge)
            self._conn.execute(
                "UPDATE m6_shared_facts SET buildset_id = ? WHERE fact_id = ?",
                (bsid, existing_fid)
            )
            self._conn.commit()
            return existing_fid
        
        # Insert new fact
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

    def project_build(self, build_name: str) -> dict[str, list[dict]]:
        """Project all facts visible in a specific Build.
        
        Returns {fact_kind: [{fact_canon, fact_id, payload}, ...]}.
        """
        result: dict[str, list[dict]] = {}
        
        # Query facts where build_name is in the buildset
        rows = self._conn.execute(
            """
            SELECT sf.fact_id, sf.fact_canon, sf.fact_kind, sf.payload_json,
                   bs.build_names_json
            FROM m6_shared_facts sf
            JOIN m6_buildsets bs ON sf.buildset_id = bs.buildset_id
            WHERE bs.build_names_json LIKE ?
            """,
            (f'%"{build_name}"%',)
        )
        
        for row in rows:
            fid, canon, kind, payload_json, builds_json = row
            builds = json.loads(builds_json)
            if build_name in builds:
                payload = json.loads(payload_json) if payload_json else None
                result.setdefault(kind, []).append({
                    "fact_id": fid,
                    "fact_canon": canon,
                    "payload": payload,
                })
        
        return result

    def add_world_fact_ref(self, world_id: int, fact_canon: str) -> None:
        """Record that a world references a fact (for GC reachability)."""
        fid = self._fact_cache.get(fact_canon)
        if fid is None:
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO m6_world_fact_refs(world_id, fact_id) VALUES(?, ?)",
            (world_id, fid)
        )
        self._conn.commit()

    def gc_unreachable_facts(self, active_world_ids: set[int]) -> dict:
        """Garbage-collect facts not reachable from any active world.
        
        Reachability: a fact is reachable if any world in active_world_ids
        references it via m6_world_fact_refs.
        
        This is reachability-based GC (no age-only deletion).
        """
        # Find all facts referenced by active worlds
        active_facts = set()
        for wid in active_world_ids:
            rows = self._conn.execute(
                "SELECT fact_id FROM m6_world_fact_refs WHERE world_id = ?",
                (wid,)
            )
            for row in rows:
                active_facts.add(row[0])
        
        # Find all facts NOT referenced by any active world
        all_facts = set(self._fact_cache.values())
        unreachable = all_facts - active_facts
        
        # Also check: facts whose buildset contains only inactive worlds
        # These are truly unreachable
        truly_unreachable = set()
        for fid in unreachable:
            row = self._conn.execute(
                "SELECT bs.build_names_json FROM m6_shared_facts sf "
                "JOIN m6_buildsets bs ON sf.buildset_id = bs.buildset_id "
                "WHERE sf.fact_id = ?",
                (fid,)
            ).fetchone()
            if row:
                builds = set(json.loads(row[0]))
                # Check if any active world's build is in this buildset
                # (For now, we use world_id directly; in production, we'd
                # resolve world_id → build_name)
                # Simplified: if no active world references this fact, it's unreachable
                truly_unreachable.add(fid)
        
        return {
            "total_facts": len(all_facts),
            "reachable": len(all_facts) - len(truly_unreachable),
            "unreachable": len(truly_unreachable),
            "unreachable_ids": list(truly_unreachable),
        }

    def stats(self) -> dict:
        """Return store statistics."""
        buildset_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_buildsets"
        ).fetchone()[0]
        fact_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_shared_facts"
        ).fetchone()[0]
        ref_count = self._conn.execute(
            "SELECT COUNT(*) FROM m6_world_fact_refs"
        ).fetchone()[0]
        return {
            "buildsets": buildset_count,
            "facts": fact_count,
            "world_refs": ref_count,
        }


def create_m6_shadow_db(db_path: Path) -> sqlite3.Connection:
    """Create or open an M6 shadow database.
    
    The shadow DB lives alongside the v2 store as a separate file
    (or in the same SQLite DB with m6_ prefixed tables).
    """
    from fw_context_mcp.store_v2.m6_schema import init_m6_tables
    
    conn = sqlite3.connect(str(db_path))
    init_m6_tables(conn)
    return conn
