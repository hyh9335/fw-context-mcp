"""store_v2.gc — M5-4 reachability-based GC for v2 semantic storage.

Implements the staged GC interface from §11 of the M5 contract:

- ``analyze_gc``  — ANALYZE / DRY RUN: compute candidate Worlds/states for
  collection, reclaimed-bytes estimate, protection roots. No deletion.
- ``apply_gc``    — APPLY: transactional deletion per approved reachability set.
- ``verify_gc``    — VERIFY: integrity checks after mutation.

Root model (§8):
  - current completed World(s) (status='active')
  - explicitly retained Worlds (status='retained')
  - Worlds protected by active PinnedQueryViews (pinned_world_ids)
  - any explicit historical retention policy (status='retained')

Shared SemanticState GC (§10):
  World deletion does NOT imply immediate state deletion. A SemanticState
  becomes eligible only after ALL referencing Worlds are unreachable. This
  module checks ``tu_bindings`` references after World deletion to determine
  state eligibility.

Crash safety (§12):
  APPLY is a single SQLite transaction. A crash during APPLY rolls back.
  GC is idempotent: re-running ANALYZE after a crash produces the same
  candidate set (minus whatever was already committed in a prior APPLY).
  A crash must NOT make a still-rooted World unusable — root Worlds are
  never in the candidate set.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GCAnalysis:
    """Result of ANALYZE (dry-run). No mutation has occurred."""

    eligible_world_ids: list[int] = field(default_factory=list)
    eligible_state_ids: list[int] = field(default_factory=list)
    protected_world_ids: list[int] = field(default_factory=list)
    protected_reasons: dict[int, str] = field(default_factory=dict)
    reclaimed_worlds_estimate: int = 0
    reclaimed_states_estimate: int = 0
    total_worlds: int = 0
    total_states: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_world_ids": list(self.eligible_world_ids),
            "eligible_state_ids": list(self.eligible_state_ids),
            "protected_world_ids": list(self.protected_world_ids),
            "protected_reasons": dict(self.protected_reasons),
            "reclaimed_worlds_estimate": self.reclaimed_worlds_estimate,
            "reclaimed_states_estimate": self.reclaimed_states_estimate,
            "total_worlds": self.total_worlds,
            "total_states": self.total_states,
            "error": self.error,
        }


@dataclass(frozen=True)
class GCApplyResult:
    """Result of APPLY (transactional deletion)."""

    deleted_worlds: int = 0
    deleted_states: int = 0
    deleted_bindings: int = 0
    deleted_rollups: int = 0
    transaction_committed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "deleted_worlds": self.deleted_worlds,
            "deleted_states": self.deleted_states,
            "deleted_bindings": self.deleted_bindings,
            "deleted_rollups": self.deleted_rollups,
            "transaction_committed": self.transaction_committed,
            "error": self.error,
        }


@dataclass(frozen=True)
class GCVerifyResult:
    """Result of VERIFY (integrity checks)."""

    orphaned_bindings: int = 0
    orphaned_states: int = 0
    dangling_fks: int = 0
    integrity_ok: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "orphaned_bindings": self.orphaned_bindings,
            "orphaned_states": self.orphaned_states,
            "dangling_fks": self.dangling_fks,
            "integrity_ok": self.integrity_ok,
            "error": self.error,
        }


# ── Root model ──────────────────────────────────────────────────────────────

# World statuses that are GC ROOTS (protected from collection).
# §8: current completed Worlds + explicitly retained Worlds.
GC_ROOT_STATUSES = frozenset({"active", "retained"})

# World statuses that are GC-ELIGIBLE (candidates for collection).
# §13: failed/abandoned partial Worlds are immediately cleanup-eligible.
# 'superseded' Worlds are old completed Worlds replaced by newer ones.
# 'retired' Worlds are marked for retirement (GC-eligible).
# 'unfinalized' Worlds that are stale (no recent finalization attempt) are
# also eligible — but we must be careful not to collect a World that is
# currently being built. The caller must pass active_build_world_ids to
# protect the in-progress World.
GC_ELIGIBLE_STATUSES = frozenset({"failed", "superseded", "retired"})


def analyze_gc(
    store,
    pinned_world_ids: list[int] | None = None,
    active_build_world_ids: list[int] | None = None,
) -> GCAnalysis:
    """ANALYZE / DRY RUN: compute GC candidates without deletion.

    Args:
        store: v2 store connection (from open_v2_store_for_write).
        pinned_world_ids: World IDs protected by active PinnedQueryViews
            (§9). These are roots and will NOT be collected.
        active_build_world_ids: World IDs currently being built (status=
            'unfinalized'). These are protected — a World under
            construction must not be collected.

    Returns:
        GCAnalysis with eligible World IDs, eligible State IDs, and
        protection information. No mutation occurs.
    """
    pinned = set(pinned_world_ids or [])
    building = set(active_build_world_ids or [])
    conn = store._conn

    try:
        # ── Step 1: classify all Worlds ──
        rows = conn.execute(
            "SELECT world_id, world_status FROM snapshot_builds"
        ).fetchall()
        total_worlds = len(rows)

        eligible_worlds: list[int] = []
        protected: dict[int, str] = {}
        protected_ids: set[int] = set()

        for row in rows:
            wid = int(row["world_id"])
            status = row["world_status"]

            if wid in pinned:
                protected[wid] = f"pinned (status={status})"
                protected_ids.add(wid)
            elif wid in building:
                protected[wid] = f"building (status={status})"
                protected_ids.add(wid)
            elif status in GC_ROOT_STATUSES:
                protected[wid] = f"root: status={status}"
                protected_ids.add(wid)
            elif status in GC_ELIGIBLE_STATUSES:
                eligible_worlds.append(wid)
            elif status == "unfinalized":
                # Stale unfinalized Worlds are eligible (§13: don't let
                # abandoned partial data grow without bound). The caller
                # must protect the current building World via
                # active_build_world_ids.
                eligible_worlds.append(wid)
            else:
                # Unknown status — protect by default (fail-safe).
                protected[wid] = f"unknown status={status}"
                protected_ids.add(wid)

        # ── Step 2: compute eligible shared SemanticStates ──
        # A SemanticState is eligible ONLY if NO protected World references
        # it via tu_bindings (§10: shared state survives until ALL
        # referencing Worlds are unreachable).
        if eligible_worlds:
            placeholders_eligible = ",".join("?" * len(eligible_worlds))
            if protected_ids:
                placeholders_protected = ",".join("?" * len(protected_ids))
                # States referenced by eligible Worlds but NOT by any
                # protected World — these become unreferenced after
                # eligible World deletion and are safe to sweep.
                eligible_state_rows = conn.execute(
                    f"SELECT DISTINCT b.state_id FROM tu_bindings b "
                    f"WHERE b.world_id IN ({placeholders_eligible}) "
                    f"AND b.state_id IS NOT NULL "
                    f"AND NOT EXISTS ("
                    f"  SELECT 1 FROM tu_bindings b2 "
                    f"  WHERE b2.state_id = b.state_id "
                    f"  AND b2.world_id IN ({placeholders_protected})"
                    f")",
                    list(eligible_worlds) + list(protected_ids),
                ).fetchall()
            else:
                # No protected Worlds — all states referenced by eligible
                # Worlds are eligible.
                eligible_state_rows = conn.execute(
                    f"SELECT DISTINCT b.state_id FROM tu_bindings b "
                    f"WHERE b.world_id IN ({placeholders_eligible}) "
                    f"AND b.state_id IS NOT NULL",
                    list(eligible_worlds),
                ).fetchall()
            eligible_states = [r["state_id"] for r in eligible_state_rows]
        else:
            eligible_states = []

        total_states = conn.execute(
            "SELECT COUNT(*) AS c FROM semantic_states"
        ).fetchone()["c"]

        return GCAnalysis(
            eligible_world_ids=sorted(eligible_worlds),
            eligible_state_ids=sorted(eligible_states),
            protected_world_ids=sorted(protected_ids),
            protected_reasons=protected,
            reclaimed_worlds_estimate=len(eligible_worlds),
            reclaimed_states_estimate=len(eligible_states),
            total_worlds=total_worlds,
            total_states=total_states,
        )
    except Exception as exc:
        log.error("gc analyze failed: %s", exc)
        return GCAnalysis(error=str(exc))


def apply_gc(store, analysis: GCAnalysis) -> GCApplyResult:
    """APPLY: transactional deletion per approved reachability set.

    Deletes eligible Worlds (CASCADE removes their tu_bindings,
    build_rollups, etc.) then sweeps shared SemanticStates that are no
    longer referenced by any remaining World.

    This is a single SQLite transaction — a crash rolls back (§12).
    Idempotent: re-running with the same analysis is safe (already-deleted
    rows are simply not found).

    Args:
        store: v2 store connection (from open_v2_store_for_write).
        analysis: GCAnalysis from analyze_gc (the approved candidate set).

    Returns:
        GCApplyResult with deletion counts.
    """
    if analysis.error:
        return GCApplyResult(error=f"analysis has error: {analysis.error}")
    if not analysis.eligible_world_ids and not analysis.eligible_state_ids:
        return GCApplyResult(transaction_committed=True)

    conn = store._conn
    deleted_worlds = 0
    deleted_bindings = 0
    deleted_rollups = 0
    deleted_states = 0

    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # ── Step 1: delete eligible Worlds (CASCADE removes bindings) ──
            for wid in analysis.eligible_world_ids:
                # Count bindings before deletion for reporting
                bcount = conn.execute(
                    "SELECT COUNT(*) AS c FROM tu_bindings WHERE world_id=?",
                    (wid,)).fetchone()["c"]
                rcount = conn.execute(
                    "SELECT COUNT(*) AS c FROM build_rollups WHERE world_id=?",
                    (wid,)).fetchone()["c"]
                conn.execute(
                    "DELETE FROM snapshot_builds WHERE world_id=?", (wid,))
                deleted_bindings += bcount
                deleted_rollups += rcount
                deleted_worlds += 1

            # ── Step 2: sweep shared SemanticStates no longer referenced ──
            # A state is eligible only if NO tu_bindings reference it.
            # (§10: shared state survives until ALL referencing Worlds
            # are unreachable.)
            for sid in analysis.eligible_state_ids:
                # Double-check: is this state still referenced?
                refcount = conn.execute(
                    "SELECT COUNT(*) AS c FROM tu_bindings WHERE state_id=?",
                    (sid,)).fetchone()["c"]
                if refcount == 0:
                    conn.execute(
                        "DELETE FROM semantic_states WHERE state_id=?",
                        (sid,))
                    deleted_states += 1

            conn.commit()
            return GCApplyResult(
                deleted_worlds=deleted_worlds,
                deleted_states=deleted_states,
                deleted_bindings=deleted_bindings,
                deleted_rollups=deleted_rollups,
                transaction_committed=True,
            )
        except BaseException:
            conn.rollback()
            raise
    except Exception as exc:
        log.error("gc apply failed: %s", exc)
        return GCApplyResult(error=str(exc))


def verify_gc(store) -> GCVerifyResult:
    """VERIFY: integrity checks after GC mutation.

    Checks for:
    - Orphaned tu_bindings (world_id not in snapshot_builds)
    - Orphaned semantic_states (not referenced by any tu_bindings — advisory)
    - Dangling foreign keys (SQLite FK check)

    Returns:
        GCVerifyResult with counts and integrity_ok flag.
    """
    conn = store._conn
    try:
        orphaned_bindings = conn.execute(
            "SELECT COUNT(*) AS c FROM tu_bindings b "
            "WHERE NOT EXISTS (SELECT 1 FROM snapshot_builds s "
            "WHERE s.world_id = b.world_id)"
        ).fetchone()["c"]

        # Orphaned states: states with no bindings referencing them.
        # These are advisory — they may be retained for caching. Not
        # necessarily an error, but reported for diagnostics.
        orphaned_states = conn.execute(
            "SELECT COUNT(*) AS c FROM semantic_states s "
            "WHERE NOT EXISTS (SELECT 1 FROM tu_bindings b "
            "WHERE b.state_id = s.state_id)"
        ).fetchone()["c"]

        # SQLite foreign-key check
        fk_result = conn.execute("PRAGMA foreign_key_check").fetchall()
        dangling_fks = len(fk_result)

        integrity_ok = (orphaned_bindings == 0 and dangling_fks == 0)

        return GCVerifyResult(
            orphaned_bindings=orphaned_bindings,
            orphaned_states=orphaned_states,
            dangling_fks=dangling_fks,
            integrity_ok=integrity_ok,
        )
    except Exception as exc:
        log.error("gc verify failed: %s", exc)
        return GCVerifyResult(error=str(exc))


# ── Lifecycle helpers ───────────────────────────────────────────────────────

def mark_world_failed(store, world_id: int) -> bool:
    """Mark a World as 'failed' (§13: incomplete/abandoned indexing attempt).

    A failed World is immediately GC-eligible. It is NOT queryable
    (QUERYABLE_WORLD_STATUS = {'active'} only).

    Returns True if the status was updated.
    """
    conn = store._conn
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE snapshot_builds SET world_status='failed' "
            "WHERE world_id=? AND world_status IN ('unfinalized','active')",
            (world_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.error("mark_world_failed failed: %s", exc)
        conn.rollback()
        return False


def mark_world_retained(store, world_id: int, reason: str = "") -> bool:
    """Mark a World as 'retained' (§8: explicitly retained for historical policy).

    A retained World is a GC root — it will NOT be collected. It is not
    directly queryable (only 'active' Worlds serve queries), but it is
    protected from deletion.

    Returns True if the status was updated.
    """
    conn = store._conn
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE snapshot_builds SET world_status='retained' "
            "WHERE world_id=? AND world_status != 'active'",
            (world_id,))
        # Store retention reason in run_provenance if provided
        if reason and cur.rowcount > 0:
            row = conn.execute(
                "SELECT run_provenance FROM snapshot_builds WHERE world_id=?",
                (world_id,)).fetchone()
            if row:
                try:
                    prov = json.loads(row["run_provenance"] or "{}")
                except Exception:
                    prov = {}
                prov["retention_reason"] = reason
                conn.execute(
                    "UPDATE snapshot_builds SET run_provenance=? "
                    "WHERE world_id=?",
                    (json.dumps(prov, sort_keys=True), world_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.error("mark_world_retained failed: %s", exc)
        conn.rollback()
        return False


def mark_world_retired(store, world_id: int) -> bool:
    """Mark a World as 'retired' (GC-eligible for future collection).

    A retired World is no longer active and no longer retained. It becomes
    a GC candidate. The World is NOT queryable.

    Returns True if the status was updated.
    """
    conn = store._conn
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE snapshot_builds SET world_status='retired' "
            "WHERE world_id=? AND world_status IN ('active','superseded','retained')",
            (world_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception as exc:
        log.error("mark_world_retired failed: %s", exc)
        conn.rollback()
        return False
