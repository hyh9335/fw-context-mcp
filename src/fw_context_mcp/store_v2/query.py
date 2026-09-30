"""store_v2.query — Phase-4 Slice 3 STRICT shadow read path.

Every v2 semantic read occurs inside an explicit, validated QueryView
(PHASE2 QUERY_VIEW_CONTRACT §1: "the view names the world; the store owes
the facts").  The strict resolution pipeline (task §3) is:

    namespace → snapshot → build → exactly one World → world-status gate
    → TUBindings → published SemanticStates → facts + per-world
    presentation/provenance

Zero-tolerance semantics implemented here:

* NO newest-created-at / first-context / CWD / active-config fallback —
  absence and ambiguity are errors, never silent picks (contract §4).
* ``AMBIGUOUS_TARGET`` enumerates its candidates so the caller can
  disambiguate explicitly; nothing is chosen automatically.
* ``semantic_states`` is storage (task §8): a state a world's bindings do
  not reference is INVISIBLE to this module, even if it shares a symbol
  name or raw USR (§36: ``DB contains fact`` ≠ ``fact visible``).
* Read-time publication gate (task §5, defense in depth): a binding whose
  target row is missing, staging, or quarantined fails CLOSED even though
  Slice-2 insert gates should have prevented it.
* Poison is NEVER treated as absence (task §4/§14): a poisoned TU yields an
  explicit structured degraded result carrying its provenance/reason.
* READ-ONLY (task §20): every statement here is a SELECT; the convenience
  :func:`open_query_connection` opens ``file:...?mode=ro`` so writes are
  structurally impossible.  Telemetry (:class:`QueryTelemetry`) is a plain
  in-memory object — the query path performs zero writes (S3-T16).

This module is shadow-only (task §21): no legacy query API is redirected,
no MCP surface changes, no Slice-4 reuse, no Slice-5 retention.  It imports
nothing from the indexer write path and never writes.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── §26 additive read-efficiency indexes (documented in SLICE3_SCHEMA_DELTA) ──

SLICE3_DDL: tuple[str, ...] = (
    # Q2: symbol lookup by name within one state's local symbol table.
    "CREATE INDEX IF NOT EXISTS idx_symbols_core_state_name "
    "ON semantic_symbols_core(state_id, name)",
    # Q1: TU selection by canonical path inside one world.
    "CREATE INDEX IF NOT EXISTS idx_tu_bindings_world_path "
    "ON tu_bindings(world_id, canon_source_path)",
)


def ensure_slice3_schema(conn: sqlite3.Connection) -> None:
    """Create the additive Slice-3 indexes (idempotent; WRITE-path only).

    Never called from a read function: the query path must stay read-only
    (task §20).  The production write path invokes this once per finalized
    run (runner finalize block); store fixtures call it explicitly.
    """
    for stmt in SLICE3_DDL:
        conn.execute(stmt)
    conn.commit()


# ── §6/§2 the QueryView value object (frozen, explicit selectors only) ────────

QUERYABLE_WORLD_STATUS = frozenset({"active"})


@dataclass(frozen=True)
class TuSelector:
    """Optional narrowing of a QueryView to ONE compile context.

    Contract §2 admits an opaque context id or an explicit description that
    resolves to exactly one context.  Slice 3 admits exactly two forms:
    the opaque ``tu_context_id`` (per-world parse-action identity,
    SAFE_REUSE G4) or the exact project-relative ``canon_source_path``.
    Exactly one form must be set — enforced at construction (§16).
    """

    tu_context_id: str | None = None
    canon_source_path: str | None = None

    def __post_init__(self) -> None:
        if (self.tu_context_id is None) == (self.canon_source_path is None):
            raise IncompleteView(
                "tu_selector requires exactly one of tu_context_id or "
                "canon_source_path",
                detail={"tu_context_id": self.tu_context_id,
                        "canon_source_path": self.canon_source_path})
        if self.tu_context_id is not None and not self.tu_context_id:
            raise IncompleteView("tu_context_id must be non-empty")
        if self.canon_source_path is not None and not self.canon_source_path:
            raise IncompleteView("canon_source_path must be non-empty")

    def kind(self) -> str:
        return ("tu_context_id" if self.tu_context_id is not None
                else "canon_source_path")

    def value(self) -> str:
        return self.tu_context_id if self.tu_context_id is not None \
            else self.canon_source_path  # type: ignore[return-value]

    def to_diag(self) -> str:
        return f"{self.kind()}={self.value()!r}"


@dataclass(frozen=True)
class QueryView:
    """The complete explicit resolution of ONE semantic world.

    Field names follow the frozen Phase-2 contract (QUERY_VIEW_CONTRACT §2):

    * ``repository_namespace`` — the opaque checkout-independent namespace
      token (the v2 store's ``repository_namespaces.namespace_id``, i.e.
      ``"r:<repository_key_sha>"`` or ``"t:<project token>"``).  NEVER a
      filesystem root, never a CWD product (contract §2 row 1).
    * ``snapshot_id`` — the SourceSnapshotID identity
      (``snapshots.snapshot_id``).
    * ``build_id`` — the opaque BuildIdentity of one completed build world.
      In the v2 store this is ``builds.build_identity_sha`` (the identity);
      the INTEGER ``builds.build_id`` is a storage handle (INV-3) and is
      deliberately NOT accepted as a selector.
    * ``tu_selector`` — optional narrowing (contract §2 row 4).  Omitted ⇒
      the view addresses ALL contexts of the world; observations are never
      merged across contexts — every result carries per-binding provenance.
    """

    repository_namespace: str
    snapshot_id: str
    build_id: str
    tu_selector: TuSelector | None = None

    def __post_init__(self) -> None:
        for name in ("repository_namespace", "snapshot_id", "build_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise IncompleteView(
                    f"QueryView.{name} is mandatory and must be a "
                    f"non-empty string (no defaults exist)",
                    detail={"field": name, "value": repr(value)})
        if self.tu_selector is not None and \
                not isinstance(self.tu_selector, TuSelector):
            raise IncompleteView("tu_selector must be a TuSelector")

    def to_diag(self) -> str:
        """Deterministic, diagnostic-friendly representation (task §6)."""
        tu = f", {self.tu_selector.to_diag()}" if self.tu_selector else ""
        return (f"QueryView(repository_namespace={self.repository_namespace!r},"
                f" snapshot_id={self.snapshot_id!r},"
                f" build_id={self.build_id!r}{tu})")


# ── §15 error taxonomy (contract §4 names + v2 world/read refinements) ────────

class QueryViewError(Exception):
    """Base of the strict read-path taxonomy.  Carries structured detail."""

    code = "QUERYVIEW_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail: dict = dict(detail or {})

    def to_diag(self) -> str:
        return f"{self.code}: {self.message} detail={self.detail!r}"


class IncompleteView(QueryViewError):
    """A mandatory selector field was omitted/invalid — construction time or
    call time.  No defaulting, ever (contract §4)."""
    code = "INCOMPLETE_VIEW"


class UnknownTarget(QueryViewError):
    """A selector resolves to zero worlds (contract §4 name)."""
    code = "UNKNOWN_TARGET"


class WorldNotFound(UnknownTarget):
    """§15 refinement: the selected namespace/snapshot/build/world does not
    exist in the store.  ``detail['stage']`` names the failing leg."""
    code = "WORLD_NOT_FOUND"


class AmbiguousTarget(QueryViewError):
    """A selector matches more than one candidate; the error enumerates
    every candidate identity (contract §4: no ordering-based pick)."""
    code = "AMBIGUOUS_TARGET"

    def __init__(self, message: str, *, candidates: list[dict],
                 detail: dict | None = None):
        super().__init__(message, detail={**(detail or {}),
                                          "candidates": candidates})
        self.candidates: list[dict] = candidates


class WorldNotQueryable(QueryViewError):
    """The world exists but its lifecycle state does not serve queries
    (unfinalized/superseded/retired, or missing run provenance)."""
    code = "WORLD_NOT_QUERYABLE"


class TuNotInWorld(QueryViewError):
    """The world is valid but the selected TU context has no binding row."""
    code = "TU_NOT_IN_WORLD"


class TuPoisoned(QueryViewError):
    """The selected TU exists but carries an explicit poison disposition —
    there is no shareable SemanticState (INV-6).  Poison provenance is in
    ``detail['poison']`` (task §14: never NOT_FOUND, never fake facts)."""
    code = "TU_POISONED"


class SemanticStateNotPublished(QueryViewError):
    """A binding points at a staging/quarantined state — read-time INV-8
    gate fails closed (task §5)."""
    code = "SEMANTIC_STATE_NOT_PUBLISHED"


class StoreIntegrityError(QueryViewError):
    """Corrupt/manual storage state detected at read time (binding → absent
    state, unexpected binding_status, malformed world rows)."""
    code = "INTEGRITY_ERROR"


class SymbolNotFound(QueryViewError):
    """A valid world/TU state was searched and holds no matching symbol."""
    code = "SYMBOL_NOT_FOUND"


# ── §37 telemetry (in-memory; never persisted by the read path) ───────────────

@dataclass
class QueryTelemetry:
    """§37 strict-query counters.  Plain object — the query path never
    writes it anywhere (S3-T16); callers may persist snapshots themselves."""

    queryview_resolution_count: int = 0
    queryview_resolution_failures: int = 0
    worlds_considered: int = 0
    bindings_considered: int = 0
    semantic_states_loaded: int = 0
    poison_results: int = 0
    last_resolution_ms: float | None = None

    def snapshot(self) -> dict:
        return {
            "queryview_resolution_count": self.queryview_resolution_count,
            "queryview_resolution_failures": self.queryview_resolution_failures,
            "worlds_considered": self.worlds_considered,
            "bindings_considered": self.bindings_considered,
            "semantic_states_loaded": self.semantic_states_loaded,
            "poison_results": self.poison_results,
            "last_resolution_ms": self.last_resolution_ms,
        }


# ── read-only connection (§20: writes structurally impossible) ────────────────

def open_query_connection(v2_path: Path) -> sqlite3.Connection:
    """Open the v2 store READ-ONLY (``mode=ro``); row access by name."""
    conn = sqlite3.connect(f"file:{Path(v2_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── result model (§29: semantic payload ⊥ world/presentation provenance) ──────

@dataclass(frozen=True)
class ResolvedWorld:
    """§7: everything downstream needs, explicitly carried."""

    view: QueryView
    namespace_id: str
    repository_key_sha: str
    namespace_tag: str
    snapshot_id: str
    snapshot_verification: str
    build_identity_sha: str
    build_handle: int                 # storage handle (diagnostic only)
    world_id: int
    world_status: str
    run_provenance: str               # JSON text (world-local; R-BD4)

    @property
    def build_id(self) -> str:
        return self.build_identity_sha

    def provenance(self) -> dict:
        return {
            "view": self.view.to_diag(),
            "namespace_id": self.namespace_id,
            "snapshot_id": self.snapshot_id,
            "build_id": self.build_id,
            "world_id": self.world_id,
            "world_status": self.world_status,
            "snapshot_verification": self.snapshot_verification,
        }


@dataclass(frozen=True)
class StateSummary:
    """Shared SemanticState identity + publication facts (storage layer)."""

    state_id: int                     # storage handle (diagnostic only)
    tusemantic_key: str
    parser_generation: int
    publication: str
    key_format: str
    completeness_sha: str
    fact_row_count: int | None
    path_spelling_sensitive: bool

    def identity(self) -> dict:
        return {"tusemantic_key": self.tusemantic_key,
                "parser_generation": self.parser_generation}


@dataclass(frozen=True)
class BindingRef:
    """Per-world binding identity + disposition (Tier B)."""

    binding_id: int
    world_id: int
    tu_context_id: str
    canon_source_path: str
    binding_status: str
    disposition: str
    state_key: str | None
    state_generation: int | None
    source_blob_sha: str
    ncc_digest_sha: str
    poison_component: str | None = None


@dataclass(frozen=True)
class PoisonInfo:
    component: str
    poison_tag: str
    nonce: str
    detected_at: str | None
    reason: str | None                # from tu_private_payloads

    def as_dict(self) -> dict:
        return {"component": self.component, "poison_tag": self.poison_tag,
                "nonce": self.nonce, "detected_at": self.detected_at,
                "reason": self.reason}


@dataclass(frozen=True)
class TUQueryResult:
    """Q1: TU/file semantic state inspection — bound or explicitly poisoned."""

    disposition: str                  # 'bound' | 'poisoned'
    binding: BindingRef
    state: StateSummary | None
    poison: PoisonInfo | None
    provenance: dict

    def as_dict(self) -> dict:
        return {"disposition": self.disposition,
                "state": self.state.identity() if self.state else None,
                "poison": self.poison.as_dict() if self.poison else None,
                "provenance": self.provenance}


@dataclass(frozen=True)
class SymbolHit:
    """Q2 result: a state-local symbol WITH its binding/world provenance."""

    name: str
    raw_usr: str | None               # ATTRIBUTE only (never a key)
    kind: str | None
    is_definition: bool
    signature: str | None
    local_key: int
    state: StateSummary
    binding: BindingRef
    provenance: dict


@dataclass
class SymbolQueryResult:
    """Q2 aggregate: explicit hits + explicitly disclosed degraded TUs."""

    name: str
    hits: list[SymbolHit] = field(default_factory=list)
    degraded_tus: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StateSymbolRef:
    """State-qualified symbol identity for edge addressing (task §11)."""

    tusemantic_key: str
    parser_generation: int
    local_key: int


@dataclass(frozen=True)
class EdgeHit:
    """Q3 result: one state-local edge with its resolved endpoint."""

    edge_class: str
    edge_ordinal: int
    to_local_key: int | None          # resolved endpoint WITHIN the same state
    to_symbol: StateSymbolRef | None
    to_resolved_name: str | None
    to_raw_usr: str | None            # unresolved/cross-state: ATTRIBUTE
    to_name: str | None               # as-stored attribute (indirect/fp edges)
    certainty: str | None
    resolution_kind: str | None
    provenance: dict


@dataclass(frozen=True)
class ConditionHit:
    """Q4a: one shared condition region + the binding's per-world branch
    states (Tier-B rows; empty list when the binding carries none)."""

    region_ordinal: int
    parent_region_ordinal: int | None
    directive: str | None
    condition: str | None
    file_blob_sha: str | None
    branch_states: list[dict]
    provenance: dict


@dataclass(frozen=True)
class PresentationRow:
    """One closure file: shared identity (ordinal/role/blob) + per-world
    presentation (canon path via the binding's file map, A-4)."""

    file_ordinal: int
    role: str
    blob_sha: str                     # shared identity leg
    canon_path: str | None            # per-world presentation leg


@dataclass(frozen=True)
class TUClosureView:
    """Q4b: shared semantic facts + per-world presentation composition."""

    state: StateSummary
    binding: BindingRef
    files: list[PresentationRow]
    cert_directives: list[dict]       # include_spelling + resolved canon path
    ncc_serial: str | None
    rsp_record: str | None
    provenance: dict


# ── internal helpers ──────────────────────────────────────────────────────────

def _row_binding(row) -> BindingRef:
    return BindingRef(
        binding_id=int(row["binding_id"]), world_id=int(row["world_id"]),
        tu_context_id=row["tu_context_id"],
        canon_source_path=row["canon_source_path"],
        binding_status=row["binding_status"],
        disposition=row["disposition"], state_key=row["state_key"],
        state_generation=(None if row["state_generation"] is None
                          else int(row["state_generation"])),
        source_blob_sha=row["source_blob_sha"],
        ncc_digest_sha=row["ncc_digest_sha"],
        poison_component=row["poison_component"])


def _row_state(row) -> StateSummary:
    return StateSummary(
        state_id=int(row["state_id"]), tusemantic_key=row["tusemantic_key"],
        parser_generation=int(row["parser_generation"]),
        publication=row["publication"], key_format=row["key_format"],
        completeness_sha=row["completeness_sha"],
        fact_row_count=(None if row["fact_row_count"] is None
                        else int(row["fact_row_count"])),
        path_spelling_sensitive=bool(row["path_spelling_sensitive"]))


def _poison_info(conn, resolved: ResolvedWorld, binding: BindingRef
                 ) -> PoisonInfo:
    prow = conn.execute(
        "SELECT component, poison_tag, nonce, detected_at FROM world_poisons "
        "WHERE world_id=? AND component=?",
        (resolved.world_id, binding.poison_component)).fetchone()
    lrow = conn.execute(
        "SELECT reason FROM tu_private_payloads WHERE binding_id=?",
        (binding.binding_id,)).fetchone()
    return PoisonInfo(
        component=binding.poison_component or "",
        poison_tag=(prow["poison_tag"] if prow is not None else ""),
        nonce=(prow["nonce"] if prow is not None else ""),
        detected_at=(prow["detected_at"] if prow is not None else None),
        reason=(lrow["reason"] if lrow is not None else None))


def _load_published_state(conn, *, state_key: str, state_generation: int,
                          telemetry: QueryTelemetry | None) -> StateSummary:
    """§5 read-time publication gate (defense in depth, fail closed)."""
    row = conn.execute(
        "SELECT * FROM semantic_states WHERE tusemantic_key=? AND "
        "parser_generation=?", (state_key, state_generation)).fetchone()
    if row is None:
        raise StoreIntegrityError(
            "binding references an absent SemanticState",
            detail={"tusemantic_key": state_key,
                    "parser_generation": state_generation})
    if telemetry is not None:
        telemetry.semantic_states_loaded += 1
    summary = _row_state(row)
    if summary.publication != "published":
        raise SemanticStateNotPublished(
            f"binding target is {summary.publication!r}, not published",
            detail={**summary.identity(), "publication": summary.publication})
    return summary


def _resolve_selector(conn, resolved: ResolvedWorld, selector: TuSelector,
                      telemetry: QueryTelemetry | None) -> BindingRef:
    """§3 step 6 for ONE context: exact id, or path that must be unambiguous."""
    if selector.tu_context_id is not None:
        row = conn.execute(
            "SELECT * FROM tu_bindings WHERE world_id=? AND tu_context_id=?",
            (resolved.world_id, selector.tu_context_id)).fetchone()
        if row is None:
            raise TuNotInWorld(
                f"no binding for tu_context_id={selector.tu_context_id!r} "
                f"in world {resolved.world_id}",
                detail={"tu_context_id": selector.tu_context_id,
                        "world_id": resolved.world_id})
        if telemetry is not None:
            telemetry.bindings_considered += 1
        return _row_binding(row)
    rows = conn.execute(
        "SELECT * FROM tu_bindings WHERE world_id=? AND canon_source_path=? "
        "ORDER BY binding_id",
        (resolved.world_id, selector.canon_source_path)).fetchall()
    if not rows:
        raise TuNotInWorld(
            f"no binding for canon_source_path={selector.canon_source_path!r} "
            f"in world {resolved.world_id}",
            detail={"canon_source_path": selector.canon_source_path,
                    "world_id": resolved.world_id})
    if len(rows) > 1:
        raise AmbiguousTarget(
            "canon_source_path matches multiple compile contexts in this "
            "world; select one explicitly by tu_context_id",
            candidates=[{"tu_context_id": r["tu_context_id"],
                         "binding_id": int(r["binding_id"]),
                         "binding_status": r["binding_status"]}
                        for r in rows],
            detail={"canon_source_path": selector.canon_source_path,
                    "world_id": resolved.world_id})
    if telemetry is not None:
        telemetry.bindings_considered += 1
    return _row_binding(rows[0])


def _pick_selector(view: QueryView, selector: TuSelector | None) -> TuSelector:
    chosen = selector if selector is not None else view.tu_selector
    if chosen is None:
        raise IncompleteView(
            "this query narrows to one compile context and requires a "
            "tu_selector (view-level or explicit)")
    return chosen


# ── §3/§7 the strict resolver ─────────────────────────────────────────────────

def resolve_query_view(conn: sqlite3.Connection, view: QueryView, *,
                       telemetry: QueryTelemetry | None = None
                       ) -> ResolvedWorld:
    """Resolve a QueryView to EXACTLY ONE queryable world (task §3).

    namespace → snapshot → build → world(0 ⇒ WORLD_NOT_FOUND, >1 ⇒
    AMBIGUOUS_TARGET with candidates) → status gate ('active' only) →
    provenance present.  Read-only; performs no writes.
    """
    t0 = time.perf_counter()
    if telemetry is not None:
        telemetry.queryview_resolution_count += 1
    try:
        ns = conn.execute(
            "SELECT namespace_id, repository_key_sha FROM "
            "repository_namespaces WHERE namespace_id=?",
            (view.repository_namespace,)).fetchone()
        if ns is None:
            raise WorldNotFound(
                f"namespace {view.repository_namespace!r} does not exist",
                detail={"stage": "namespace",
                        "repository_namespace": view.repository_namespace})
        snap = conn.execute(
            "SELECT snapshot_id, verification, namespace_tag FROM snapshots "
            "WHERE namespace_id=? AND snapshot_id=?",
            (view.repository_namespace, view.snapshot_id)).fetchone()
        if snap is None:
            raise WorldNotFound(
                f"snapshot {view.snapshot_id!r} does not exist in namespace "
                f"{view.repository_namespace!r}",
                detail={"stage": "snapshot",
                        "snapshot_id": view.snapshot_id})
        build = conn.execute(
            "SELECT build_id, build_identity_sha FROM builds WHERE "
            "namespace_id=? AND build_identity_sha=?",
            (view.repository_namespace, view.build_id)).fetchone()
        if build is None:
            raise WorldNotFound(
                f"build {view.build_id!r} does not exist in namespace "
                f"{view.repository_namespace!r}",
                detail={"stage": "build", "build_id": view.build_id})
        worlds = conn.execute(
            "SELECT * FROM snapshot_builds WHERE namespace_id=? AND "
            "snapshot_id=? AND build_identity_sha=? ORDER BY world_id",
            (view.repository_namespace, view.snapshot_id,
             view.build_id)).fetchall()
        if telemetry is not None:
            telemetry.worlds_considered += len(worlds)
        if not worlds:
            raise WorldNotFound(
                "no world for the requested (namespace, snapshot, build) "
                "triple",
                detail={"stage": "world",
                        "namespace_id": view.repository_namespace,
                        "snapshot_id": view.snapshot_id,
                        "build_id": view.build_id})
        if len(worlds) > 1:
            # Defense in depth: the frozen UNIQUE(namespace, snapshot,
            # build) constraint makes this unreachable on a well-formed
            # store; if manual corruption defeats it, fail closed rather
            # than pick (I4).
            raise AmbiguousTarget(
                "world identity triple matches multiple world rows — "
                "malformed store; refusing to choose",
                candidates=[{"world_id": int(w["world_id"]),
                             "world_status": w["world_status"]}
                            for w in worlds],
                detail={"namespace_id": view.repository_namespace,
                        "snapshot_id": view.snapshot_id,
                        "build_id": view.build_id})
        wrow = worlds[0]
        if wrow["world_status"] not in QUERYABLE_WORLD_STATUS:
            raise WorldNotQueryable(
                f"world {int(wrow['world_id'])} has status "
                f"{wrow['world_status']!r}; only "
                f"{sorted(QUERYABLE_WORLD_STATUS)} worlds serve queries",
                detail={"world_id": int(wrow["world_id"]),
                        "world_status": wrow["world_status"]})
        run_provenance = wrow["run_provenance"] or ""
        if not run_provenance:
            raise WorldNotQueryable(
                f"world {int(wrow['world_id'])} carries no run provenance",
                detail={"world_id": int(wrow["world_id"])})
        resolved = ResolvedWorld(
            view=view, namespace_id=ns["namespace_id"],
            repository_key_sha=ns["repository_key_sha"],
            namespace_tag=snap["namespace_tag"],
            snapshot_id=snap["snapshot_id"],
            snapshot_verification=snap["verification"],
            build_identity_sha=build["build_identity_sha"],
            build_handle=int(build["build_id"]),
            world_id=int(wrow["world_id"]),
            world_status=wrow["world_status"],
            run_provenance=run_provenance)
        if telemetry is not None:
            telemetry.last_resolution_ms = (time.perf_counter() - t0) * 1000.0
        return resolved
    except QueryViewError:
        if telemetry is not None:
            telemetry.queryview_resolution_failures += 1
            telemetry.last_resolution_ms = (time.perf_counter() - t0) * 1000.0
        raise


def resolve_bindings(conn: sqlite3.Connection, resolved: ResolvedWorld, *,
                     telemetry: QueryTelemetry | None = None
                     ) -> list[BindingRef]:
    """§3 step 6: every binding row of the resolved world (all dispositions)."""
    rows = conn.execute(
        "SELECT * FROM tu_bindings WHERE world_id=? ORDER BY binding_id",
        (resolved.world_id,)).fetchall()
    if telemetry is not None:
        telemetry.bindings_considered += len(rows)
    return [_row_binding(r) for r in rows]


# ── Q1 (§9): TU/file semantic state inspection ────────────────────────────────

def inspect_tu(conn: sqlite3.Connection, view: QueryView, *,
               selector: TuSelector | None = None,
               telemetry: QueryTelemetry | None = None) -> TUQueryResult:
    """Binding/state/provenance summary for ONE compile context.

    A poisoned TU returns an explicit structured degraded result
    (``disposition='poisoned'`` + full poison provenance) — never
    NOT_FOUND, never fake facts (task §14).  A bound TU returns only after
    the read-time publication gate passes.
    """
    resolved = resolve_query_view(conn, view, telemetry=telemetry)
    binding = _resolve_selector(conn, resolved, _pick_selector(view, selector),
                                telemetry)
    if binding.binding_status == "poisoned" or \
            binding.disposition == "poisoned":
        info = _poison_info(conn, resolved, binding)
        if telemetry is not None:
            telemetry.poison_results += 1
        return TUQueryResult(
            disposition="poisoned", binding=binding, state=None, poison=info,
            provenance={**resolved.provenance(),
                        "binding_id": binding.binding_id,
                        "tu_context_id": binding.tu_context_id,
                        "canon_source_path": binding.canon_source_path,
                        "binding_status": binding.binding_status,
                        "disposition": binding.disposition})
    if binding.binding_status != "bound":
        raise StoreIntegrityError(
            f"unexpected binding_status {binding.binding_status!r}",
            detail={"binding_id": binding.binding_id,
                    "binding_status": binding.binding_status})
    if binding.state_key is None or binding.state_generation is None:
        raise StoreIntegrityError(
            "bound binding carries no state identity",
            detail={"binding_id": binding.binding_id})
    state = _load_published_state(
        conn, state_key=binding.state_key,
        state_generation=binding.state_generation, telemetry=telemetry)
    return TUQueryResult(
        disposition="bound", binding=binding, state=state, poison=None,
        provenance={**resolved.provenance(),
                    "binding_id": binding.binding_id,
                    "tu_context_id": binding.tu_context_id,
                    "canon_source_path": binding.canon_source_path,
                    "binding_status": binding.binding_status,
                    "disposition": binding.disposition,
                    "state_id": state.state_id,
                    "state_key": state.tusemantic_key,
                    "state_generation": state.parser_generation,
                    "publication": state.publication})


# ── Q2 (§9/§10): world-scoped symbol lookup ───────────────────────────────────

def lookup_symbol(conn: sqlite3.Connection, view: QueryView, name: str, *,
                  selector: TuSelector | None = None,
                  telemetry: QueryTelemetry | None = None
                  ) -> SymbolQueryResult:
    """Symbols visible ONLY through the selected world's bindings (§10).

    Traversal is World → TUBinding → SemanticState → state-local symbol
    table.  Raw USR is never a lookup key.  Multiple legitimate matches are
    returned as explicit multiple results (per-binding provenance), never
    collapsed.  Poisoned TUs are disclosed in ``degraded_tus`` — never
    silently treated as absence; a selector that names a poisoned TU raises
    :class:`TuPoisoned`.
    """
    if not name:
        raise IncompleteView("symbol name must be non-empty")
    resolved = resolve_query_view(conn, view, telemetry=telemetry)
    if selector is not None or view.tu_selector is not None:
        binding = _resolve_selector(conn, resolved,
                                    _pick_selector(view, selector),
                                    telemetry)
        if binding.binding_status == "poisoned" or \
                binding.disposition == "poisoned":
            info = _poison_info(conn, resolved, binding)
            if telemetry is not None:
                telemetry.poison_results += 1
            raise TuPoisoned(
                f"selected TU {binding.canon_source_path!r} is poisoned; "
                f"no shareable SemanticState exists",
                detail={"poison": info.as_dict(),
                        "tu_context_id": binding.tu_context_id,
                        "canon_source_path": binding.canon_source_path})
        targets = [binding]
    else:
        targets = [b for b in resolve_bindings(conn, resolved,
                                               telemetry=telemetry)
                   if b.binding_status == "bound"]
    # Poison disclosure over the WHOLE world is explicit, never silent (§4):
    degraded = []
    for b in resolve_bindings(conn, resolved, telemetry=telemetry):
        if b.binding_status == "poisoned" or b.disposition == "poisoned":
            info = _poison_info(conn, resolved, b)
            degraded.append({**info.as_dict(),
                             "binding_id": b.binding_id,
                             "tu_context_id": b.tu_context_id,
                             "canon_source_path": b.canon_source_path})
            if telemetry is not None:
                telemetry.poison_results += 1
    hits: list[SymbolHit] = []
    for binding in targets:
        if binding.state_key is None or binding.state_generation is None:
            raise StoreIntegrityError(
                "bound binding carries no state identity",
                detail={"binding_id": binding.binding_id})
        state = _load_published_state(
            conn, state_key=binding.state_key,
            state_generation=binding.state_generation, telemetry=telemetry)
        rows = conn.execute(
            "SELECT * FROM semantic_symbols_core WHERE state_id=? AND "
            "name=? ORDER BY local_key", (state.state_id, name)).fetchall()
        for r in rows:
            hits.append(SymbolHit(
                name=r["name"], raw_usr=r["raw_usr"], kind=r["kind"],
                is_definition=bool(r["is_definition"]),
                signature=r["signature"], local_key=int(r["local_key"]),
                state=state, binding=binding,
                provenance={**resolved.provenance(),
                            "binding_id": binding.binding_id,
                            "tu_context_id": binding.tu_context_id,
                            "canon_source_path": binding.canon_source_path,
                            "state_id": state.state_id,
                            "state_key": state.tusemantic_key,
                            "state_generation": state.parser_generation}))
    if not hits:
        if degraded:
            # Poison must not masquerade as absence (§14): return the
            # structured empty-with-degraded result instead of NOT_FOUND.
            return SymbolQueryResult(name=name, hits=[], degraded_tus=degraded,
                                     provenance=resolved.provenance())
        raise SymbolNotFound(
            f"symbol {name!r} not found in the resolved world's bound "
            f"states", detail={"name": name,
                               "world_id": resolved.world_id})
    return SymbolQueryResult(name=name, hits=hits, degraded_tus=degraded,
                             provenance=resolved.provenance())


# ── Q3 (§9/§11): state-local refs/calls ───────────────────────────────────────

def symbol_edges(conn: sqlite3.Connection, view: QueryView, *,
                 state_key: str, state_generation: int, local_key: int,
                 edge_class: str | None = None,
                 telemetry: QueryTelemetry | None = None) -> list[EdgeHit]:
    """Edges whose SOURCE endpoint belongs to the given state (task §11).

    The state must be reachable through the resolved world's bindings
    (addressing an unreachable state fails closed — §36).  Endpoint
    resolution stays state-local: ``to_symbol`` only ever names symbols of
    the SAME state; unresolved/cross-state targets keep their stored
    attribute legs (``to_raw_usr``/``to_name``) and a NULL ``to_symbol``.
    """
    resolved = resolve_query_view(conn, view, telemetry=telemetry)
    reachable = {
        (b.state_key, b.state_generation)
        for b in resolve_bindings(conn, resolved, telemetry=telemetry)
        if b.binding_status == "bound" and b.state_key is not None
        and b.state_generation is not None
    }
    if (state_key, state_generation) not in reachable:
        raise SymbolNotFound(
            "state is not reachable through this view's bindings "
            "(DB membership does not imply visibility)",
            detail={"tusemantic_key": state_key,
                    "parser_generation": state_generation,
                    "world_id": resolved.world_id})
    state = _load_published_state(conn, state_key=state_key,
                                  state_generation=state_generation,
                                  telemetry=telemetry)
    src = conn.execute(
        "SELECT * FROM semantic_symbols_core WHERE state_id=? AND "
        "local_key=?", (state.state_id, local_key)).fetchone()
    if src is None:
        raise SymbolNotFound(
            f"local_key {local_key} does not exist in the addressed state",
            detail={**state.identity(), "local_key": local_key})
    if edge_class is None:
        rows = conn.execute(
            "SELECT e.*, s2.name AS to_resolved_name, s2.raw_usr AS "
            "to_resolved_usr FROM semantic_edges e LEFT JOIN "
            "semantic_symbols_core s2 ON s2.state_id=e.state_id AND "
            "s2.local_key=e.to_local_key WHERE e.state_id=? AND e.local_key=? "
            "ORDER BY e.edge_ordinal", (state.state_id, local_key)).fetchall()
    else:
        rows = conn.execute(
            "SELECT e.*, s2.name AS to_resolved_name, s2.raw_usr AS "
            "to_resolved_usr FROM semantic_edges e LEFT JOIN "
            "semantic_symbols_core s2 ON s2.state_id=e.state_id AND "
            "s2.local_key=e.to_local_key WHERE e.state_id=? AND e.local_key=? "
            "AND e.edge_class=? ORDER BY e.edge_ordinal",
            (state.state_id, local_key, edge_class)).fetchall()
    hits: list[EdgeHit] = []
    for r in rows:
        to_local = (None if r["to_local_key"] is None
                    else int(r["to_local_key"]))
        to_sym = None
        if to_local is not None:
            to_sym = StateSymbolRef(tusemantic_key=state.tusemantic_key,
                                    parser_generation=state.parser_generation,
                                    local_key=to_local)
        hits.append(EdgeHit(
            edge_class=r["edge_class"], edge_ordinal=int(r["edge_ordinal"]),
            to_local_key=to_local, to_symbol=to_sym,
            to_resolved_name=r["to_resolved_name"],
            to_raw_usr=(r["to_resolved_usr"] or r["to_raw_usr"] or "") or None,
            to_name=r["to_name"], certainty=r["certainty"],
            resolution_kind=r["resolution_kind"],
            provenance={**resolved.provenance(),
                        "source_local_key": local_key,
                        "state_id": state.state_id,
                        "state_key": state.tusemantic_key,
                        "state_generation": state.parser_generation}))
    return hits


# ── Q4a: shared conditions + per-world branch states ──────────────────────────

def tu_conditions(conn: sqlite3.Connection, view: QueryView, *,
                  selector: TuSelector | None = None,
                  telemetry: QueryTelemetry | None = None
                  ) -> list[ConditionHit]:
    """Shared condition regions of the binding's state, each annotated with
    the BINDING's own Tier-B branch states (per-world; never shared)."""
    resolved = resolve_query_view(conn, view, telemetry=telemetry)
    binding = _resolve_selector(conn, resolved, _pick_selector(view, selector),
                                telemetry)
    if binding.binding_status == "poisoned" or \
            binding.disposition == "poisoned":
        info = _poison_info(conn, resolved, binding)
        if telemetry is not None:
            telemetry.poison_results += 1
        raise TuPoisoned(
            f"selected TU {binding.canon_source_path!r} is poisoned",
            detail={"poison": info.as_dict(),
                    "tu_context_id": binding.tu_context_id})
    if binding.state_key is None or binding.state_generation is None:
        raise StoreIntegrityError("bound binding carries no state identity",
                                  detail={"binding_id": binding.binding_id})
    state = _load_published_state(conn, state_key=binding.state_key,
                                  state_generation=binding.state_generation,
                                  telemetry=telemetry)
    branches: dict[int, list[dict]] = {}
    for r in conn.execute(
            "SELECT region_ordinal, branch_ordinal, taken FROM "
            "tu_branch_states WHERE binding_id=? ORDER BY region_ordinal, "
            "branch_ordinal", (binding.binding_id,)).fetchall():
        branches.setdefault(int(r["region_ordinal"]), []).append(
            {"branch_ordinal": int(r["branch_ordinal"]),
             "taken": int(r["taken"])})
    out: list[ConditionHit] = []
    for r in conn.execute(
            "SELECT region_ordinal, parent_region_ordinal, directive, "
            "condition, file_blob_sha FROM semantic_condition_index WHERE "
            "state_id=? ORDER BY region_ordinal", (state.state_id,)):
        out.append(ConditionHit(
            region_ordinal=int(r["region_ordinal"]),
            parent_region_ordinal=(None if r["parent_region_ordinal"] is None
                                   else int(r["parent_region_ordinal"])),
            directive=r["directive"], condition=r["condition"],
            file_blob_sha=r["file_blob_sha"],
            branch_states=branches.get(int(r["region_ordinal"]), []),
            provenance={**resolved.provenance(),
                        "binding_id": binding.binding_id,
                        "tu_context_id": binding.tu_context_id,
                        "state_id": state.state_id,
                        "state_key": state.tusemantic_key}))
    return out


# ── Q4b: shared facts + per-world presentation composition (§12) ──────────────

def tu_closure_presentation(conn: sqlite3.Connection, view: QueryView, *,
                            selector: TuSelector | None = None,
                            telemetry: QueryTelemetry | None = None
                            ) -> TUClosureView:
    """Compose the shared state file table with the BINDING's per-world
    file map (A-4) and certificate rows.  Canon paths are project-relative
    (INV-4): no absolute clone-root path can leak into results."""
    resolved = resolve_query_view(conn, view, telemetry=telemetry)
    binding = _resolve_selector(conn, resolved, _pick_selector(view, selector),
                                telemetry)
    if binding.binding_status == "poisoned" or \
            binding.disposition == "poisoned":
        info = _poison_info(conn, resolved, binding)
        if telemetry is not None:
            telemetry.poison_results += 1
        raise TuPoisoned(
            f"selected TU {binding.canon_source_path!r} is poisoned",
            detail={"poison": info.as_dict(),
                    "tu_context_id": binding.tu_context_id})
    if binding.state_key is None or binding.state_generation is None:
        raise StoreIntegrityError("bound binding carries no state identity",
                                  detail={"binding_id": binding.binding_id})
    state = _load_published_state(conn, state_key=binding.state_key,
                                  state_generation=binding.state_generation,
                                  telemetry=telemetry)
    shared_files = {
        int(r["file_ordinal"]): (r["role"], r["blob_sha"])
        for r in conn.execute(
            "SELECT file_ordinal, role, blob_sha FROM state_file_table WHERE "
            "state_id=? ORDER BY file_ordinal", (state.state_id,))
    }
    per_world: dict[int, str | None] = {}
    for r in conn.execute(
            "SELECT m.file_ordinal, p.path AS canon_path FROM "
            "binding_file_map m LEFT JOIN canon_paths p ON "
            "p.path_id=m.canon_path_id WHERE m.binding_id=? "
            "ORDER BY m.file_ordinal", (binding.binding_id,)):
        per_world[int(r["file_ordinal"])] = r["canon_path"]
    files = [
        PresentationRow(file_ordinal=ordinal, role=shared_files[ordinal][0],
                        blob_sha=shared_files[ordinal][1],
                        canon_path=per_world.get(ordinal))
        for ordinal in sorted(shared_files)
    ]
    directives = [
        {"directive_ordinal": int(r["directive_ordinal"]),
         "include_spelling": r["include_spelling"], "role": r["role"],
         "rnf": bool(r["rnf"]), "resolved_canon_path": r["resolved_canon_path"]}
        for r in conn.execute(
            "SELECT cr.directive_ordinal, cr.include_spelling, cr.role, "
            "cr.rnf, p.path AS resolved_canon_path FROM cert_rows cr LEFT "
            "JOIN canon_paths p ON p.path_id=cr.resolved_path_id WHERE "
            "cr.binding_id=? ORDER BY cr.directive_ordinal",
            (binding.binding_id,))
    ]
    detail = conn.execute(
        "SELECT ncc_serial, rsp_record, presentation_context FROM "
        "tu_context_details WHERE binding_id=?",
        (binding.binding_id,)).fetchone()
    return TUClosureView(
        state=state, binding=binding, files=files, cert_directives=directives,
        ncc_serial=(detail["ncc_serial"] if detail is not None else None),
        rsp_record=(detail["rsp_record"] if detail is not None else None),
        provenance={**resolved.provenance(),
                    "binding_id": binding.binding_id,
                    "tu_context_id": binding.tu_context_id,
                    "canon_source_path": binding.canon_source_path,
                    "state_id": state.state_id,
                    "state_key": state.tusemantic_key,
                    "state_generation": state.parser_generation,
                    "publication": state.publication})


# ── internal diagnostic (§8: clearly separate from world-semantic queries) ────

def diagnose_world(conn: sqlite3.Connection, view: QueryView) -> dict:
    """Raw storage shape of the resolved world (diagnostic ONLY).

    This is NOT a world-semantic query: it reports storage rows (including
    nonpublished states and world-level poison rows) for operator
    inspection, clearly named and kept out of the semantic result path.
    """
    resolved = resolve_query_view(conn, view)
    rows = conn.execute(
        "SELECT binding_status, disposition, COUNT(*) AS n FROM tu_bindings "
        "WHERE world_id=? GROUP BY binding_status, disposition",
        (resolved.world_id,)).fetchall()
    return {
        "world": resolved.provenance(),
        "bindings": [{k: r[k] for k in r.keys()} for r in rows],
        "poisons": [{k: r[k] for k in r.keys()} for r in conn.execute(
            "SELECT component, poison_tag, nonce, detected_at FROM "
            "world_poisons WHERE world_id=?", (resolved.world_id,))],
        "distinct_bound_states": int(conn.execute(
            "SELECT COUNT(DISTINCT state_key || ':' || state_generation) "
            "FROM tu_bindings WHERE world_id=? AND binding_status='bound'",
            (resolved.world_id,)).fetchone()[0]),
    }
