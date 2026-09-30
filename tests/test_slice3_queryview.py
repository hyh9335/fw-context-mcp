"""test_slice3_queryview — S3.1–S3.3/S3.8 store-level oracles.

QueryView construction-time validation, strict resolution (exactly one
world), the world-status gate, the read-time publication gate, ambiguity
fail-closed, and the I1–I4 corruption/integrity battery.  Every read uses a
``mode=ro`` connection through ``store_v2.query``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice3_helpers import (  # noqa: E402
    bind,
    make_query_env,
    open_q,
)
from slice2_helpers import v2_rows  # noqa: E402

from fw_context_mcp.store_v2 import query, worlds  # noqa: E402
from fw_context_mcp.store_v2.query import (  # noqa: E402
    AmbiguousTarget,
    IncompleteView,
    QueryView,
    SemanticStateNotPublished,
    StoreIntegrityError,
    SymbolNotFound,
    TuNotInWorld,
    TuSelector,
    WorldNotQueryable,
    WorldNotFound,
    resolve_query_view,
)

# fw_context_mcp redirects sys.modules['sqlite3'] -> pysqlite3 at import time
# (same posture as test_slice1_publication): both exception hierarchies can
# appear depending on which connection raised, so catch the tuple.
import pysqlite3  # noqa: E402
SQLITE_INTEGRITY_ERROR = (pysqlite3.dbapi2.IntegrityError,
                          sqlite3.IntegrityError)
SQLITE_OPERATIONAL_ERROR = (pysqlite3.dbapi2.OperationalError,
                            sqlite3.OperationalError)
STAGE_OF = {"repository_namespace": "namespace", "snapshot_id": "snapshot",
            "build_id": "build"}
MAIN = query.TuSelector(canon_source_path="src/main.c")


@pytest.fixture()
def env(tmp_path):
    e = make_query_env(tmp_path)
    yield e
    e["store"].close()
    e["conn"].close()


# ── S3.1: QueryView value object ──────────────────────────────────────────────

def test_s3_view_construction_validation():
    """§16: partial views are invalid at CONSTRUCTION time (INCOMPLETE_VIEW);
    tu_selector admits exactly one form."""
    with pytest.raises(IncompleteView):
        QueryView(repository_namespace="", snapshot_id="s", build_id="b")
    with pytest.raises(IncompleteView):
        QueryView(repository_namespace="r:x", snapshot_id="", build_id="b")
    with pytest.raises(IncompleteView):
        QueryView(repository_namespace="r:x", snapshot_id="s", build_id="")
    with pytest.raises(IncompleteView):
        TuSelector()                       # neither form
    with pytest.raises(IncompleteView):
        TuSelector(tu_context_id="cx", canon_source_path="src/main.c")
    sel = TuSelector(canon_source_path="src/main.c")
    v = QueryView(repository_namespace="r:x", snapshot_id="s",
                  build_id="b", tu_selector=sel)
    assert v.tu_selector is sel
    with pytest.raises(Exception):         # frozen: immutable value object
        v.snapshot_id = "other"            # type: ignore[misc]
    d1 = v.to_diag()
    assert d1 == v.to_diag() and "r:" in d1 and "src/main.c" in d1


def test_s3_view_is_not_cwd_or_handle_derived(env):
    """§2/§6: the view carries only explicit identities — no CWD, no legacy
    config_hash, no INTEGER storage handles as selectors."""
    view = env["view"]
    assert view.repository_namespace.startswith(("r:", "t:"))
    assert len(view.snapshot_id) == 64 and len(view.build_id) == 64
    assert view.build_id == env["ctx"].build_identity_sha


# ── S3-T01: exact view resolves exactly one world ─────────────────────────────

def test_s3_t01_exact_view_resolves_one_world(env):
    conn, view = env["conn"], env["view"]
    tel = query.QueryTelemetry()
    r = resolve_query_view(conn, view, telemetry=tel)
    assert r.world_id == env["ctx"].world_id
    assert r.namespace_id == view.repository_namespace
    assert r.snapshot_id == view.snapshot_id
    assert r.build_id == view.build_id
    assert r.world_status == "active"
    assert r.run_provenance                # non-empty provenance required
    assert tel.queryview_resolution_count == 1
    assert tel.queryview_resolution_failures == 0
    assert tel.worlds_considered == 1
    assert tel.last_resolution_ms is not None
    assert r.provenance()["world_id"] == r.world_id


# ── S3-T02: missing world → explicit NOT_FOUND ────────────────────────────────

def test_s3_t02_missing_world_not_found(env):
    conn = env["conn"]
    base = env["view"]
    for field, bad in (("snapshot_id", "0" * 64),
                       ("build_id", "0" * 64),
                       ("repository_namespace", "r:" + "0" * 64)):
        kwargs = {"repository_namespace": base.repository_namespace,
                  "snapshot_id": base.snapshot_id, "build_id": base.build_id}
        kwargs[field] = bad
        view = QueryView(**kwargs)
        with pytest.raises(WorldNotFound) as ei:
            resolve_query_view(conn, view)
        assert ei.value.code == "WORLD_NOT_FOUND"
        assert ei.value.detail["stage"] == STAGE_OF[field]
    # a query through a missing world names the error, never returns data
    with pytest.raises(WorldNotFound):
        query.inspect_tu(conn, QueryView(
            repository_namespace=base.repository_namespace,
            snapshot_id="0" * 64, build_id=base.build_id,
            tu_selector=TuSelector(canon_source_path="src/main.c")))


# ── S3-T03: ambiguity fails closed, never newest-wins ─────────────────────────

def test_s3_t03_ambiguous_path_selector_fails_closed(env):
    """Two compile contexts of one world share a canon path: the path
    selector is AMBIGUOUS with enumerated candidates; the exact
    tu_context_id selector resolves.  No automatic pick."""
    conn, view, ctx, cert = env["conn"], env["view"], env["ctx"], env["cert"]
    r2 = bind(env, tu="cx2", canon="src/main.c")
    assert r2.status == "bound_new"
    worlds.finalize_world(env["store"], ctx)
    sel_path = TuSelector(canon_source_path="src/main.c")
    with pytest.raises(AmbiguousTarget) as ei:
        query.inspect_tu(conn, view, selector=sel_path)
    assert ei.value.code == "AMBIGUOUS_TARGET"
    got = sorted(c["tu_context_id"] for c in ei.value.candidates)
    assert got == ["cx", "cx2"]            # enumerated, not picked
    # explicit disambiguation by the opaque context id
    res = query.inspect_tu(conn, view,
                           selector=TuSelector(tu_context_id="cx2"))
    assert res.disposition == "bound"
    assert res.binding.tu_context_id == "cx2"
    assert res.binding.binding_id != query.inspect_tu(
        conn, view, selector=TuSelector(tu_context_id="cx")).binding.binding_id


def test_s3_t03b_world_triple_uniqueness_structurally_blocks_ambiguity(env):
    """I4: the frozen UNIQUE(namespace, snapshot, build) constraint refuses a
    duplicate world row — the resolver's >1 branch is defense in depth, and
    the store cannot be made ambiguous without weakening constraints."""
    conn = env["store"]._conn
    w = v2_rows(env["store"].path, "SELECT * FROM snapshot_builds")[0]
    with pytest.raises(SQLITE_INTEGRITY_ERROR) as ei:
        conn.execute(
            "INSERT INTO snapshot_builds(namespace_id, snapshot_id, "
            "build_identity_sha, world_status, run_provenance) "
            "VALUES(?,?,?,?,?)",
            (w["namespace_id"], w["snapshot_id"], w["build_identity_sha"],
             "active", w["run_provenance"]))
        env["store"]._conn.commit()
    assert "UNIQUE constraint failed" in str(ei.value)


# ── S3-T04/I3: world-status gate ──────────────────────────────────────────────

def test_s3_t04_incomplete_world_rejected(env):
    conn, view, store = env["conn"], env["view"], env["store"]
    store._conn.execute(
        "UPDATE snapshot_builds SET world_status='unfinalized' "
        "WHERE world_id=?", (env["ctx"].world_id,))
    store._conn.commit()
    with pytest.raises(WorldNotQueryable) as ei:
        resolve_query_view(conn, view)
    assert ei.value.detail["world_status"] == "unfinalized"
    # lifecycle vocabulary: retired/superseded never serve queries either
    store._conn.execute(
        "UPDATE snapshot_builds SET world_status='retired' WHERE world_id=?",
        (env["ctx"].world_id,))
    store._conn.commit()
    with pytest.raises(WorldNotQueryable):
        resolve_query_view(conn, view)


def test_s3_t04b_missing_run_provenance_rejected(env):
    conn, view, store = env["conn"], env["view"], env["store"]
    store._conn.execute(
        "UPDATE snapshot_builds SET run_provenance='' WHERE world_id=?",
        (env["ctx"].world_id,))
    store._conn.commit()
    with pytest.raises(WorldNotQueryable):
        resolve_query_view(conn, view)


# ── S3-T05/I1: read-time publication gate (defense in depth) ──────────────────

def test_s3_t05_binding_to_nonpublished_state_rejected(env):
    """Manually demote the published state to 'staging': the Slice-2 insert
    gate cannot be relied on at read time — the query fails closed."""
    conn, store = env["conn"], env["store"]
    store._conn.execute(
        "UPDATE semantic_states SET publication='staging' WHERE "
        "tusemantic_key=? AND parser_generation=?", (env["key"], env["gen"]))
    store._conn.commit()
    with pytest.raises(SemanticStateNotPublished) as ei:
        query.inspect_tu(conn, env["view"], selector=MAIN)
    assert ei.value.code == "SEMANTIC_STATE_NOT_PUBLISHED"
    assert ei.value.detail["publication"] == "staging"
    with pytest.raises(SemanticStateNotPublished):
        query.lookup_symbol(conn, env["view"], "main")
    with pytest.raises(SemanticStateNotPublished):
        query.tu_closure_presentation(conn, env["view"], selector=MAIN)


# ── I2: binding → absent state (constraint demonstration + read gate) ─────────

def test_s3_i2_absent_state_fails_closed(env):
    store, conn = env["store"], env["conn"]
    bid = v2_rows(store.path, "SELECT binding_id FROM tu_bindings")[0]\
        ["binding_id"]
    # (a) with FK enforcement ON (the store's own connections), the
    # corruption is refused outright — constraint demonstrated:
    with pytest.raises(SQLITE_INTEGRITY_ERROR):
        try:
            store._conn.execute(
                "UPDATE tu_bindings SET state_key='no-such-key' WHERE "
                "binding_id=?", (bid,))
            store._conn.commit()
        finally:
            store._conn.rollback()   # release the lock the failed stmt held
    # (b) a foreign connection with FKs off CAN corrupt; the strict read
    # must then fail closed as INTEGRITY_ERROR:
    raw = sqlite3.connect(str(store.path))
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("UPDATE tu_bindings SET state_key='no-such-key' WHERE "
                    "binding_id=?", (bid,))
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(StoreIntegrityError) as ei:
        query.inspect_tu(conn, env["view"], selector=MAIN)
    assert ei.value.code == "INTEGRITY_ERROR"
    assert ei.value.detail["tusemantic_key"] == "no-such-key"


# ── selector requirements on TU-scoped queries ────────────────────────────────

def test_s3_tu_scoped_queries_require_a_selector(env):
    conn = env["conn"]
    view = QueryView(repository_namespace=env["view"].repository_namespace,
                     snapshot_id=env["view"].snapshot_id,
                     build_id=env["view"].build_id)      # no tu_selector
    for fn in (query.inspect_tu, query.tu_conditions,
               query.tu_closure_presentation):
        with pytest.raises(IncompleteView):
            fn(conn, view)
    # a view-level selector satisfies the requirement
    view_sel = QueryView(repository_namespace=view.repository_namespace,
                         snapshot_id=view.snapshot_id,
                         build_id=view.build_id,
                         tu_selector=TuSelector(canon_source_path="src/main.c"))
    res = query.inspect_tu(conn, view_sel)
    assert res.disposition == "bound"


def test_s3_tu_not_in_world_distinct_codes(env):
    conn, view = env["conn"], env["view"]
    with pytest.raises(TuNotInWorld) as ei:
        query.inspect_tu(conn, view, selector=TuSelector(
            canon_source_path="src/nope.c"))
    assert ei.value.code == "TU_NOT_IN_WORLD"
    with pytest.raises(TuNotInWorld):
        query.inspect_tu(conn, view, selector=TuSelector(
            tu_context_id="cx-absent"))


def test_s3_symbol_not_found_distinct(env):
    conn, view = env["conn"], env["view"]
    with pytest.raises(SymbolNotFound) as ei:
        query.lookup_symbol(conn, view, "definitely_not_there")
    assert ei.value.code == "SYMBOL_NOT_FOUND"


def test_s3_missing_store_query_cannot_seed_or_create(tmp_path):
    """§24 posture: a query against a nonexistent v2 store fails cleanly and
    CREATES NOTHING (no file, no state probe)."""
    v2 = tmp_path / "absent" / "index-v2.db"
    with pytest.raises(SQLITE_OPERATIONAL_ERROR) as ei:
        query.open_query_connection(v2)
    assert "unable to open" in str(ei.value)
    assert not v2.exists()
    assert not v2.parent.exists() or not any(v2.parent.iterdir())


def test_s3_index_creation_is_write_path_only(env):
    """§26: the two additive Slice-3 indexes exist after the write path
    created them; ensure_slice3_schema is never called by read functions."""
    rows = v2_rows(env["store"].path,
                   "SELECT name FROM sqlite_master WHERE type='index' "
                   "AND name LIKE 'idx_%'")
    names = {r["name"] for r in rows}
    assert {"idx_symbols_core_state_name",
            "idx_tu_bindings_world_path"} <= names
    src = Path(query.__file__).read_text(encoding="utf-8")
    body = src.split("def ensure_slice3_schema", 1)[1]
    assert "INSERT" not in body and "UPDATE" not in body
