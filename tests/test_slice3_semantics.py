"""test_slice3_semantics — S3.7/S3.8 + §13/§14/§20/§31 oracles.

Poison vs absence (T13/T14), real-rename presentation remap (T15), zero
writes across every query surface (T16), provenance envelopes (T20),
read-committed concurrency (§31), and the Q4 shared/per-world composition.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice2_helpers import make_git_project  # noqa: E402
from slice3_helpers import (  # noqa: E402
    init_project_id,
    make_poison_env,
    make_query_env,
    open_q,
    run_index,
    store_row_snapshot,
    v2_of,
    view_from_world_row,
)

from fw_context_mcp.store_v2 import query, worlds  # noqa: E402

MAIN = query.TuSelector(canon_source_path="src/main.c")
GHOST = query.TuSelector(canon_source_path="src/ghost.c")


def sel(path: str) -> query.TuSelector:
    return query.TuSelector(canon_source_path=path)


@pytest.fixture()
def env(tmp_path):
    e = make_query_env(tmp_path / "env")
    yield e
    e["store"].close()
    e["conn"].close()


@pytest.fixture()
def poison_env(tmp_path):
    e = make_poison_env(tmp_path / "poison")
    yield e
    e["store"].close()
    e["conn"].close()


# ── §14 / T13: poison is an explicit structured degraded result ───────────────

def test_s3_t13_poisoned_tu_explicit_degraded_result(poison_env):
    conn, view = poison_env["conn"], poison_env["view"]
    tel = query.QueryTelemetry()
    res = query.inspect_tu(conn, view, selector=GHOST, telemetry=tel)
    assert res.disposition == "poisoned"
    assert res.state is None                      # INV-6: no fake state
    assert res.poison is not None
    assert res.poison.poison_tag == "unresolved-include"
    assert res.poison.reason and "ghost.h" in res.poison.reason
    assert res.poison.component.startswith("tu:cx-poison:")
    assert res.binding.binding_status == "poisoned"
    assert res.provenance["world_id"] == poison_env["ctx"].world_id
    assert tel.poison_results == 1
    # the bound sibling stays fully queryable in the SAME world
    ok = query.inspect_tu(conn, view, selector=MAIN)
    assert ok.disposition == "bound" and ok.state is not None


def test_s3_t14_absent_vs_poisoned_distinct(poison_env):
    conn, view = poison_env["conn"], poison_env["view"]
    # poisoned path → explicit degraded result …
    res = query.inspect_tu(conn, view, selector=GHOST)
    assert res.disposition == "poisoned"
    # …an unknown path → TU_NOT_IN_WORLD, a DIFFERENT outcome
    with pytest.raises(query.TuNotInWorld):
        query.inspect_tu(conn, view, selector=sel("src/nope.c"))
    # a selector naming the poisoned TU poisons symbol lookups explicitly
    with pytest.raises(query.TuPoisoned) as ei:
        query.lookup_symbol(conn, view, "util", selector=GHOST)
    assert ei.value.code == "TU_POISONED"
    assert ei.value.detail["poison"]["poison_tag"] == "unresolved-include"


def test_s3_t14b_poison_never_silently_absent(poison_env):
    """World-level symbol lookup DISCLOSES the poisoned TU instead of
    pretending it does not exist (§4: poison is never absence)."""
    conn, view = poison_env["conn"], poison_env["view"]
    res = query.lookup_symbol(conn, view, "util")
    assert res.hits                               # from the bound TU's state
    assert len(res.degraded_tus) == 1
    d = res.degraded_tus[0]
    assert d["canon_source_path"] == "src/ghost.c"
    assert d["poison_tag"] == "unresolved-include"
    # a name that exists ONLY in a poisoned world member → structured
    # empty-with-degraded, never a bare NOT_FOUND
    empty = query.lookup_symbol(conn, view, "ghost_main")
    assert empty.hits == [] and len(empty.degraded_tus) == 1


# ── §13 / T15: same shared state, per-world presentation remap ────────────────

RENAME_MAIN = "renamed_main.c"


def test_s3_t15_rename_presentation_remap(tmp_path):
    """Real-hook rename: identical bytes under a new path — the state key
    EXCLUDES cert paths (A-1), so both worlds share ONE SemanticState while
    every result is presented under its own world's canonical path (A-4)."""
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    old = os.environ.get("FW_CONTEXT_SHADOW_V2")
    old_auth = os.environ.get("FW_CONTEXT_V2_WRITE_AUTHORITY")
    old_m6 = os.environ.get("FW_CONTEXT_M6_WRITE_AUTHORITY")
    os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    # Authority island: the rename scenario queries BOTH runner-produced
    # worlds; the runner finalizes them only when v2 write authority is on.
    os.environ["FW_CONTEXT_V2_WRITE_AUTHORITY"] = "1"
    os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = "0"
    _UP._reset_v2_flag_cache()
    _UP._reset_v2_write_authority_cache()
    C.reset_flag_cache()
    try:
        data = make_git_project(tmp_path)
        proj: Path = data["proj"]
        init_project_id(proj)
        _, db = run_index(proj, tmp_path, name="index")
        v2 = v2_of(db)
        (proj / "src" / "main.c").rename(proj / "src" / RENAME_MAIN)
        cc = proj / "compile_commands.json"
        entries = json.loads(cc.read_text())
        for e in entries:
            e["file"] = str(proj / "src" / RENAME_MAIN)
            e["arguments"] = [RENAME_MAIN_TOKEN(a) for a in e["arguments"]]
        cc.write_text(json.dumps(entries))
        run_index(proj, tmp_path, name="index")
    finally:
        if old is None:
            os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
        else:
            os.environ["FW_CONTEXT_SHADOW_V2"] = old
        if old_auth is None:
            os.environ.pop("FW_CONTEXT_V2_WRITE_AUTHORITY", None)
        else:
            os.environ["FW_CONTEXT_V2_WRITE_AUTHORITY"] = old_auth
        if old_m6 is None:
            os.environ.pop("FW_CONTEXT_M6_WRITE_AUTHORITY", None)
        else:
            os.environ["FW_CONTEXT_M6_WRITE_AUTHORITY"] = old_m6
        _UP._reset_v2_flag_cache()
        _UP._reset_v2_write_authority_cache()
        C.reset_flag_cache()
    conn = open_q(v2)
    try:
        rows = conn.execute("SELECT world_id FROM snapshot_builds "
                            "ORDER BY world_id").fetchall()
        assert len(rows) == 2
        va = view_from_world_row(v2, int(rows[0]["world_id"]))
        vb = view_from_world_row(v2, int(rows[1]["world_id"]))
        ra = query.inspect_tu(conn, va, selector=sel("src/main.c"))
        rb = query.inspect_tu(conn, vb, selector=sel("src/" + RENAME_MAIN))
        assert ra.disposition == "bound" and rb.disposition == "bound"
        # ONE shared semantic state across the rename …
        assert ra.state.tusemantic_key == rb.state.tusemantic_key
        assert ra.state.state_id == rb.state.state_id
        assert ra.state.completeness_sha == rb.state.completeness_sha
        # …with per-world canonical-path presentation
        assert ra.binding.canon_source_path == "src/main.c"
        assert rb.binding.canon_source_path == "src/" + RENAME_MAIN
        pa = query.tu_closure_presentation(conn, va, selector=sel("src/main.c"))
        pb = query.tu_closure_presentation(
            conn, vb, selector=sel("src/" + RENAME_MAIN))
        assert {f.blob_sha for f in pa.files} == {f.blob_sha for f in pb.files}
        # the SHARED closure presents identically (same header canon paths);
        # the DEFINING file's per-world path lives on the binding record
        paths_a = {f.canon_path for f in pa.files}
        paths_b = {f.canon_path for f in pb.files}
        assert paths_a == paths_b                  # shared closure remap
        assert ra.binding.canon_source_path == "src/main.c"
        assert rb.binding.canon_source_path == "src/" + RENAME_MAIN
        # symbol content identical, defining presentation differs per world
        ha = query.lookup_symbol(conn, va, "main")
        hb = query.lookup_symbol(conn, vb, "main")
        assert ha.hits[0].raw_usr == hb.hits[0].raw_usr
        assert ha.hits[0].state.state_id == hb.hits[0].state.state_id
        assert ha.hits[0].provenance["canon_source_path"] == "src/main.c"
        assert hb.hits[0].provenance["canon_source_path"] == \
            "src/" + RENAME_MAIN
    finally:
        conn.close()


def RENAME_MAIN_TOKEN(a: str) -> str:
    return "src/" + RENAME_MAIN if a == "src/main.c" else a


# ── §20 / T16: the read path writes NOTHING ───────────────────────────────────

def test_s3_t16_zero_writes_and_ro_enforced(env):
    conn, store, view = env["conn"], env["store"], env["view"]
    before = store_row_snapshot(conn)
    tel = query.QueryTelemetry()
    resolve_query = query.resolve_query_view(conn, view, telemetry=tel)
    assert resolve_query.world_id
    query.inspect_tu(conn, view, selector=MAIN, telemetry=tel)
    query.lookup_symbol(conn, view, "util", telemetry=tel)
    query.lookup_symbol(conn, view, "main", telemetry=tel)
    query.symbol_edges(conn, view, state_key=env["key"],
                       state_generation=env["gen"], local_key=1,
                       telemetry=tel)
    query.tu_conditions(conn, view, selector=MAIN, telemetry=tel)
    query.tu_closure_presentation(conn, view, selector=MAIN, telemetry=tel)
    query.diagnose_world(conn, view)
    after = store_row_snapshot(conn)
    assert before == after                       # byte-identical row sets
    # every query op re-resolves the view (strict pipeline, no caching) —
    # 7 operations above, all resolved, none failed
    assert tel.queryview_resolution_count == 7
    assert tel.queryview_resolution_failures == 0
    # the connection itself refuses writes (mode=ro, structural)
    with pytest.raises(Exception) as ei:
        conn.execute("INSERT INTO store_metadata(key, value) VALUES('x','y')")
    assert "readonly" in str(ei.value).lower()


# ── T20: provenance envelope completeness ─────────────────────────────────────

def test_s3_t20_provenance_envelopes(env, poison_env):
    conn, view = env["conn"], env["view"]
    res = query.inspect_tu(conn, view, selector=MAIN)
    p = res.provenance
    for k in ("view", "namespace_id", "snapshot_id", "build_id", "world_id",
              "world_status", "binding_id", "tu_context_id",
              "canon_source_path", "state_id", "state_key",
              "state_generation", "publication"):
        assert k in p, k
    assert p["world_status"] == "active"
    assert p["publication"] == "published"
    # canon paths are project-relative — no clone-root leak (INV-4)
    assert not Path(p["canon_source_path"]).is_absolute()
    pres = query.tu_closure_presentation(conn, view, selector=MAIN)
    for f in pres.files:
        assert f.canon_path is None or not Path(f.canon_path).is_absolute()
    # poisoned results carry full poison provenance
    pres2 = query.inspect_tu(poison_env["conn"], poison_env["view"],
                             selector=GHOST)
    assert pres2.poison.component and pres2.poison.poison_tag
    assert pres2.poison.reason


# ── §31: read-committed isolation for concurrent readers ──────────────────────

def test_s3_concurrent_reader_isolation(env):
    conn, store, view = env["conn"], env["store"], env["view"]
    r2 = open_q(store.path)
    try:
        # two read-only readers operate concurrently
        assert query.resolve_query_view(conn, view).world_id
        assert query.resolve_query_view(r2, view).world_id
        # (a) an UNCOMMITTED staged state is never partially visible
        store._conn.execute("BEGIN IMMEDIATE")
        store._conn.execute(
            "INSERT INTO semantic_states(tusemantic_key, parser_generation, "
            "key_format, l0_source_blob_sha, l1_ncc_digest_sha, "
            "l2_closure_digest_sha, l3_env_digest_sha, publication, "
            "completeness_sha, digest_algorithm, fact_row_count, "
            "fact_schema_version, payload_format, payload_ref, "
            "payload_digest_sha, owner_lease, path_spelling_sensitive, "
            "created_at, created_by_run) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?,?,?,?,?,?)",
            ("stg:half", env["gen"], "kb-c/v1", "0" * 64, "1" * 64,
             "2" * 64, "3" * 64, "staging", "d" * 64, "fwctx-fsd/v1+sha256",
             0, 1, "normalized", None, "e" * 64, None, 0, "now", "test"))
        store._conn.rollback()                   # never committed
        res = query.lookup_symbol(conn, view, "util")
        assert all(h.state.publication == "published" for h in res.hits)
        # (b) a COMMITTED-but-unbound staging state stays invisible too
        store._conn.execute(
            "INSERT INTO semantic_states(tusemantic_key, parser_generation, "
            "key_format, l0_source_blob_sha, l1_ncc_digest_sha, "
            "l2_closure_digest_sha, l3_env_digest_sha, publication, "
            "completeness_sha, digest_algorithm, fact_row_count, "
            "fact_schema_version, payload_format, payload_ref, "
            "payload_digest_sha, owner_lease, path_spelling_sensitive, "
            "created_at, created_by_run) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?,?,?,?,?,?)",
            ("stg:visible", env["gen"], "kb-c/v1", "4" * 64, "5" * 64,
             "6" * 64, "7" * 64, "staging", "f" * 64, "fwctx-fsd/v1+sha256",
             0, 1, "normalized", None, "0" * 64, None, 0, "now", "test"))
        store._conn.commit()
        res = query.lookup_symbol(r2, view, "util")
        assert all(h.state.tusemantic_key != "stg:visible" for h in res.hits)
        assert all(h.state.publication == "published" for h in res.hits)
        # (c) an unfinalized world committed mid-population is rejected
        store._conn.execute(
            "INSERT INTO snapshot_builds(namespace_id, snapshot_id, "
            "build_identity_sha, world_status, run_provenance) "
            "VALUES(?,?,?,?,?)",
            (view.repository_namespace, "f" * 64, env["ctx"].build_identity_sha,
             "unfinalized", "x"))
        store._conn.commit()
        with pytest.raises(query.WorldNotFound):
            query.resolve_query_view(r2, query.QueryView(
                repository_namespace=view.repository_namespace,
                snapshot_id="f" * 64, build_id=view.build_id))
    finally:
        r2.close()


# ── Q4: shared conditions + per-world presentation composition ────────────────

def test_s3_q4_conditions_and_closure_composition(env):
    conn, view, store = env["conn"], env["view"], env["store"]
    st = query.inspect_tu(conn, view, selector=MAIN)
    conds = query.tu_conditions(conn, view, selector=MAIN)
    db_conds = store._conn.execute(
        "SELECT region_ordinal, directive, condition, file_blob_sha FROM "
        "semantic_condition_index WHERE state_id=? ORDER BY region_ordinal",
        (st.state.state_id,)).fetchall()
    assert len(conds) == len(db_conds)
    # Tier-B: the BINDING's own branch states (per-world, never shared) —
    # the query surface must answer exactly the rows the store holds.
    db_branches: dict[int, list[dict]] = {}
    for r in store._conn.execute(
            "SELECT region_ordinal, branch_ordinal, taken FROM "
            "tu_branch_states WHERE binding_id=? "
            "ORDER BY region_ordinal, branch_ordinal",
            (st.binding.binding_id,)).fetchall():
        db_branches.setdefault(int(r["region_ordinal"]), []).append(
            {"branch_ordinal": int(r["branch_ordinal"]),
             "taken": int(r["taken"])})
    for hit, row in zip(conds, db_conds):
        assert hit.region_ordinal == row["region_ordinal"]
        assert hit.directive == row["directive"]
        assert hit.condition == row["condition"]
        assert hit.branch_states == db_branches.get(hit.region_ordinal, [])
    pres = query.tu_closure_presentation(conn, view, selector=MAIN)
    assert pres.state.state_id == st.state.state_id
    assert pres.binding.canon_source_path == "src/main.c"
    assert pres.files                     # closure file rows present
    for f in pres.files:
        assert f.canon_path is None or not Path(f.canon_path).is_absolute()
    assert pres.ncc_serial                # per-binding NCC record present
    for d in pres.cert_directives:
        assert d["include_spelling"]
        # resolved includes point at project-relative canon paths
        if d["resolved_canon_path"] is not None:
            assert not Path(d["resolved_canon_path"]).is_absolute()


def test_s3_diagnostic_clearly_separate(poison_env):
    """diagnose_world reports raw storage shape (poisons included) but is
    namespaced apart from the semantic query surface."""
    conn, view = poison_env["conn"], poison_env["view"]
    diag = query.diagnose_world(conn, view)
    kinds = {b["binding_status"]: b["n"] for b in diag["bindings"]}
    assert kinds == {"bound": 1, "poisoned": 1}
    assert len(diag["poisons"]) == 1
    assert diag["distinct_bound_states"] == 1
