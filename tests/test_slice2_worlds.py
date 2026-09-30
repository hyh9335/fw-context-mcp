"""test_slice2_worlds — S2.3–S2.7 store-level oracles for the binding path.

Covers SLICE2 task §9–§17 + §27 crash cases at the store layer (real
certificates + real publication, then the production ``worlds`` functions):

* S2-T03/S2-T05 multiple builds coexist; equal-KB-C states SHARED, not
  duplicated; binding never mints or mutates state (INV-1/INV-2/INV-5).
* S2-T04 two snapshots coexist under one namespace; bindings isolated.
* S2-T06 changed source ⇒ new state; old bindings unchanged.
* S2-T07 binding to a non-published state refused (fail closed, C4).
* A-2 gate: path_spelling_sensitive state + different canon source path ⇒
  refusal + integrity event.
* S2-T08 poisoned TU ⇒ explicit poison disposition (no fake key, INV-6).
* S2-T09/T10 binding idempotence + conflict refusal (no last-writer-wins).
* S2-T12/T13 incomplete world stays 'unfinalized'; retry completes (C1–C3).
* S2-T14 legacy retention activity leaves the v2 store untouched.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import (  # noqa: E402
    certify_project,
    fresh_store,
    make_project,
    store_nonce,
)
from slice2_helpers import (  # noqa: E402
    binding_table,
    counts,
    dirty,
    make_git_project,
    metric_rows,
    state_table,
    v2_rows,
    world_table,
)

from fw_context_mcp.store_v2 import worlds  # noqa: E402
from fw_context_mcp.store_v2.facts import (  # noqa: E402
    build_cold_payload,
    extract_state_facts,
    fact_set_digest,
)
from fw_context_mcp.store_v2.identity_capture import (  # noqa: E402
    capture_snapshot,
)
from fw_context_mcp.store_v2.publish import (  # noqa: E402
    publish_one_shot,
    record_parser_identity,
)


@pytest.fixture()
def env(tmp_path):
    """Project + v2 store + one certified+published state."""
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    store = fresh_store(tmp_path)
    nonce = store_nonce(store)
    unit, parsed, cert = certify_project(tmp_path, proj, nonce=nonce)
    record_parser_identity(store, cert.parser_identity)
    facts = extract_state_facts(parsed, cert)
    cold = build_cold_payload(parsed, cert, facts)
    outcome = publish_one_shot(
        store, cert=cert, facts=facts, cold_payload=cold,
        fact_digest=fact_set_digest(facts, cold), owner="s2-test")
    assert outcome.outcome == "published_new"
    ctx = worlds.build_world_context(project_root=proj, build_system="cmake",
                                     project_id="pid-s2")
    yield {"store": store, "cert": cert, "parsed": parsed, "unit": unit,
           "proj": proj, "data": data, "ctx": ctx,
           "key": cert.tusemantic_key, "gen": cert.parser_generation}
    store.close()


def bind(env, tu="cx", canon="src/main.c", *, cert=None, ctx=None,
         is_new=False):
    return worlds.bind_tu(
        env["store"], ctx or env["ctx"], cert or env["cert"],
        tu_context_id=tu, canon_source_path=canon,
        state_key=(cert or env["cert"]).tusemantic_key,
        state_generation=(cert or env["cert"]).parser_generation,
        is_new_state=is_new)


# ── S2-T03/T05: build coexistence + shared state ─────────────────────────────

def test_s2_t03_two_builds_share_equal_kb_c_state(env):
    """Two worlds (different BuildIdentity legs) bind to the SAME published
    state — coexistence without duplication, no cross-world contamination."""
    store, cert, key, gen = env["store"], env["cert"], env["key"], env["gen"]
    ctx_b = worlds.build_world_context(project_root=env["proj"],
                                       build_system="make",
                                       project_id="pid-s2")
    r1 = bind(env, tu="cx")                      # Build A (cmake)
    r2 = bind(env, tu="cx", ctx=ctx_b)           # Build B (make)
    assert (r1.status, r2.status) == ("bound_new", "bound_new")
    shape = counts(store.path)
    assert shape["states"] == 1                  # KB-C equal ⇒ ONE state
    assert shape["worlds"] == 2                  # ...but TWO worlds
    assert shape["bindings"] == 2
    worlds_rows = world_table(store.path)
    assert {w["world_status"] for w in worlds_rows} == {"unfinalized"}
    b_rows = binding_table(store.path)
    assert {b["state_key"] for b in b_rows} == {key}
    assert {b["world_id"] for b in b_rows} == \
        {env["ctx"].world_id, ctx_b.world_id}


def test_s2_t05_binding_mints_no_state_and_mutates_no_state(env):
    """INV-5/INV-2: bind_tu neither inserts nor updates semantic_states."""
    store, ctx = env["store"], env["ctx"]
    before = state_table(store.path)
    r = bind(env)
    assert r.status == "bound_new"
    after = state_table(store.path)
    assert before == after                       # byte-identical state row


# ── S2-T04: two snapshots coexist ─────────────────────────────────────────────

def test_s2_t04_two_snapshots_two_worlds_isolated_bindings(tmp_path, env):
    """Dirty the worktree ⇒ new SourceSnapshotID ⇒ second world; bindings
    stay per-world; namespace shared."""
    store, proj, cert = env["store"], env["proj"], env["cert"]
    ctx_a = env["ctx"]
    snap_a = capture_snapshot(proj)
    assert ctx_a.snapshot.snapshot_id == snap_a.snapshot_id
    r1 = bind(env, tu="cx")
    assert r1.status == "bound_new"

    dirty(proj, "inc/util.h", "#define UTIL_VERSION 2\nint util2(void);\n")
    ctx_b = worlds.build_world_context(project_root=proj, build_system="cmake",
                                       project_id="pid-s2")
    snap_b = capture_snapshot(proj)
    assert snap_b.snapshot_id != snap_a.snapshot_id
    assert ctx_b.snapshot.snapshot_id == snap_b.snapshot_id
    r2 = bind(env, tu="cx", ctx=ctx_b)
    assert r2.status == "bound_new"

    ws = world_table(store.path)
    assert len(ws) == 2
    assert {w["snapshot_id"] for w in ws} == {snap_a.snapshot_id,
                                              snap_b.snapshot_id}
    assert len({w["namespace_id"] for w in ws}) == 1
    per_world = {w["world_id"]: b["state_key"]
                 for w in ws
                 for b in binding_table(store.path)
                 if b["world_id"] == w["world_id"]}
    assert len(per_world) == 2                   # one binding per world
    assert set(per_world.values()) == {cert.tusemantic_key}


# ── S2-T06: changed state, isolated old binding ──────────────────────────────

def test_s2_t06_changed_source_new_state_old_binding_intact(tmp_path, env):
    store, proj = env["store"], env["proj"]
    key1, gen = env["key"], env["gen"]
    r1 = bind(env)
    assert r1.status == "bound_new"

    # a changed TU source ⇒ a new certified state (different L0)
    dirty(proj, "src/main.c",
          '#include "util.h"\n#include "cfg.h"\n#include "trans.h"\n'
          'int util(void) { return UTIL_VERSION; }\nint main2(void) { return 1; }\n')
    store_nonce(store)
    unit, parsed, cert2 = certify_project(tmp_path, proj,
                                          nonce=store_nonce(store))
    facts = extract_state_facts(parsed, cert2)
    cold = build_cold_payload(parsed, cert2, facts)
    outcome = publish_one_shot(store, cert=cert2, facts=facts,
                               cold_payload=cold,
                               fact_digest=fact_set_digest(facts, cold),
                               owner="s2-test")
    assert outcome.outcome == "published_new"
    key2 = cert2.tusemantic_key
    assert key2 != key1
    r2 = bind(env, tu="cx-2", cert=cert2, is_new=True)
    assert r2.status == "bound_new"

    shape = counts(store.path)
    assert shape["states"] == 2
    rows = {b["tu_context_id"]: b["state_key"]
            for b in binding_table(store.path)}
    assert rows == {"cx": key1, "cx-2": key2}    # no contamination


# ── S2-T07 (C4): unpublished target refused ──────────────────────────────────

def test_s2_t07_binding_to_unpublished_state_refused(env):
    store, ctx = env["store"], env["ctx"]
    store._conn.execute(
        "UPDATE semantic_states SET publication='staging' WHERE "
        "tusemantic_key=? AND parser_generation=?", (env["key"], env["gen"]))
    store._conn.commit()
    events_before = v2_rows(store.path,
                            "SELECT COUNT(*) c FROM integrity_events")[0]["c"]
    r = bind(env)
    assert r.status == "refused_unpublished"
    # no BINDING row is created (fail closed); the world scaffold may exist
    # (run metadata) but stays 'unfinalized' — query-invisible by Row 3
    assert binding_table(store.path) == []
    for w in world_table(store.path):
        assert w["world_status"] == "unfinalized"
    events_after = v2_rows(store.path,
                           "SELECT COUNT(*) c FROM integrity_events")[0]["c"]
    assert events_after == events_before + 1
    ev = v2_rows(store.path, "SELECT kind FROM integrity_events "
                                 "ORDER BY event_id DESC LIMIT 1")[0]
    assert ev["kind"] == "binding_to_unpublished_state"


# ── A-2 gate (store level) ────────────────────────────────────────────────────

def test_s2_a2_gate_refuses_cross_spelling_binding(env):
    store, ctx = env["store"], env["ctx"]
    store._conn.execute(
        "UPDATE semantic_states SET path_spelling_sensitive=1 WHERE "
        "tusemantic_key=? AND parser_generation=?", (env["key"], env["gen"]))
    store._conn.commit()
    assert bind(env, tu="cx", canon="src/main.c").status == "bound_new"
    r = bind(env, tu="cx-renamed", canon="src/renamed_main.c")
    assert r.status == "refused_path_spelling"
    ev = v2_rows(store.path, "SELECT kind FROM integrity_events "
                                "ORDER BY event_id DESC LIMIT 1")[0]
    assert ev["kind"] == "binding_path_spelling_refused"
    # spelling-equal canon path is allowed
    assert bind(env, tu="cx-2", canon="src/main.c").status == "bound_new"


def test_s2_a2_normal_state_binds_across_spellings(env):
    """A NON-flagged state keeps the path-insensitive sharing behavior:
    different canon paths bind fine (RENAME_NOT_SHAREABLE applies only to
    flagged states)."""
    assert bind(env, tu="cx", canon="src/main.c").status == "bound_new"
    assert bind(env, tu="cx-renamed", canon="src/renamed_main.c").status == \
        "bound_new"


# ── S2-T08: poisoned TU disposition ──────────────────────────────────────────

def test_s2_t08_poisoned_tu_explicit_disposition(tmp_path, env):
    """A poisoned certificate becomes binding='poisoned' + world_poisons +
    tu_private_payloads; NO state key is minted (INV-6)."""
    store, proj = env["store"], env["proj"]
    # a TU whose closure cannot resolve ⇒ real poison from the production
    # certification path
    (proj / "src" / "main.c").write_text(
        '#include "ghost.h"\nint main(void){return 0;}\n')
    unit, parsed, pcert = certify_project(tmp_path, proj, nonce="n1")
    assert pcert.status == "poisoned", pcert.poison_reason

    ctx = env["ctx"]
    r = worlds.record_poison(store, ctx, pcert, tu_context_id="cx-poison",
                             canon_source_path="src/main.c")
    assert r.status == "poisoned_recorded"
    shape = counts(store.path)
    assert shape["states"] == 1                  # poison minted NO state
    rows = [b for b in binding_table(store.path)
            if b["binding_status"] == "poisoned"]
    assert len(rows) == 1
    assert rows[0]["disposition"] == "poisoned"
    assert rows[0]["state_key"] is None
    assert rows[0]["poison_component"].startswith("tu:cx-poison:")
    wp = v2_rows(store.path, "SELECT * FROM world_poisons")
    assert len(wp) == 1 and wp[0]["world_id"] == ctx.world_id
    tp = v2_rows(store.path, "SELECT * FROM tu_private_payloads")
    assert len(tp) == 1 and tp[0]["binding_id"] == rows[0]["binding_id"]
    payload = json.loads(bytes(tp[0]["payload"]).decode("utf-8"))
    assert payload["poison_reason"] == pcert.poison_reason
    # idempotent replay
    r2 = worlds.record_poison(store, ctx, pcert, tu_context_id="cx-poison",
                              canon_source_path="src/main.c")
    assert r2.status == "poisoned_recorded"
    assert len(v2_rows(store.path, "SELECT * FROM world_poisons")) == 1


# ── S2-T09/T10: idempotence + conflict ────────────────────────────────────────

def test_s2_t09_binding_idempotence(env):
    store = env["store"]
    assert bind(env).status == "bound_new"
    r = bind(env)
    assert r.status == "bound_existing" and r.binding_id is not None
    assert len(binding_table(store.path)) == 1


def test_s2_t10_conflicting_binding_refused(env):
    store = env["store"]
    key1, gen = env["key"], env["gen"]
    assert bind(env).status == "bound_new"
    # a certified state under a DIFFERENT key for the same (world, tu):
    # re-presenting the certificate with another key_result.root simulates
    # the conflicting-state admission without touching frozen fixtures
    from fw_context_mcp.store_v2.keys import TUKeyResult
    cert2 = dataclasses.replace(
        env["cert"],
        key_result=dataclasses.replace(env["cert"].key_result,
                                       root="other:" + key1))
    store._conn.execute(
        "INSERT INTO semantic_states(tusemantic_key, parser_generation, "
        "key_format, l0_source_blob_sha, l1_ncc_digest_sha, "
        "l2_closure_digest_sha, l3_env_digest_sha, publication, "
        "completeness_sha, digest_algorithm, fact_row_count, "
        "fact_schema_version, payload_format, payload_ref, "
        "payload_digest_sha, owner_lease, path_spelling_sensitive, "
        "created_at, created_by_run) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
        "?,?,?,?,?,?,?)",
        ("other:" + key1, gen, env["cert"].inputs.key_format,
         "0" * 64, "1" * 64, "2" * 64, "3" * 64, "published", "d" * 64,
         "fwctx-fsd/v1+sha256", 1, 1, "normalized", None, "e" * 64, None,
         0, "now", "test"))
    store._conn.commit()
    r = bind(env, cert=cert2)
    assert r.status == "refused_conflict"
    rows = binding_table(store.path)
    assert len(rows) == 1 and rows[0]["state_key"] == key1  # NO last-writer-wins
    ev = v2_rows(store.path, "SELECT kind FROM integrity_events "
                                "ORDER BY event_id DESC LIMIT 1")[0]
    assert ev["kind"] == "binding_conflict"


# ── S2-T12/T13: world lifecycle (C1/C2/C3) ───────────────────────────────────

def test_s2_t12_incomplete_world_stays_unfinalized_then_completes(env):
    store, ctx = env["store"], env["ctx"]
    assert bind(env).status == "bound_new"
    # a TU the run observed but that never produced a binding row (C2:
    # crash between certification bookkeeping and the binding insert)
    ctx.note_processed("cx-never-bound")
    fin = worlds.finalize_world(store, ctx)
    assert fin.ok is False and fin.world_status == "unfinalized"
    assert "cx-never-bound" in fin.missing
    wrow = [w for w in world_table(store.path)
            if w["world_id"] == ctx.world_id][0]
    assert wrow["world_status"] == "unfinalized"
    # C3: the retry completes the world idempotently
    bind(env, tu="cx-never-bound")
    fin2 = worlds.finalize_world(store, ctx)
    assert fin2.ok is True and fin2.world_status == "active"
    assert fin2.bound_count == 2 and fin2.poisoned_count == 0
    # re-finalize is a no-op (no duplicate metrics / no error)
    fin3 = worlds.finalize_world(store, ctx)
    assert fin3.ok is True and fin3.world_status == "active"


def test_s2_t12b_empty_run_cannot_finalize(env):
    """A run whose world was never created (no certified TU) stays missing."""
    fin = worlds.finalize_world(env["store"], env["ctx"])
    assert fin.ok is False and fin.world_status == "missing"


def test_s2_t13_finalize_carries_poisoned_world(env):
    """A world with a poisoned TU still finalizes — published-but-degraded
    (D-4); the poison is visible at world level."""
    store, ctx = env["store"], env["ctx"]
    pcert = dataclasses.replace(env["cert"], status="poisoned",
                                poison_reason="synthetic:unit-test",
                                inputs=None, key_result=None,
                                source_blob_sha="")
    worlds.record_poison(store, ctx, pcert, tu_context_id="cx-poison",
                         canon_source_path="src/main.c")
    assert bind(env).status == "bound_new"
    fin = worlds.finalize_world(store, ctx)
    assert fin.ok is True
    assert fin.world_status == "active"
    assert (fin.bound_count, fin.poisoned_count) == (1, 1)


# ── S2-T14: legacy retention leaves the v2 store untouched ───────────────────

def test_s2_t14_legacy_retention_isolation(env, tmp_path):
    """The legacy cleanup step deletes only legacy rows; v2 worlds/states/
    bindings survive byte-identically (separate DB + no code path)."""
    store, proj = env["store"], env["proj"]
    assert bind(env).status == "bound_new"
    worlds.finalize_world(store, env["ctx"])
    before = (world_table(store.path), state_table(store.path),
              binding_table(store.path))

    # legacy side: a real legacy db (full production schema) + the
    # production retention step; an older config_hash gets deleted there
    import sqlite3
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    conn = sqlite3.connect(legacy_dir / "index.db")
    conn.row_factory = sqlite3.Row
    from fw_context_mcp.indexer.db._connection import ensure_schema
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO build_configs(project_id, variant, image, config_hash, "
        "compile_commands_path, created_at) "
        "VALUES('pid-s2', '', '', 'oldhash', 'cc.json', '2020-01-01')")
    conn.commit()

    from fw_context_mcp.indexer._postprocess import _step_cleanup_old_builds
    _step_cleanup_old_builds(conn, {"config_hash": "currenthash",
                                    "project_id": "pid-s2",
                                    "db_dir": legacy_dir,
                                    "variant": "", "image": ""})
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM build_configs WHERE "
                        "config_hash='oldhash'").fetchone()[0]
    conn.close()
    assert left == 0                             # legacy cleanup ran

    after = (world_table(store.path), state_table(store.path),
             binding_table(store.path))
    assert before == after                       # v2 untouched


# ── world claim (minimal D2-S3) ───────────────────────────────────────────────

def test_s2_world_claim_exclusive_within_lease(env):
    store, ctx = env["store"], env["ctx"]
    bind(env)
    assert worlds.world_claim(store, ctx, "owner-a") is True
    assert worlds.world_claim(store, ctx, "owner-b") is False
    assert worlds.world_claim(store, ctx, "owner-a") is True   # renew/reentry


# ── metrics persistence (§40) ────────────────────────────────────────────────

def test_s2_metrics_persisted_per_run(env):
    store, ctx = env["store"], env["ctx"]
    bind(env, tu="cx", is_new=True)      # state published new this run
    bind(env, tu="cx2", is_new=False)    # same state, already-seen
    worlds.finalize_world(store, ctx)
    worlds.record_run_metrics(store, ctx)
    m = metric_rows(store.path)[ctx.run_id]
    for name in worlds.METRIC_NAMES:
        assert name in m, name
    assert m["bindings_created"] == 2
    assert m["new_semantic_states"] == 1
    assert m["existing_semantic_states_matched"] == 1
    assert m["unique_semantic_states"] == 1
    assert m["tu_world_count"] == 2
    assert m["finalized"] == 1
