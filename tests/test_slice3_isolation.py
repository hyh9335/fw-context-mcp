"""test_slice3_isolation — S3.4–S3.6 + §17/§18/§19/§36 oracles.

Snapshot isolation (T06/T07), build isolation with distinct states on the
same snapshot (T08/T09), shared-state two-world queries with per-binding
provenance (T10), USR-alias containment (T11), state-local edge ownership
(T12), and DB-membership-negative visibility (§36).  The central scenario
(§35) runs the REAL runner twice over a two-TU git project.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import make_project, fresh_store  # noqa: E402
from slice2_helpers import dirty  # noqa: E402
from slice3_helpers import (  # noqa: E402
    certify_project,
    init_project_id,
    make_multi_tu_git_project,
    open_q,
    publish_cert,
    run_index,
    store_nonce,
    view_from_ctx,
    view_from_world_row,
    v2_of,
)

from fw_context_mcp.store_v2 import query, worlds  # noqa: E402
from fw_context_mcp.store_v2.publish import open_v2_store_for_write  # noqa: E402

MAIN_V2 = ('#include "util.h"\n#include "cfg.h"\n#include "trans.h"\n'
           "int util(void) { return UTIL_VERSION + 1; }\n"
           "int main(void) { return util(); }\n")


def sel(path: str) -> query.TuSelector:
    return query.TuSelector(canon_source_path=path)


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    """§35 central multi-version scenario through the REAL runner.

    Both runs accumulate into ONE v2 store (same namespace): version A
    (main.c v1 + util.c) then version B (main.c dirty → new state) while
    util.c is touched mtime-only — reparsed, converging onto the SAME
    published state.  util.c is the common TU; main.c the changed TU."""
    tmp = tmp_path_factory.mktemp("s3central")
    from fw_context_mcp.indexer import _unit_processor as _UP
    from fw_context_mcp.store_v2 import certify as C
    old = os.environ.get("FW_CONTEXT_SHADOW_V2")
    os.environ["FW_CONTEXT_SHADOW_V2"] = "1"
    _UP._reset_v2_flag_cache()
    C.reset_flag_cache()
    try:
        data = make_multi_tu_git_project(tmp)
        proj: Path = data["proj"]
        init_project_id(proj)
        _, db_a = run_index(proj, tmp, name="index")
        v2 = v2_of(db_a)
        dirty(proj, "src/main.c", MAIN_V2)
        # Version B re-parses BOTH TUs (documented force lever → both cheap
        # staleness tiers bypassed): main.c certifies a NEW state, util.c
        # converges onto the SAME published state.  Compile commands are
        # byte-identical, so both runs share one build plan.
        os.environ["FW_CONTEXT_FORCE_REFINDEX"] = "1"
        try:
            run_index(proj, tmp, name="index")
        finally:
            os.environ.pop("FW_CONTEXT_FORCE_REFINDEX", None)
    finally:
        if old is None:
            os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
        else:
            os.environ["FW_CONTEXT_SHADOW_V2"] = old
        _UP._reset_v2_flag_cache()
        C.reset_flag_cache()
    conn = open_q(v2)
    rows = conn.execute(
        "SELECT world_id FROM snapshot_builds ORDER BY world_id").fetchall()
    world_ids = [int(r["world_id"]) for r in rows]
    conn.close()
    assert len(world_ids) == 2, world_ids
    return {"proj": proj, "v2": v2, "world_a": world_ids[0],
            "world_b": world_ids[1]}


def _views(central):
    conn = open_q(central["v2"])
    va = view_from_world_row(central["v2"], central["world_a"])
    vb = view_from_world_row(central["v2"], central["world_b"])
    return va, vb, conn


# ── §18 / T06+T07: snapshot isolation through the real hook ───────────────────

def test_s3_t06_t07_changed_tu_isolated_per_view(central):
    """Same source path, two versions: world A serves ONLY the A state,
    world B ONLY the B state — never a union, never newest-wins."""
    va, vb, conn = _views(central)
    try:
        assert va.snapshot_id != vb.snapshot_id
        assert va.build_id == vb.build_id   # byte-identical compile plans
        ra = query.inspect_tu(conn, va, selector=sel("src/main.c"))
        rb = query.inspect_tu(conn, vb, selector=sel("src/main.c"))
        assert ra.disposition == "bound" and rb.disposition == "bound"
        assert ra.state.tusemantic_key != rb.state.tusemantic_key
        assert ra.provenance["world_id"] == central["world_a"]
        assert rb.provenance["world_id"] == central["world_b"]
        ha = query.lookup_symbol(conn, va, "main")
        hb = query.lookup_symbol(conn, vb, "main")
        assert {h.state.tusemantic_key for h in ha.hits} == \
            {ra.state.tusemantic_key}
        assert {h.state.tusemantic_key for h in hb.hits} == \
            {rb.state.tusemantic_key}
        assert all(h.binding.world_id == central["world_a"] for h in ha.hits)
        assert all(h.binding.world_id == central["world_b"] for h in hb.hits)
    finally:
        conn.close()


# ── §19 / T10: common TU → same shared state, per-world bindings ──────────────

def test_s3_t10_common_tu_shares_state_with_distinct_provenance(central):
    va, vb, conn = _views(central)
    try:
        ra = query.inspect_tu(conn, va, selector=sel("src/util.c"))
        rb = query.inspect_tu(conn, vb, selector=sel("src/util.c"))
        assert ra.disposition == "bound" and rb.disposition == "bound"
        # same shared SemanticState…
        assert ra.state.tusemantic_key == rb.state.tusemantic_key
        assert ra.state.state_id == rb.state.state_id
        # …bound through DIFFERENT bindings of DIFFERENT worlds
        assert ra.binding.binding_id != rb.binding.binding_id
        assert ra.provenance["world_id"] != rb.provenance["world_id"]
        # G4: the compile-context identity is content-derived — the SAME
        # context id participates in both worlds via separate bindings.
        assert ra.provenance["tu_context_id"] == \
            rb.provenance["tu_context_id"]
        ha = query.lookup_symbol(conn, va, "helper")
        hb = query.lookup_symbol(conn, vb, "helper")
        assert {h.state.tusemantic_key for h in ha.hits} == \
            {ra.state.tusemantic_key}
        assert {h.state.tusemantic_key for h in hb.hits} == \
            {rb.state.tusemantic_key}
        assert ha.hits[0].binding.binding_id == ra.binding.binding_id
        assert hb.hits[0].binding.binding_id == rb.binding.binding_id
    finally:
        conn.close()


# ── §17 / T08+T09: build isolation — same snapshot, different states ──────────

def _certify_with_extra_define(tmp_path, proj, nonce, *, context_id):
    """Certify main.c with an extra -D flag (different NCC ⇒ different
    state), restoring compile_commands.json bytes afterwards."""
    cc = proj / "compile_commands.json"
    original = cc.read_text()
    entries = json.loads(original)
    for e in entries:
        if e["file"].endswith("main.c"):
            e["arguments"] = list(e["arguments"]) + ["-DOTHER=7"]
    cc.write_text(json.dumps(entries))
    try:
        return certify_project(tmp_path, proj, nonce=nonce,
                               context_id=context_id)
    finally:
        cc.write_text(original)


def test_s3_t08_t09_two_builds_same_snapshot_isolated(tmp_path):
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    store = fresh_store(tmp_path)
    nonce = store_nonce(store)
    try:
        _, parsed_a, cert_a = certify_project(tmp_path, proj, nonce=nonce,
                                              context_id="cx-a")
        assert publish_cert(store, cert_a, parsed_a) == "published_new"
        ctx_a = worlds.build_world_context(project_root=proj,
                                           build_system="cmake",
                                           project_id="pid-s3")
        worlds.bind_tu(store, ctx_a, cert_a, tu_context_id="cx-a",
                       canon_source_path="src/main.c",
                       state_key=cert_a.tusemantic_key,
                       state_generation=cert_a.parser_generation,
                       is_new_state=True)
        worlds.finalize_world(store, ctx_a)
        _, parsed_b, cert_b = _certify_with_extra_define(
            tmp_path, proj, nonce, context_id="cx-b")
        assert publish_cert(store, cert_b, parsed_b) == "published_new"
        assert cert_b.tusemantic_key != cert_a.tusemantic_key
        ctx_b = worlds.build_world_context(project_root=proj,
                                           build_system="make",
                                           project_id="pid-s3")
        assert ctx_b.snapshot.snapshot_id == ctx_a.snapshot.snapshot_id
        assert ctx_b.build_identity_sha != ctx_a.build_identity_sha
        worlds.bind_tu(store, ctx_b, cert_b, tu_context_id="cx-b",
                       canon_source_path="src/main.c",
                       state_key=cert_b.tusemantic_key,
                       state_generation=cert_b.parser_generation,
                       is_new_state=True)
        worlds.finalize_world(store, ctx_b)
        query.ensure_slice3_schema(store._conn)
        conn = open_q(store.path)
        try:
            va = view_from_ctx(ctx_a)
            vb = view_from_ctx(ctx_b)
            assert va.snapshot_id == vb.snapshot_id
            ta = query.inspect_tu(conn, va, selector=sel("src/main.c"))
            tb = query.inspect_tu(conn, vb, selector=sel("src/main.c"))
            assert ta.state.tusemantic_key == cert_a.tusemantic_key
            assert tb.state.tusemantic_key == cert_b.tusemantic_key
            # no cross-build union in either direction
            ha = query.lookup_symbol(conn, va, "util")
            hb = query.lookup_symbol(conn, vb, "util")
            assert {h.state.tusemantic_key for h in ha.hits} == \
                {cert_a.tusemantic_key}
            assert {h.state.tusemantic_key for h in hb.hits} == \
                {cert_b.tusemantic_key}
            assert {h.binding.world_id for h in ha.hits} == {ctx_a.world_id}
            assert {h.binding.world_id for h in hb.hits} == {ctx_b.world_id}
        finally:
            conn.close()
    finally:
        store.close()


# ── §10/T11: identical raw USRs never collapse across states ──────────────────

def test_s3_t11_usr_alias_never_crosses_states(central):
    va, vb, conn = _views(central)
    try:
        ha = query.lookup_symbol(conn, va, "main")
        hb = query.lookup_symbol(conn, vb, "main")
        assert len(ha.hits) == 1 and len(hb.hits) == 1
        usr_a, usr_b = ha.hits[0].raw_usr, hb.hits[0].raw_usr
        assert usr_a and usr_a == usr_b            # the alias REALLY exists
        # …yet each view returns exactly its own state's symbol — the same
        # raw_usr in the other state is invisible (attribute, not a key)
        assert ha.hits[0].state.tusemantic_key != \
            hb.hits[0].state.tusemantic_key
        assert ha.hits[0].state.state_id != hb.hits[0].state.state_id
    finally:
        conn.close()


# ── §11/T12: edges are state-local; cross-state addressing fails closed ───────

def test_s3_t12_edges_resolve_within_state_only(central):
    va, vb, conn = _views(central)
    try:
        ha = query.lookup_symbol(conn, va, "main")
        hit = ha.hits[0]
        edges = query.symbol_edges(
            conn, va, state_key=hit.state.tusemantic_key,
            state_generation=hit.state.parser_generation,
            local_key=hit.local_key)
        assert edges, "main() should carry at least one edge"
        for e in edges:
            assert e.provenance["state_key"] == hit.state.tusemantic_key
            if e.to_symbol is not None:
                # endpoint resolves WITHIN the same state only
                assert e.to_symbol.tusemantic_key == hit.state.tusemantic_key
                assert e.to_symbol.parser_generation == \
                    hit.state.parser_generation
        assert any(e.to_symbol is not None for e in edges), \
            "call target util should resolve state-locally"
        # the B version's state is INVISIBLE to view A even for addressing
        rb = query.inspect_tu(conn, vb, selector=sel("src/main.c"))
        with pytest.raises(query.SymbolNotFound) as ei:
            query.symbol_edges(
                conn, va, state_key=rb.state.tusemantic_key,
                state_generation=rb.state.parser_generation, local_key=1)
        assert ei.value.code == "SYMBOL_NOT_FOUND"
        assert ei.value.detail["tusemantic_key"] == rb.state.tusemantic_key
    finally:
        conn.close()


# ── §36: DB membership does NOT imply visibility ──────────────────────────────

def test_s3_db_membership_not_visibility(central):
    """A published state Z in the SAME store, holding the same symbol name,
    bound to NO world of view A, must be invisible to every A query."""
    va, vb, conn = _views(central)
    try:
        common = query.inspect_tu(conn, va, selector=sel("src/util.c"))
        u_state = common.state
        store = open_v2_store_for_write(central["v2"])
        try:
            cur = store._conn.execute(
                "INSERT INTO semantic_states(tusemantic_key, "
                "parser_generation, key_format, l0_source_blob_sha, "
                "l1_ncc_digest_sha, l2_closure_digest_sha, "
                "l3_env_digest_sha, publication, completeness_sha, "
                "digest_algorithm, fact_row_count, fact_schema_version, "
                "payload_format, payload_ref, payload_digest_sha, "
                "owner_lease, path_spelling_sensitive, created_at, "
                "created_by_run) SELECT 'zz:'||tusemantic_key, "
                "parser_generation, key_format, l0_source_blob_sha, "
                "l1_ncc_digest_sha, l2_closure_digest_sha, "
                "l3_env_digest_sha, 'published', completeness_sha, "
                "digest_algorithm, fact_row_count, fact_schema_version, "
                "payload_format, payload_ref, payload_digest_sha, "
                "owner_lease, path_spelling_sensitive, created_at, 'test-z' "
                "FROM semantic_states WHERE state_id=? AND parser_generation=?",
                (u_state.state_id, u_state.parser_generation))
            z_id = int(cur.lastrowid)
            store._conn.execute(
                "INSERT INTO semantic_symbols_core(state_id, local_key, "
                "name, raw_usr, kind, is_definition, is_virtual, "
                "is_pure_virtual, is_template, signature) SELECT ?, "
                "local_key, name, raw_usr, kind, is_definition, "
                "is_virtual, is_pure_virtual, is_template, signature FROM "
                "semantic_symbols_core WHERE state_id=? LIMIT 1",
                (z_id, u_state.state_id))
            store._conn.commit()
        finally:
            store.close()
        # the DB really does contain the planted symbol row…
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM semantic_symbols_core WHERE "
            "state_id=?", (z_id,)).fetchone()["n"]
        assert n == 1
        z_name = conn.execute(
            "SELECT name FROM semantic_symbols_core WHERE state_id=?",
            (z_id,)).fetchone()["name"]
        # …but NO query against view A may return it
        res = query.lookup_symbol(conn, va, z_name)
        assert res.hits
        assert all(h.state.state_id != z_id for h in res.hits)
        with pytest.raises(query.SymbolNotFound):
            query.symbol_edges(conn, va,
                               state_key="zz:" + u_state.tusemantic_key,
                               state_generation=u_state.parser_generation,
                               local_key=1)
    finally:
        conn.close()
