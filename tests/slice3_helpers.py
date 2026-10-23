"""slice3_helpers — fixture builders for Phase-4 Slice-3 strict-query tests.

Extends :mod:`slice2_helpers` with the read-side: multi-TU git projects,
QueryView builders over production world contexts, a poisoned-world
environment, and full-store row snapshots for the zero-write proof.  Every
world comes through the REAL Slice-2 write path; every query goes through
``store_v2.query`` with a ``mode=ro`` connection.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from slice1_helpers import (  # noqa: E402
    CLANG_TARGET,
    build_units,
    certify_project,
    fresh_store,
    make_project,
    parse_unit,
)
from slice2_helpers import (  # noqa: E402
    dirty,
    git,
    make_git_project,
)

from fw_context_mcp.store_v2 import query, worlds  # noqa: E402
from fw_context_mcp.store_v2.facts import (  # noqa: E402
    build_cold_payload,
    extract_state_facts,
    fact_set_digest,
)
from fw_context_mcp.store_v2.publish import (  # noqa: E402
    open_v2_store_for_write,
    publish_one_shot,
    record_parser_identity,
    store_nonce,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


def _two_entry_cc(entry_a: dict, proj: Path) -> None:
    """Turn a one-entry compile_commands.json into main.c + util.c entries."""
    argv_b = list(entry_a["arguments"])
    argv_b[argv_b.index("src/main.c")] = "src/util.c"   # the SOURCE token
    entry_b = {"directory": entry_a["directory"], "arguments": argv_b,
               "file": str(proj / "src" / "util.c")}
    (proj / "compile_commands.json").write_text(
        json.dumps([entry_a, entry_b]))


def make_multi_tu_git_project(tmp_path: Path) -> dict:
    """Two-TU git project: main.c (changed between versions) + util.c
    (common TU, content-stable) + a two-entry compile_commands.json."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    (proj / "src" / "util.c").write_text(
        '#include "util.h"\nint helper(void) { return UTIL_VERSION * 2; }\n')
    _two_entry_cc(json.loads((proj / "compile_commands.json").read_text())[0],
                  proj)
    git("add", "-A", cwd=proj)
    git("commit", "-q", "-m", "two-tu baseline", cwd=proj)
    return data


def make_two_tu_project(tmp_path: Path) -> dict:
    """Store-level variant of the multi-TU project (no git needed)."""
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    (proj / "src" / "util.c").write_text(
        '#include "util.h"\nint helper(void) { return UTIL_VERSION * 2; }\n')
    _two_entry_cc(json.loads((proj / "compile_commands.json").read_text())[0],
                  proj)
    return data


def certify_unit(proj: Path, *, src_token: str, nonce: str,
                 context_id: str):
    """Production certify of ONE compile entry whose argv carries
    ``src_token`` as the source (the cc file may hold several entries)."""
    from fw_context_mcp.store_v2.certify import build_certificate
    units = build_units(proj)
    unit = next(u for u in units if src_token in Path(u.file).name)
    parsed = parse_unit(unit)
    cert = build_certificate(unit, parsed, proj, nonce=nonce,
                             context_id=context_id)
    return unit, parsed, cert


def publish_cert(store, cert, parsed) -> str:
    """Record parser identity + one-shot publication; returns the outcome."""
    record_parser_identity(store, cert.parser_identity)
    facts = extract_state_facts(parsed, cert)
    cold = build_cold_payload(parsed, cert, facts)
    outcome = publish_one_shot(
        store, cert=cert, facts=facts, cold_payload=cold,
        fact_digest=fact_set_digest(facts, cold), owner="s3-test")
    return outcome.outcome


def view_from_ctx(ctx, *, tu: query.TuSelector | None = None) -> query.QueryView:
    """QueryView over a production world context (bind FIRST so the run's
    BuildIdentity is anchored and ``ctx.build_identity_sha`` is set)."""
    assert ctx.build_identity_sha, "bind_tu must run before view_from_ctx"
    return query.QueryView(
        repository_namespace=ctx.namespace_id,
        snapshot_id=ctx.snapshot.snapshot_id,
        build_id=ctx.build_identity_sha, tu_selector=tu)


def view_from_world_row(v2_path: Path, world_id: int, *,
                        tu: query.TuSelector | None = None
                        ) -> query.QueryView:
    """QueryView reconstructed from a real runner-produced world row."""
    conn = query.open_query_connection(v2_path)
    try:
        w = conn.execute("SELECT * FROM snapshot_builds WHERE world_id=?",
                         (world_id,)).fetchone()
        return query.QueryView(
            repository_namespace=w["namespace_id"],
            snapshot_id=w["snapshot_id"],
            build_id=w["build_identity_sha"], tu_selector=tu)
    finally:
        conn.close()


def open_q(v2_path: Path):
    return query.open_query_connection(v2_path)


# ── store-level environments ──────────────────────────────────────────────────

def make_query_env(tmp_path: Path, *, finalize: bool = True) -> dict:
    """Project + v2 store + certified/published state + bound+finalized
    world — the Slice-2 ``env`` shape with the Slice-3 read side added."""
    data = make_project(tmp_path)
    proj: Path = data["proj"]
    store = fresh_store(tmp_path)
    nonce = store_nonce(store)
    unit, parsed, cert = certify_project(tmp_path, proj, nonce=nonce)
    assert publish_cert(store, cert, parsed) == "published_new"
    ctx = worlds.build_world_context(project_root=proj, build_system="cmake",
                                     project_id="pid-s3")
    r = worlds.bind_tu(store, ctx, cert, tu_context_id="cx",
                       canon_source_path="src/main.c",
                       state_key=cert.tusemantic_key,
                       state_generation=cert.parser_generation,
                       is_new_state=True)
    assert r.status == "bound_new", r.status
    if finalize:
        fin = worlds.finalize_world(store, ctx)
        assert fin.ok and fin.world_status == "active", fin
        worlds.record_run_metrics(store, ctx)
    query.ensure_slice3_schema(store._conn)
    view = view_from_ctx(ctx)
    conn = open_q(store.path)
    return {"store": store, "cert": cert, "parsed": parsed, "unit": unit,
            "proj": proj, "data": data, "ctx": ctx, "view": view, "conn": conn,
            "key": cert.tusemantic_key, "gen": cert.parser_generation,
            "nonce": nonce}


def bind(env, tu="cx", canon="src/main.c", *, cert=None, ctx=None,
         is_new=False):
    return worlds.bind_tu(
        env["store"], ctx or env["ctx"], cert or env["cert"],
        tu_context_id=tu, canon_source_path=canon,
        state_key=(cert or env["cert"]).tusemantic_key,
        state_generation=(cert or env["cert"]).parser_generation,
        is_new_state=is_new)


def make_poison_env(tmp_path: Path) -> dict:
    """World with ONE bound TU (published state) and ONE REAL poisoned TU
    (unresolved include from the production certification path) — the D-4
    published-but-degraded shape."""
    env = make_query_env(tmp_path, finalize=False)
    store, proj, ctx = env["store"], env["proj"], env["ctx"]
    (proj / "src" / "ghost_entry.c").write_text(
        '#include "ghost.h"\nint ghost_main(void){return 0;}\n')
    cc = proj / "compile_commands.json"
    entries = json.loads(cc.read_text())
    entry = dict(entries[0])
    entry["arguments"] = list(entry["arguments"])
    entry["arguments"][entry["arguments"].index("src/main.c")] = \
        "src/ghost_entry.c"
    entry["file"] = str(proj / "src" / "ghost_entry.c")
    cc.write_text(json.dumps([entry]))
    try:
        _, _, pcert = certify_project(tmp_path, proj, nonce=env["nonce"],
                                      context_id="cx-poison")
    finally:
        cc.write_text(json.dumps(entries))
    assert pcert.status == "poisoned", pcert.poison_reason
    r = worlds.record_poison(store, ctx, pcert, tu_context_id="cx-poison",
                             canon_source_path="src/ghost.c")
    assert r.status == "poisoned_recorded"
    fin = worlds.finalize_world(store, ctx)
    assert fin.ok and fin.world_status == "active"
    assert (fin.bound_count, fin.poisoned_count) == (1, 1)
    worlds.record_run_metrics(store, ctx)
    query.ensure_slice3_schema(store._conn)
    env["view"] = view_from_ctx(ctx)
    env["pcert"] = pcert
    return env


# ── real-runner helpers (copied pattern from test_slice2_hook) ────────────────

def init_project_id(proj: Path) -> str:
    from fw_context_mcp.config.settings import (_write_project_id,
                                                generate_project_id)
    pid = generate_project_id()
    _write_project_id(proj, pid)
    return pid


def run_index(proj: Path, tmp_path: Path, *, name: str = "index"):
    from fw_context_mcp.config import derive_project_id
    from fw_context_mcp.indexer import runner
    pid = derive_project_id(proj)
    db_path = tmp_path / name / pid / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    runner.run(compile_commands=proj / "compile_commands.json",
               db_path=db_path, project_root=proj, project_id=pid,
               index_refs=True, index_embeddings=False, analyze_symbols=False)
    return pid, db_path


def v2_of(db_path: Path) -> Path:
    return db_path.parent / "index-v2.db"


# ── full-store row snapshot (zero-write proof, S3-T16) ────────────────────────

def store_row_snapshot(conn) -> dict:
    """Every user table's complete row set (sorted, JSON-safe)."""
    snap: dict = {}
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    def _jsonsafe(v):
        # file_blobs.content is the cold payload's raw bytes (a blob is a
        # legitimate stored value; the snapshot only needs comparability)
        return v.decode("utf-8", "replace") if isinstance(v, (bytes, memoryview)) else v

    for t in tables:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608
        snap[t] = sorted(json.dumps([_jsonsafe(r[k]) for k in r.keys()])
                         for r in rows)
    return snap
