"""store_v2.worlds — Phase-4 Slice 2 production shadow binding path.

Implements the Slice-2 chain over the ALREADY-FROZEN Slice-0 schema
(``schema.py`` carries the full D1 §2 skeleton: ``repository_namespaces``,
``snapshots``, ``builds``, ``snapshot_builds`` (the World row),
``world_poisons``, ``snapshot_files``, ``file_blobs``, ``tu_bindings`` with
the G2 generation anchor, ``tu_context_details``, ``canon_paths``,
``cert_rows``, ``binding_file_map``, ``tu_private_payloads``)::

    RepositoryNamespace → SourceSnapshot → Build → World → TUBinding
                        → already-published SemanticState

Normative sources (consumed, not redesigned):

* World identity triple ``UNIQUE(namespace_id, snapshot_id,
  build_identity_sha)`` + explicit ``'unfinalized'`` state (DECISION G2
  amendment / Row 3): a mid-run world is NOT query-visible until
  :func:`finalize_world` flips it to ``'active'`` after verifying that every
  expected TU context carries either a normal binding or an explicit poison
  disposition.
* Only ``publication='published'`` states may receive a normal binding
  (task §12; D-2 visibility gate).  Staging/quarantined/missing targets fail
  CLOSED with an integrity event — no valid binding row is created.
* A binding NEVER mutates SemanticState (INV-1/INV-2/INV-5).  Reachability is
  derived from bindings; GC is Slice 5.
* A-2 gate: a ``path_spelling_sensitive`` state binds only to bindings whose
  canon source path is spelling-equal to the state's first binding (fail
  closed).
* Poisoned TU: explicit ``binding_status='poisoned'`` row + ``world_poisons``
  + ``tu_private_payloads``; NO fake semantic-state key (INV-6).  The world
  can still finalize — published-but-degraded (D-4 world-level disposition).
* Binding idempotence: re-presenting the same (world, tu_context, state)
  converges to ONE row; a conflicting state for an existing binding identity
  fails closed + integrity event — no last-writer-wins (task §22).
* Crash cases (task §27): C1 orphan published states are allowed (binding
  absent ⇒ world unfinalized); C2 partial worlds stay ``'unfinalized'``;
  C3 retry completes idempotently; C4 unpublished-state references fail
  closed.

Transaction discipline (task §26): every store function here opens its OWN
short ``BEGIN IMMEDIATE`` transaction and never parses inside it.  World-row
materialization (:func:`_ensure_world_rows`) is a NON-transactional helper —
it must be called inside an open transaction.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .identity_capture import (
    CAPTURE_VERSION,
    SnapshotCapture,
    capture_repository_identity,
    capture_snapshot,
    toolchain_identity_sha,
)
from .keys import UNKNOWN_TAG, h
from .world_id import build_identity

#: Additive Slice-2 DDL (no frozen table is touched).  ``slice2_run_metrics``
#: holds the §40 per-shadow-run counters (before/after evidence for Slice 4).
#: (``tu_bindings`` world/state indexes already exist in the frozen schema's
#: INDEX_STATEMENTS — not duplicated here.)
SLICE2_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS slice2_run_metrics (
      run_id   TEXT NOT NULL,
      metric   TEXT NOT NULL,
      value    INTEGER NOT NULL,
      detail   TEXT,
      updated_at TEXT,
      PRIMARY KEY(run_id, metric)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sb_ns ON snapshot_builds(namespace_id)",
)

#: §40 metric names (established at Slice 2; work-saved claims begin Slice 4).
METRIC_NAMES = (
    "tu_world_count",
    "unique_semantic_states",
    "new_semantic_states",
    "existing_semantic_states_matched",
    "bindings_created",
    "poisoned_tus",
    "parse_count",
    "certification_count",
)


def ensure_slice2_schema(conn) -> None:
    """Create the additive Slice-2 structures (idempotent)."""
    for stmt in SLICE2_DDL:
        conn.execute(stmt)


# ── the run context (§34/§35: orchestration ONLY, never an identity layer) ──

@dataclass
class V2WorldContext:
    """One shadow index/build run's world orchestration context.

    Constructed ONCE per ``runner.run`` when the v2 flag is on (task §34) and
    threaded into the per-TU hook.  Carries approved identity fields plus
    expected-membership bookkeeping and §40 counters.  It is orchestration
    context ONLY — it never becomes a semantic identity layer, and no field
    of it ever enters a TUSemanticKey.
    """

    run_id: str
    project_root: str
    namespace_id: str                 # routing token (r:<repokey> | t:<token>)
    repository_key_sha: str
    namespace_derivation: str         # JSON (INV-9)
    namespace_tag: str                # 'git' | 'tree'
    snapshot: SnapshotCapture
    builder_family: str
    declared_env_salt: str
    declared_env_provenance: str      # JSON
    store_token: str                  # legacy project_id (provenance)
    expected_tu_contexts: set = field(default_factory=set)
    processed_tu_contexts: set = field(default_factory=set)
    metrics: dict = field(default_factory=lambda: {m: 0 for m in METRIC_NAMES})
    touched_states: set = field(default_factory=set)   # (key, generation)
    world_id: int | None = None
    build_identity_sha: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- counter plumbing (thread-safe) --------------------------------------
    def inc(self, metric: str, amount: int = 1) -> None:
        with self._lock:
            self.metrics[metric] = self.metrics.get(metric, 0) + amount

    def set(self, metric: str, value: int) -> None:
        with self._lock:
            self.metrics[metric] = value

    def note_processed(self, tu_context_id: str) -> None:
        with self._lock:
            self.processed_tu_contexts.add(tu_context_id)
            self.metrics["tu_world_count"] = len(self.processed_tu_contexts)

    def note_state(self, key: str, generation: int, is_new: bool) -> None:
        with self._lock:
            self.touched_states.add((key, generation))
            self.metrics["unique_semantic_states"] = len(self.touched_states)
            if is_new:
                self.metrics["new_semantic_states"] += 1
            else:
                self.metrics["existing_semantic_states_matched"] += 1

    def snapshot_metrics(self) -> dict:
        with self._lock:
            return dict(self.metrics)


def build_world_context(*, project_root, build_dir_patterns=None,
                        build_system: str | None = None,
                        project_id: str | None = None,
                        declared_env_salt: str = "",
                        ) -> V2WorldContext | None:
    """Construct the run context ONCE per index run (task §34/§35).

    Returns None on any capture failure — the caller then skips the Slice-2
    path for this run while legacy indexing continues (exception isolation,
    task §32).  Namespace routing: git workspaces route by RepositoryKey
    (``r:<sha>``); no-git workspaces by the store token (``t:<token>``
    [S1]§1.4 — RepositoryKey is UNKNOWN there and NEVER gates anything).
    """
    try:
        root = Path(project_root).resolve()
        repo = capture_repository_identity(root)
        capture = capture_snapshot(root, build_dir_patterns)
        salt = (declared_env_salt
                or os.environ.get("FW_CONTEXT_ENV_SALT", ""))
        salt_prov = ({"declared_by": "env:FW_CONTEXT_ENV_SALT"}
                     if salt else
                     {"declared_by": "none", "note": "undeclared env salt"})
        if repo.namespace_tag == "git":
            namespace_id = f"r:{repo.repository_key_sha}"
        else:
            namespace_id = f"t:{project_id or 'unknown-store'}"
        return V2WorldContext(
            run_id=f"run:{uuid.uuid4().hex}",
            project_root=str(root),
            namespace_id=namespace_id,
            repository_key_sha=repo.repository_key_sha,
            namespace_derivation=repo.derivation_detail,
            namespace_tag=repo.namespace_tag,
            snapshot=capture,
            builder_family=(build_system or "unknown"),
            declared_env_salt=salt,
            declared_env_provenance=json.dumps(salt_prov, sort_keys=True),
            store_token=project_id or "",
        )
    except Exception:                                    # pragma: no cover
        return None


# ── store write path ─────────────────────────────────────────────────────────

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_project_rel(display: str) -> bool:
    """``ClosureMember.display_path`` is project-relative posix, else absolute."""
    if display.startswith("/"):
        return False
    return not (len(display) > 2 and display[1] == ":")


def _ensure_world_rows(conn, ctx: V2WorldContext, cert) -> int:
    """Materialize namespace → snapshot → build → world rows (idempotent).

    NON-transactional: the caller MUST hold an open ``BEGIN IMMEDIATE``
    transaction.  ``INSERT OR IGNORE`` over the identity tuples makes
    repeated calls pure no-ops, so no per-TU world metadata can diverge
    (task §34).  BuildIdentity legs reuse the Slice-1 certified parser
    identity — no second toolchain identity system (task §7).  Poison-only
    runs hash UNKNOWN legs (R-K1(d)) instead of inventing identity.
    """
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO repository_namespaces(namespace_id, "
        "repository_key_sha, derivation_detail, legacy_store_token, "
        "created_at, created_by) VALUES(?,?,?,?,?,?)",
        (ctx.namespace_id, ctx.repository_key_sha, ctx.namespace_derivation,
         ctx.store_token, now, "fw-context slice2"))
    snap = ctx.snapshot
    conn.execute(
        "INSERT OR IGNORE INTO snapshots(snapshot_id, namespace_id, "
        "namespace_tag, base_commit, dirty_delta_digest, provenance_branch, "
        "provenance_tags, verification, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (snap.snapshot_id, ctx.namespace_id, snap.namespace_tag,
         snap.base_commit, snap.dirty_delta_digest, snap.branch or None,
         json.dumps({"capture_version": CAPTURE_VERSION,
                     "dirty_count": len(snap.dirty_entries)},
                    sort_keys=True),
         snap.verification, now, "fw-context slice2"))
    ident = cert.parser_identity
    inputs = cert.inputs
    tool_sha = toolchain_identity_sha(
        kind="clang",
        version_string=ident.capture_binary_id or UNKNOWN_TAG,
        reported_target=ident.target_triple or UNKNOWN_TAG)
    candidate = build_identity(
        toolchain_identity=tool_sha,
        parser_salt=(inputs.parser_salt if inputs else "") or UNKNOWN_TAG,
        parser_generation=(inputs.parser_generation if inputs
                           else ident.indexer_generation),
        environment_digest=(inputs.environment_digest
                            if inputs else "") or UNKNOWN_TAG,
        declared_env_salt=ctx.declared_env_salt,
        declared_env_provenance=ctx.declared_env_provenance,
        builder_family=ctx.builder_family)
    if ctx.build_identity_sha is not None and candidate != ctx.build_identity_sha:
        # The run's build identity is ANCHORED by its first certification
        # (BuildIdentity is a run property; a poisoned TU reuses the run's
        # certified legs rather than inventing a second world).  A divergence
        # is an integrity event, never a new world.
        _record_event(conn, "world_build_identity_divergence",
                      detail=f"run={ctx.run_id} anchored={ctx.build_identity_sha}"
                             f" candidate={candidate}")
    bsha = ctx.build_identity_sha or candidate
    first_establishment = ctx.build_identity_sha is None
    ctx.build_identity_sha = bsha
    if first_establishment:
        conn.execute(
            "INSERT OR IGNORE INTO builds(namespace_id, build_identity_sha, "
            "toolchain_identity, parser_salt, parser_generation, "
            "environment_digest_sha, declared_env_salt, "
            "declared_env_provenance, builder_family, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ctx.namespace_id, bsha, tool_sha,
             (inputs.parser_salt if inputs else "") or UNKNOWN_TAG,
             (inputs.parser_generation if inputs else ident.indexer_generation),
             (inputs.environment_digest if inputs else "") or UNKNOWN_TAG,
             ctx.declared_env_salt, ctx.declared_env_provenance,
             ctx.builder_family, now))
    conn.execute(
        "INSERT OR IGNORE INTO snapshot_builds(namespace_id, snapshot_id, "
        "build_identity_sha, world_status, run_provenance, created_at) "
        "VALUES(?,?,?,'unfinalized',?,?)",
        (ctx.namespace_id, snap.snapshot_id, bsha,
         json.dumps({"run_id": ctx.run_id,
                     "project_root": str(ctx.project_root),
                     "capture": snap.capture_detail,
                     "verification": snap.verification,
                     "capture_version": CAPTURE_VERSION},
                    sort_keys=True),
         now))
    row = conn.execute(
        "SELECT world_id FROM snapshot_builds WHERE namespace_id=? AND "
        "snapshot_id=? AND build_identity_sha=?",
        (ctx.namespace_id, snap.snapshot_id, bsha)).fetchone()
    ctx.world_id = int(row["world_id"])
    ctx.build_identity_sha = bsha
    ensure_slice2_schema(conn)
    return ctx.world_id


def _canon_path_id(conn, namespace_id: str, path: str) -> int:
    row = conn.execute(
        "SELECT path_id FROM canon_paths WHERE namespace_id=? AND path=?",
        (namespace_id, path)).fetchone()
    if row is not None:
        return int(row["path_id"])
    cur = conn.execute(
        "INSERT INTO canon_paths(namespace_id, path) VALUES(?,?)",
        (namespace_id, path))
    return int(cur.lastrowid)


def _record_event(conn, kind: str, *, key: str = "", generation=None,
                  detail: str = "") -> None:
    conn.execute(
        "INSERT INTO integrity_events(ts, kind, tusemantic_key, "
        "parser_generation, detail) VALUES(?,?,?,?,?)",
        (_now(), kind, key, 0 if generation is None else generation, detail))


@dataclass(frozen=True)
class BindingResult:
    """Outcome of one bind attempt (honest dispositions, task §22)."""

    status: str          # bound_new | bound_existing | refused_unpublished |
                         # refused_path_spelling | refused_conflict |
                         # poisoned_recorded | error
    binding_id: int | None
    detail: str = ""


def _cert_rows_digest(cert) -> str:
    legs = [f"{m.display_path}\x1f{m.blob_sha or ''}\x1f{m.role}"
            for m in cert.closure]
    return h("fwctx-certdigest/v1", *legs)


def _insert_cert_rows(conn, ctx: V2WorldContext, binding_id: int,
                      cert) -> None:
    """§24: persist the per-binding resolution certificate (diffable)."""
    for ordinal, member in enumerate(cert.closure):
        rnf = 1 if member.blob_sha is None else 0
        path_id = None
        if member.blob_sha is not None:
            path_id = _canon_path_id(
                conn, ctx.namespace_id if _is_project_rel(member.display_path)
                else "", member.display_path)
        conn.execute(
            "INSERT INTO cert_rows(binding_id, directive_ordinal, "
            "include_spelling, resolved_blob_sha, resolved_path_id, role, "
            "rnf) VALUES(?,?,?,?,?,?,?)",
            (binding_id, ordinal, member.display_path, member.blob_sha,
             path_id, member.role, rnf))
        if member.blob_sha is not None:
            conn.execute(
                "INSERT OR IGNORE INTO binding_file_map(binding_id, "
                "file_ordinal, canon_path_id, blob_sha) VALUES(?,?,?,?)",
                (binding_id, ordinal, path_id, member.blob_sha))


def _insert_branch_states(conn, binding_id: int, cert, *,
                          state_key: str = "",
                          state_generation: int = 0) -> None:
    """T03/ADR-011/ADR-013: write per-binding conditional branch states.

    The cert carries branch states evaluated from the preprocessor's
    ``clang_getAllSkippedRanges`` record at certification time.  Each
    ``(region_ordinal, branch_ordinal, taken)`` tuple maps to one row in
    ``tu_branch_states``, joining to ``semantic_condition_index`` via
    ``region_ordinal`` (which is consistent across both because
    ``_condition_regions`` and ``_evaluate_branch_states`` share the
    same ordinal assignment).

    An empty list means the TU had no conditional directives, the
    preprocessor's skipped-range record was unavailable, OR the cert
    was loaded from the DB via ``_build_recorded_cert`` (fast-reuse path
    where ``branch_states`` is not persisted in ``tu_certificates``).

    In the fast-reuse case, copy branch states from an existing binding
    with the same ``state_key`` + ``state_generation`` — same
    SemanticState means same source, same macros, same branch states.
    """
    branch_states = getattr(cert, "branch_states", None)
    if not branch_states:
        # Fast-reuse path: cert was loaded from the DB without
        # branch_states.  Copy from an existing binding with the same
        # SemanticState (same state_key + state_generation = same
        # branch states, because the macro environment is part of the
        # state identity).
        if not state_key or not state_generation:
            return
        existing = conn.execute(
            "SELECT tbs.region_ordinal, tbs.branch_ordinal, tbs.taken "
            "FROM tu_branch_states tbs "
            "JOIN tu_bindings tb ON tb.binding_id = tbs.binding_id "
            "WHERE tb.state_key=? AND tb.state_generation=? "
            "  AND tb.binding_id != ? "
            "ORDER BY tbs.region_ordinal, tbs.branch_ordinal "
            "LIMIT 1",
            (state_key, state_generation, binding_id)).fetchone()
        if existing is None:
            return  # no existing binding to copy from — fail-open
        conn.execute(
            "INSERT INTO tu_branch_states(binding_id, region_ordinal, "
            "branch_ordinal, taken) "
            "SELECT ?, region_ordinal, branch_ordinal, taken "
            "FROM tu_branch_states tbs "
            "JOIN tu_bindings tb ON tb.binding_id = tbs.binding_id "
            "WHERE tb.state_key=? AND tb.state_generation=? "
            "  AND tb.binding_id != ?",
            (binding_id, state_key, state_generation, binding_id))
        return
    conn.executemany(
        "INSERT INTO tu_branch_states(binding_id, region_ordinal, "
        "branch_ordinal, taken) VALUES(?,?,?,?)",
        [(binding_id, r_ord, b_ord, taken)
         for r_ord, b_ord, taken in branch_states])


def _insert_membership(conn, ctx: V2WorldContext, cert) -> None:
    """K-12 membership rows.  Runs BEFORE cert_rows so the
    ``cert_rows.resolved_blob_sha → file_blobs`` FK is satisfiable.

    file_blobs: content-addressed, ANY origin (project/system/generated).
    snapshot_files: PROJECT files of this TU's closure only (partial,
    ``'delta_certificate'`` completeness — DEDUP-1 substrate)."""
    now = _now()
    for member in cert.closure:
        if member.blob_sha is None:
            continue
        origin = ("generated" if member.generated
                  else "project" if _is_project_rel(member.display_path)
                  else "system")
        # F3 (Slice 6C-F3): persist content bytes for historical retrieval
        content_bytes = (member.text or "").encode("utf-8") if member.text else b""
        conn.execute(
            "INSERT OR IGNORE INTO file_blobs(blob_sha, byte_size, "
            "origin_class, content, first_seen_at) VALUES(?,?,?,?,?)",
            (member.blob_sha, len(content_bytes),
             origin, content_bytes if content_bytes else None, now))
        if _is_project_rel(member.display_path):
            conn.execute(
                "INSERT OR IGNORE INTO snapshot_files(namespace_id, "
                "snapshot_id, canon_path, blob_sha, completeness) "
                "VALUES(?,?,?,?,?)",
                (ctx.namespace_id, ctx.snapshot.snapshot_id,
                 member.display_path, member.blob_sha, "delta_certificate"))

    # F3 (Slice 6C-F3): capture main TU source file content into file_blobs.
    # The main file's blob_sha is cert.source_blob_sha; its bytes were read
    # during certification but never stored.  We re-read here and verify hash.
    if cert.source_blob_sha and getattr(cert, 'main_file_abs_path', ''):
        try:
            from pathlib import Path as _P
            import hashlib as _hl
            raw = _P(cert.main_file_abs_path).read_bytes()
            if _hl.sha256(raw).hexdigest() == cert.source_blob_sha:
                conn.execute(
                    "INSERT OR IGNORE INTO file_blobs(blob_sha, byte_size, "
                    "origin_class, content, first_seen_at) VALUES(?,?,?,?,?)",
                    (cert.source_blob_sha, len(raw), "project", raw, now))
        except OSError:
            pass  # file unreadable — content stays NULL (historical unavail)


def bind_tu(store, ctx: V2WorldContext, cert, *, tu_context_id: str,
            canon_source_path: str, state_key: str, state_generation: int,
            is_new_state: bool) -> BindingResult:
    """Create one TUBinding to a PUBLISHED state (all gates inside one txn)."""
    conn = store._conn
    binding_id: int | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            world_id = _ensure_world_rows(conn, ctx, cert)
            # ── gate 1 (§12): the target state must be PUBLISHED ──────────
            srow = conn.execute(
                "SELECT publication, path_spelling_sensitive FROM "
                "semantic_states WHERE tusemantic_key=? AND "
                "parser_generation=?",
                (state_key, state_generation)).fetchone()
            if srow is None or srow["publication"] != "published":
                _record_event(
                    conn, "binding_to_unpublished_state", key=state_key,
                    generation=state_generation,
                    detail=f"publication="
                           f"{srow['publication'] if srow else 'missing'};"
                           f" world={world_id}; tu={tu_context_id}")
                conn.commit()
                return BindingResult("refused_unpublished", None,
                                     "target state not published")
            # ── gate 2 (A-2): cross-spelling refusal for flagged states ───
            if int(srow["path_spelling_sensitive"] or 0):
                clash = conn.execute(
                    "SELECT canon_source_path FROM tu_bindings WHERE "
                    "state_key=? AND state_generation=? AND "
                    "binding_status='bound' AND canon_source_path<>?",
                    (state_key, state_generation,
                     canon_source_path)).fetchone()
                if clash is not None:
                    _record_event(
                        conn, "binding_path_spelling_refused", key=state_key,
                        generation=state_generation,
                        detail=f"flagged state: '{canon_source_path}' vs "
                               f"'{clash['canon_source_path']}'")
                    conn.commit()
                    return BindingResult("refused_path_spelling", None,
                                         "path_spelling_sensitive state")
            # ── gate 3 (§22): idempotence / conflict ──────────────────────
            existing = conn.execute(
                "SELECT binding_id, state_key, state_generation FROM "
                "tu_bindings WHERE world_id=? AND tu_context_id=?",
                (world_id, tu_context_id)).fetchone()
            if existing is not None:
                if (existing["state_key"] == state_key
                        and existing["state_generation"] == state_generation):
                    conn.commit()
                    return BindingResult("bound_existing",
                                         int(existing["binding_id"]),
                                         "idempotent replay")
                _record_event(
                    conn, "binding_conflict", key=state_key,
                    generation=state_generation,
                    detail=f"world={world_id} tu={tu_context_id}: existing="
                           f"{existing['state_key']}"
                           f"@{existing['state_generation']}")
                conn.commit()
                return BindingResult("refused_conflict", None,
                                     "binding identity holds another state")

            # ── inserts: binding + details + certs + membership ───────────
            cur = conn.execute(
                "INSERT INTO tu_bindings(world_id, tu_context_id, "
                "canon_source_path, source_blob_sha, ncc_digest_sha, "
                "state_key, state_generation, binding_status, disposition, "
                "poison_component, cert_digest_sha) "
                "VALUES(?,?,?,?,?,?,?,'bound','ok',NULL,?)",
                (world_id, tu_context_id, canon_source_path,
                 cert.source_blob_sha, cert.key_result.L1, state_key,
                 state_generation, _cert_rows_digest(cert)))
            binding_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO tu_context_details(binding_id, ncc_serial, "
                "rsp_record, presentation_context) VALUES(?,?,?,?)",
                (binding_id, cert.ncc_serial,
                 json.dumps({"ncc_warnings": cert.ncc_warnings,
                             "rsp_warnings": cert.rsp_warnings},
                            sort_keys=True),
                 json.dumps({"language": cert.language,
                             "main_file_display": cert.main_file_display},
                            sort_keys=True)))
            _insert_membership(conn, ctx, cert)
            _insert_cert_rows(conn, ctx, binding_id, cert)
            # T03/ADR-011/ADR-013: write per-binding branch states from
            # the cert's preprocessor-evaluated conditions.  Each binding
            # gets its own branch states because the same SemanticState
            # can be bound to worlds with different macro environments.
            # When the cert was freshly built (certify_and_publish_for_unit),
            # branch_states is populated.  When the cert was loaded from
            # the DB via _build_recorded_cert (fast-reuse path), branch_states
            # is empty — in that case, copy from an existing binding with
            # the same state (same SemanticState = same branch states).
            _insert_branch_states(conn, binding_id, cert,
                                  state_key=state_key,
                                  state_generation=state_generation)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    except Exception as exc:                              # pragma: no cover
        return BindingResult("error", None, str(exc)[:200])
    ctx.inc("bindings_created")
    ctx.note_state(state_key, state_generation, is_new_state)
    ctx.note_processed(tu_context_id)
    return BindingResult("bound_new", binding_id)


def record_poison(store, ctx: V2WorldContext, cert, *, tu_context_id: str,
                  canon_source_path: str) -> BindingResult:
    """§16: honest poisoned-TU disposition (world/TU level).

    Creates ``binding_status='poisoned'`` + ``world_poisons`` +
    ``tu_private_payloads`` rows.  NO fake semantic-state key is minted
    (INV-6); the poisoned TU is never silently omitted, so a finalized world
    carrying one is published-but-degraded (D-4 world-level disposition).
    """
    conn = store._conn
    now = _now()
    reason = cert.poison_reason or "unknown"
    component = f"tu:{tu_context_id}:{reason.split(':', 1)[0]}"
    nonce = (cert.inputs.nonce if cert.inputs else "") or "none"
    payload = json.dumps({
        "poison_reason": reason[:400], "cert_version": cert.cert_version,
        "nonce": nonce, "context_id": cert.context_id,
        "capture_version": CAPTURE_VERSION}, sort_keys=True)
    binding_id: int | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            world_id = _ensure_world_rows(conn, ctx, cert)
            existing = conn.execute(
                "SELECT binding_id, binding_status FROM tu_bindings WHERE "
                "world_id=? AND tu_context_id=?",
                (world_id, tu_context_id)).fetchone()
            if existing is not None:
                conn.commit()
                if existing["binding_status"] == "poisoned":
                    return BindingResult("poisoned_recorded",
                                         int(existing["binding_id"]),
                                         "idempotent replay")
                return BindingResult("refused_conflict", None,
                                     "binding identity already bound")
            cur = conn.execute(
                "INSERT INTO tu_bindings(world_id, tu_context_id, "
                "canon_source_path, source_blob_sha, ncc_digest_sha, "
                "state_key, state_generation, binding_status, disposition, "
                "poison_component, cert_digest_sha) "
                "VALUES(?,?,?,?,?,NULL,NULL,'poisoned','poisoned',?,NULL)",
                (world_id, tu_context_id, canon_source_path,
                 cert.source_blob_sha or "",
                 cert.inputs.ncc_serial if cert.inputs else "", component))
            binding_id = int(cur.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO world_poisons(world_id, component, "
                "nonce, poison_tag, detected_at) VALUES(?,?,?,?,?)",
                (world_id, component, nonce,
                 reason.split(":", 1)[0][:80], now))
            conn.execute(
                "INSERT INTO tu_private_payloads(binding_id, world_id, "
                "payload_format, payload, payload_digest, reason) "
                "VALUES(?,?,?,?,?,?)",
                (binding_id, world_id, "application/json",
                 payload.encode("utf-8"),
                 h("fwctx-priv/v1", payload)[:64], reason[:200]))
            conn.execute(
                "INSERT INTO tu_context_details(binding_id, ncc_serial, "
                "rsp_record, presentation_context) VALUES(?,?,?,?)",
                (binding_id, cert.ncc_serial or "",
                 json.dumps({"ncc_warnings": cert.ncc_warnings,
                             "rsp_warnings": cert.rsp_warnings},
                            sort_keys=True),
                 json.dumps({"poisoned": True}, sort_keys=True)))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    except Exception as exc:                              # pragma: no cover
        return BindingResult("error", None, str(exc)[:200])
    ctx.inc("poisoned_tus")
    ctx.note_processed(tu_context_id)
    return BindingResult("poisoned_recorded", binding_id)


@dataclass(frozen=True)
class FinalizeResult:
    ok: bool
    bound_count: int
    poisoned_count: int
    missing: tuple
    world_status: str


def finalize_world(store, ctx: V2WorldContext) -> FinalizeResult:
    """DECISION Row-3 lifecycle: ``'unfinalized' → 'active'`` with checks.

    Verifies (task §28): the world row exists and is unfinalized; every TU
    context processed by this run carries a bound-or-poisoned row; world
    provenance exists.  A failing world STAYS ``'unfinalized'`` — a future
    strict QueryView (Slice 3) can then reject it (task §10 invariant).
    """
    conn = store._conn
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if ctx.build_identity_sha is None:
                conn.commit()
                return FinalizeResult(False, 0, 0, ("no-world",), "missing")
            row = conn.execute(
                "SELECT world_id, world_status FROM snapshot_builds WHERE "
                "namespace_id=? AND snapshot_id=? AND build_identity_sha=?",
                (ctx.namespace_id, ctx.snapshot.snapshot_id,
                 ctx.build_identity_sha)).fetchone()
            if row is None:
                conn.commit()
                return FinalizeResult(False, 0, 0, ("no-world",), "missing")
            world_id = int(row["world_id"])
            rows = conn.execute(
                "SELECT tu_context_id, binding_status FROM tu_bindings "
                "WHERE world_id=?", (world_id,)).fetchall()
            present = {r["tu_context_id"]: r["binding_status"] for r in rows}
            bound = sum(1 for s in present.values() if s == "bound")
            poisoned = sum(1 for s in present.values() if s == "poisoned")
            missing = tuple(sorted(
                ctxid for ctxid in ctx.processed_tu_contexts
                if ctxid not in present))
            prov = conn.execute(
                "SELECT run_provenance FROM snapshot_builds WHERE world_id=?",
                (world_id,)).fetchone()
            prov_ok = bool(prov and (prov["run_provenance"] or "").strip())
            if missing or not prov_ok:
                conn.commit()
                return FinalizeResult(False, bound, poisoned, missing,
                                      "unfinalized")
            conn.execute(
                "UPDATE snapshot_builds SET world_status='active' WHERE "
                "world_id=? AND world_status='unfinalized'", (world_id,))
            ensure_slice2_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO slice2_run_metrics(run_id, metric, "
                "value, detail, updated_at) VALUES(?,?,?,?,?)",
                (ctx.run_id, "finalized", 1,
                 json.dumps({"world_id": world_id, "bound": bound,
                             "poisoned": poisoned}, sort_keys=True), _now()))
            conn.commit()
            return FinalizeResult(True, bound, poisoned, missing, "active")
        except BaseException:
            conn.rollback()
            raise
    except Exception as exc:                              # pragma: no cover
        return FinalizeResult(False, 0, 0, (str(exc)[:120],), "error")


def record_run_metrics(store, ctx: V2WorldContext) -> None:
    """§40: persist the per-run counters (work-saved claims start Slice 4)."""
    conn = store._conn
    now = _now()
    metrics = ctx.snapshot_metrics()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, value in sorted(metrics.items()):
            conn.execute(
                "INSERT OR REPLACE INTO slice2_run_metrics(run_id, metric, "
                "value, detail, updated_at) VALUES(?,?,?,?,?)",
                (ctx.run_id, name, int(value), None, now))
        ensure_slice2_schema(conn)
        conn.commit()
    except Exception:                                     # pragma: no cover
        try:
            conn.rollback()
        except Exception:
            pass


def capture_extra_files(store, ctx: V2WorldContext, project_root) -> int:
    """G10 historical integrity: capture non-TU files (assembly, linker scripts)
    into ``world_extra_files`` so queries read from immutable indexed content,
    NOT live disk state.

    Scans ``project_root`` for ``.s``/``.S`` (assembly) files and stores their
    content per-World.  This is the ONLY way ``get_vector_table`` can honor
    historical World semantics — without it, the adapter would read current
    disk content, violating the immutability contract when assembly files
    change or are deleted after indexing.

    Returns the number of files captured.
    """
    import hashlib
    from pathlib import Path as _P

    conn = store._conn
    world_row = conn.execute(
        "SELECT world_id FROM snapshot_builds WHERE namespace_id=? AND "
        "snapshot_id=? AND build_identity_sha=?",
        (ctx.namespace_id, ctx.snapshot.snapshot_id,
         ctx.build_identity_sha)).fetchone()
    if world_row is None:
        return 0
    world_id = int(world_row["world_id"])

    root = _P(project_root)
    # Deduplicate case-insensitively on Windows
    asm_paths = set()
    for ext in ("*.s", "*.S"):
        for f in root.rglob(ext):
            asm_paths.add(f.resolve())

    captured = 0
    now = _now()
    for asm_path in sorted(asm_paths):
        try:
            content = asm_path.read_bytes()
        except Exception:
            continue
        # Canon path relative to project root
        try:
            canon = str(asm_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            canon = str(asm_path)
        blob_sha = hashlib.sha256(content).hexdigest()
        conn.execute(
            "INSERT OR REPLACE INTO world_extra_files"
            "(world_id, canon_path, file_role, blob_sha, content, "
            "byte_size, captured_at) VALUES(?,?,?,?,?,?,?)",
            (world_id, canon, "assembly", blob_sha, content,
             len(content), now))
        captured += 1

    if captured:
        conn.commit()
    return captured




def world_claim(store, ctx: V2WorldContext, owner: str, ttl_s: float = 300.0
                ) -> bool:
    """Minimal D2-S3 world claim: coordinate, never arbitrate semantics."""
    conn = store._conn
    now = time.time()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT owner_id, lease_deadline FROM world_claims WHERE "
            "world_id=?", (ctx.world_id,)).fetchone()
        if row is not None:
            try:
                lease = json.loads(row["owner_id"] or "{}")
            except ValueError:
                lease = {}
            deadline = float(row["lease_deadline"] or 0)
            if lease.get("owner") != owner and deadline > now:
                conn.commit()
                return False
        conn.execute(
            "INSERT OR REPLACE INTO world_claims(world_id, owner_id, "
            "lease_deadline, heartbeat_at, mode, acquired_at) "
            "VALUES(?,?,?,?, 'binding', ?)",
            (ctx.world_id, json.dumps({"owner": owner}), now + ttl_s,
             now, _now()))
        conn.commit()
        return True
    except Exception:                                     # pragma: no cover
        try:
            conn.rollback()
        except Exception:
            pass
        return False
