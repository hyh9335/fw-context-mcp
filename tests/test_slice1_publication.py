"""Slice-1 publication tests (S1.7-S1.10): fact_set_digest semantics,
one-shot immutable publication, convergence under identical re-publish,
the W-1 same-key/different-digest tripwire, quarantine refusal, the staging
claim surface (lease + stale recovery + heartbeat), SQLITE_BUSY backoff,
INV-6/INV-8 gates, and legacy isolation of the production hook.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

# fw_context_mcp redirects sys.modules['sqlite3'] -> pysqlite3 at import time
# (utils.py L234): catch BOTH exception families.
try:                                    # noqa: E402
    import pysqlite3
    SQLITE_INTEGRITY_ERROR = (pysqlite3.dbapi2.IntegrityError,
                              sqlite3.IntegrityError)
except ImportError:                     # pragma: no cover
    SQLITE_INTEGRITY_ERROR = (sqlite3.IntegrityError,)

from tests.slice1_helpers import (  # noqa: E402
    build_units,
    cert_store,
    certify_project,
    fresh_store,
    make_project,
    parse_unit,
    store,
    store_nonce,
)
from fw_context_mcp.store_v2 import certify as C  # noqa: E402
from fw_context_mcp.store_v2.facts import (  # noqa: E402
    build_cold_payload,
    extract_state_facts,
    fact_set_digest,
)
from fw_context_mcp.store_v2.publish import (  # noqa: E402
    get_published_state,
    heartbeat_claim,
    insert_certificate_row,
    integrity_event_count,
    open_v2_store_for_write,
    publish_one_shot,
    stage_claim,
    store_nonce,
)


def _publish(cert_store, owner="w1"):
    cert = cert_store["cert"]
    facts = extract_state_facts(cert_store["parsed"], cert)
    cold = build_cold_payload(cert_store["parsed"], cert, facts)
    digest = fact_set_digest(facts, cold)
    out = publish_one_shot(cert_store["store"], cert=cert, facts=facts,
                           cold_payload=cold, fact_digest=digest, owner=owner)
    insert_certificate_row(cert_store["store"], cert, outcome=out.outcome,
                           fact_set_digest=digest)
    return out, facts, cold, digest


# ── digest semantics ─────────────────────────────────────────────────────────

def test_s1_digest_deterministic_and_sensitive(cert_store, tmp_path):
    _out1, _f1, _c1, d1 = _publish(cert_store)
    # same parse again -> identical digest
    data = cert_store["data"]
    _u, parsed_b, cert_b = certify_project(tmp_path, data["proj"],
                                           nonce=cert_store["nonce"])
    facts_b = extract_state_facts(parsed_b, cert_b)
    cold_b = build_cold_payload(parsed_b, cert_b, facts_b)
    d2 = fact_set_digest(facts_b, cold_b)
    assert d1 == d2
    # a fact change -> different digest (symbol docstring lands in cold)
    (data["proj"] / "inc" / "util.h").write_text(
        "/* documented */\n#define UTIL_VERSION 2\nint util(void);\n")
    _u2, parsed_c, cert_c = certify_project(tmp_path, data["proj"],
                                            nonce=cert_store["nonce"])
    facts_c = extract_state_facts(parsed_c, cert_c)
    cold_c = build_cold_payload(parsed_c, cert_c, facts_c)
    d3 = fact_set_digest(facts_c, cold_c)
    assert d3 != d1


def test_s1_fact_rows_canonical(cert_store):
    """local_key 1-based ordinals; edges deduped with per-source ordinals."""
    cert = cert_store["cert"]
    facts = extract_state_facts(cert_store["parsed"], cert)
    assert [r[0] for r in facts.symbols] == list(range(1, len(facts.symbols) + 1))
    seen_pairs = [(r[0], r[1]) for r in facts.edges]
    assert len(seen_pairs) == len(set(seen_pairs))     # (local_key, ordinal) unique
    for r in facts.edges:
        assert r[2] in ("call", "ref", "member", "indirect", "implicit_construct",
                        "fp_assign", "inherit")


# ── one-shot publication + convergence ──────────────────────────────────────

def test_s1_publish_new_then_converge(cert_store):
    out1, facts, cold, digest = _publish(cert_store)
    assert out1.outcome == "published_new"
    row = get_published_state(cert_store["store"], key=cert_store["cert"].tusemantic_key,
                              generation=cert_store["cert"].parser_generation)
    assert row is not None and row["publication"] == "published"
    assert row["completeness_sha"] == digest
    assert row["digest_algorithm"] == "fwctx-fsd/v1+sha256"
    assert row["fact_row_count"] == facts.fact_row_count
    assert row["payload_digest_sha"]
    # hot facts persisted
    sid = row["state_id"]
    n_syms = cert_store["store"]._conn.execute(
        "SELECT count(*) FROM semantic_symbols_core WHERE state_id=?",
        (sid,)).fetchone()[0]
    assert n_syms == len(facts.symbols)
    # cold payload persisted and digest-consistent
    cold_row = cert_store["store"]._conn.execute(
        "SELECT payload_digest FROM slice1_state_cold WHERE tusemantic_key=? AND "
        "parser_generation=?", (cert_store["cert"].tusemantic_key,
                                cert_store["cert"].parser_generation)).fetchone()
    assert cold_row is not None
    # convergence: identical re-publish by a DIFFERENT owner is a no-op
    out2, _f, _c, _d = _publish(cert_store, owner="w2")
    assert out2.outcome == "converged"
    assert integrity_event_count(cert_store["store"], key=cert_store["cert"].tusemantic_key,
                                 generation=cert_store["cert"].parser_generation) == 0


def test_s1_w1_same_key_different_digest_fails_closed(cert_store):
    """REQUIRED adversarial: same (key, generation), different completeness
    digest -> refuse, keep existing row, record high-severity event."""
    out1, _f, _c, digest = _publish(cert_store)
    assert out1.outcome == "published_new"
    cert = cert_store["cert"]
    # craft a divergent candidate: same key/gen, tampered digest + facts
    facts = extract_state_facts(cert_store["parsed"], cert)
    cold = build_cold_payload(cert_store["parsed"], cert, facts)
    tampered = "f" * 64
    out2 = publish_one_shot(cert_store["store"], cert=cert, facts=facts,
                            cold_payload=cold, fact_digest=tampered, owner="w2")
    assert out2.outcome == "refused_conflict"
    # existing row UNCHANGED
    row = get_published_state(cert_store["store"], key=cert.tusemantic_key,
                              generation=cert.parser_generation)
    assert row["completeness_sha"] == digest
    # high-severity integrity event recorded
    assert integrity_event_count(cert_store["store"], key=cert.tusemantic_key,
                                 generation=cert.parser_generation) == 1
    conn = cert_store["store"]._conn
    ev = conn.execute("SELECT kind, existing_digest, candidate_digest FROM "
                      "integrity_events").fetchone()
    assert ev["kind"] == "same_key_different_digest"
    assert ev["existing_digest"] == digest and ev["candidate_digest"] == tampered


def test_s1_unique_key_generation_constraint(cert_store):
    out1, _f, _c, _d = _publish(cert_store)
    cert = cert_store["cert"]
    with pytest.raises(SQLITE_INTEGRITY_ERROR):
        cert_store["store"]._conn.execute(
            "INSERT INTO semantic_states(tusemantic_key, parser_generation, "
            "key_format, l0_source_blob_sha, l1_ncc_digest_sha, "
            "l2_closure_digest_sha, l3_env_digest_sha, publication, "
            "completeness_sha, digest_algorithm, fact_schema_version, "
            "payload_format, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cert.tusemantic_key, cert.parser_generation, "x", "0" * 64,
             "1" * 64, "2" * 64, "3" * 64, "published", "d", "alg", 1,
             "normalized", "now"))


def test_s1_certificate_rows_recorded(cert_store):
    """Every certification outcome (incl. poisons) lands in tu_certificates."""
    _out, _f, _c, digest = _publish(cert_store)
    conn = cert_store["store"]._conn
    row = conn.execute("SELECT status, outcome, tusemantic_key, l0_sha, "
                       "fact_set_digest, timing_json FROM tu_certificates"
                       ).fetchone()
    assert row["status"] == "certified"
    assert row["outcome"] in ("published_new", "converged")
    assert row["tusemantic_key"] == cert_store["cert"].tusemantic_key
    assert len(row["l0_sha"]) == 64
    assert row["fact_set_digest"] == digest
    assert json.loads(row["timing_json"])            # per-leg timings recorded
    # poison disposition recorded without any state row
    cert2 = cert_store["cert"]
    bad = cert2.__class__(
        cert_version=cert2.cert_version, status="poisoned",
        poison_reason="synthetic", inputs=None, key_result=None, closure=[],
        forced_legs=(), ncc_serial="", ncc_warnings=[], rsp_warnings=[],
        macro_env_digest="", macro_env_count=0,
        parser_identity=cert2.parser_identity, source_blob_sha="0" * 64,
        language="c", context_id="x", config_hash="y", main_file_display="m")
    insert_certificate_row(cert_store["store"], bad, outcome="poisoned",
                           fact_set_digest="")
    n = conn.execute("SELECT count(*) FROM tu_certificates WHERE "
                     "status='poisoned'").fetchone()[0]
    assert n == 1
    assert conn.execute("SELECT count(*) FROM semantic_states WHERE "
                        "tusemantic_key=''").fetchone()[0] == 0


# ── staging claim surface (D2 §5.2) ─────────────────────────────────────────

KEY_ARGS = dict(key="k-claim", generation=7, key_format="fwctx-tukb/v1.l2closure",
                l0="0" * 64, l1="1" * 64, l2="2" * 64, l3="3" * 64)


def test_s1_staging_claim_lifecycle(store):
    claim = stage_claim(store, fact_digest="d1", owner="w1", **KEY_ARGS)
    assert claim.outcome == "staged_new"
    # staging row is NOT visible as published (INV-8 gate)
    assert get_published_state(store, key="k-claim", generation=7) is None
    # foreign heartbeat refused
    assert heartbeat_claim(store, key="k-claim", generation=7, owner="w2") is False
    assert heartbeat_claim(store, key="k-claim", generation=7, owner="w1") is True
    # foreign ACTIVE claim -> staged_active for a second worker
    claim2 = stage_claim(store, fact_digest="d1", owner="w2", **KEY_ARGS)
    assert claim2.outcome == "staged_active"
    # same-digest completion by the owner flips staging -> published
    from fw_context_mcp.store_v2.publish import publish_staged_completion
    out = publish_staged_completion(store, key="k-claim", generation=7,
                                    fact_digest="d1", fact_row_count=3,
                                    owner="w1")
    assert out.outcome == "published_new"
    row = get_published_state(store, key="k-claim", generation=7)
    assert row is not None and row["publication"] == "published"
    assert row["owner_lease"] is None


def test_s1_stale_claim_recovery_after_lease_expiry(store):
    """S1-T12 crash story: worker1 claims, 'crashes'; after the lease deadline
    worker2 adopts the stale claim and completes publication."""
    claim = stage_claim(store, fact_digest="d1", owner="w1",
                        lease_seconds=0.05, **KEY_ARGS)
    assert claim.outcome == "staged_new"
    time.sleep(0.1)                                   # lease expires
    claim2 = stage_claim(store, fact_digest="d1", owner="w2", **KEY_ARGS)
    assert claim2.outcome == "staged_adopted"
    from fw_context_mcp.store_v2.publish import publish_staged_completion
    out = publish_staged_completion(store, key="k-claim", generation=7,
                                    fact_digest="d1", fact_row_count=1,
                                    owner="w2")
    assert out.outcome == "published_new"


def test_s1_stale_claim_divergent_digest_refused(store):
    """A stale claim carrying DIFFERENT facts must not be adopted blindly."""
    stage_claim(store, fact_digest="d-old", owner="w1",
                lease_seconds=0.05, **KEY_ARGS)
    time.sleep(0.1)
    claim2 = stage_claim(store, fact_digest="d-new", owner="w2", **KEY_ARGS)
    assert claim2.outcome == "staged_adopted"        # claim surface re-pointed
    from fw_context_mcp.store_v2.publish import publish_staged_completion
    # completing with the OLD digest mismatches the claim -> refused + event
    out = publish_staged_completion(store, key="k-claim", generation=7,
                                    fact_digest="d-old", fact_row_count=1,
                                    owner="w2")
    assert out.outcome == "refused_conflict"
    assert integrity_event_count(store, key="k-claim", generation=7) == 1
    assert get_published_state(store, key="k-claim", generation=7) is None


def test_s1_quarantined_refusal(store):
    cert = _fake_cert("k-q", 1)
    conn = store._conn
    conn.execute(
        "INSERT INTO semantic_states(tusemantic_key, parser_generation, "
        "key_format, l0_source_blob_sha, l1_ncc_digest_sha, "
        "l2_closure_digest_sha, l3_env_digest_sha, publication, "
        "completeness_sha, digest_algorithm, fact_schema_version, "
        "payload_format, created_at, quarantine_note) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cert.tusemantic_key, cert.parser_generation, "fwctx-tukb/v1.l2closure",
         "0" * 64, "1" * 64, "2" * 64, "3" * 64, "quarantined", "d", "alg", 1,
         "normalized", "now", "adjudicated"))
    conn.commit()
    out = publish_one_shot(store, cert=cert, facts=_fake_facts(),
                           cold_payload={"format": "x"}, fact_digest="d",
                           owner="w1")
    assert out.outcome == "refused_quarantined"
    assert get_published_state(store, key=cert.tusemantic_key,
                               generation=cert.parser_generation) is None
    assert integrity_event_count(store, key=cert.tusemantic_key,
                                 generation=cert.parser_generation) == 1


# ── concurrency ──────────────────────────────────────────────────────────────

def test_s1_busy_backoff_then_success(cert_store):
    """A writer holding the db does not fail publication (E2 backoff)."""
    v2_path = cert_store["store"]._conn.execute(
        "PRAGMA database_list").fetchone()[2]
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():                      # owns its own sqlite objects
        c = sqlite3.connect(v2_path, timeout=0.01, isolation_level=None)
        c.execute("PRAGMA busy_timeout=10")
        c.execute("BEGIN IMMEDIATE")
        c.execute("CREATE TABLE IF NOT EXISTS _hold(x)")
        holder_ready.set()
        release.wait(5.0)
        c.execute("ROLLBACK")
        c.close()

    th = threading.Thread(target=holder)
    th.start()
    holder_ready.wait(5.0)
    try:
        out, _f, _c, _d = _publish(cert_store)
        assert out.outcome == "published_new"
    finally:
        release.set()
        th.join()


def test_s1_concurrent_publishers_converge(cert_store):
    """Two threads publish the SAME state concurrently: exactly one
    published_new, the other converged — never two rows, never corruption."""
    cert = cert_store["cert"]
    facts = extract_state_facts(cert_store["parsed"], cert)
    cold = build_cold_payload(cert_store["parsed"], cert, facts)
    digest = fact_set_digest(facts, cold)
    v2_path = cert_store["store"]._conn.execute(
        "PRAGMA database_list").fetchone()[2]
    results = []

    def worker(owner):
        s = open_v2_store_for_write(v2_path)
        try:
            out = publish_one_shot(s, cert=cert, facts=facts, cold_payload=cold,
                                   fact_digest=digest, owner=owner)
            results.append(out.outcome)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(results) == ["converged", "published_new"]
    conn = cert_store["store"]._conn
    n = conn.execute("SELECT count(*) FROM semantic_states WHERE "
                     "tusemantic_key=? AND parser_generation=?",
                     (cert.tusemantic_key, cert.parser_generation)).fetchone()[0]
    assert n == 1


# ── legacy isolation / hook ──────────────────────────────────────────────────

@pytest.fixture()
def _v2_flags(monkeypatch):
    """Reset BOTH flag caches around each hook test."""
    import fw_context_mcp.indexer._unit_processor as _UP
    C.reset_flag_cache()
    _UP._reset_v2_flag_cache()
    yield
    C.reset_flag_cache()
    _UP._reset_v2_flag_cache()


def test_s1_hook_disabled_by_default(tmp_path, monkeypatch, _v2_flags):
    monkeypatch.delenv("FW_CONTEXT_SHADOW_V2", raising=False)
    try:
        from fw_context_mcp.config import load
        assert load().index.shadow_v2_enabled is False
        data = make_project(tmp_path)
        unit = build_units(data["proj"])[0]
        parsed = parse_unit(unit)
        out = C.certify_and_publish_for_unit(
            unit=unit, parsed=parsed, project_root=data["proj"],
            db_path=tmp_path / "legacy" / "pid" / "index.db")
        assert out is None
        assert not (tmp_path / "legacy" / "pid" / "index-v2.db").exists()
        # the _unit_processor flag gate itself is off without the env var
        import fw_context_mcp.indexer._unit_processor as _UP
        assert _UP._v2_shadow_flag_on() is False
    finally:
        C.reset_flag_cache()


def test_s1_hook_env_enabled_publishes(tmp_path, monkeypatch, _v2_flags):
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    import fw_context_mcp.indexer._unit_processor as _UP
    assert _UP._v2_shadow_flag_on() is True
    try:
        data = make_project(tmp_path)
        unit = build_units(data["proj"])[0]
        parsed = parse_unit(unit)
        db_path = tmp_path / "legacy" / "pid" / "index.db"
        out = C.certify_and_publish_for_unit(
            unit=unit, parsed=parsed, project_root=data["proj"],
            db_path=db_path)
        assert out is not None and out.status == "certified"
        v2 = tmp_path / "legacy" / "pid" / "index-v2.db"
        assert v2.exists()
        store = open_v2_store_for_write(v2)
        try:
            row = get_published_state(store, key=out.tusemantic_key,
                                      generation=out.parser_generation)
            assert row is not None and row["publication"] == "published"
        finally:
            store.close()
    finally:
        C.reset_flag_cache()


def test_s1_hook_failure_isolated_from_legacy(tmp_path, monkeypatch, _v2_flags):
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    try:
        data = make_project(tmp_path)
        unit = build_units(data["proj"])[0]
        parsed = parse_unit(unit)
        monkeypatch.setattr(C, "build_certificate",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("synthetic v2 failure")))
        out = C.certify_and_publish_for_unit(
            unit=unit, parsed=parsed, project_root=data["proj"],
            db_path=tmp_path / "legacy" / "pid" / "index.db")
        assert out is None                        # swallowed, legacy unaffected
    finally:
        C.reset_flag_cache()


def _init_project_id(proj: Path) -> str:
    """Minimal `fw-context init` equivalent: register a project id in the
    project config (same store the CLI writes)."""
    from fw_context_mcp.config.settings import _write_project_id, generate_project_id
    pid = generate_project_id()
    _write_project_id(proj, pid)
    return pid


def test_s1_full_pipeline_process_unit_publishes(tmp_path, monkeypatch, _v2_flags):
    """THE hook, end to end: a REAL `runner.run` indexing pass with the flag
    on publishes the certified state into the sibling v2 store; legacy rows
    are unaffected.  (In-process full pipeline: runner → _process_unit →
    hook → certify → publish.)"""
    monkeypatch.setenv("FW_CONTEXT_SHADOW_V2", "1")
    from fw_context_mcp.config import derive_project_id
    from fw_context_mcp.indexer import runner

    data = make_project(tmp_path)
    proj = data["proj"]
    _init_project_id(proj)
    index_dir = tmp_path / "index"
    pid = derive_project_id(proj)
    db_path = index_dir / pid / "index.db"

    config_hash = runner.run(
        compile_commands=proj / "compile_commands.json",
        db_path=db_path, project_root=proj, project_id=pid,
        index_refs=False, index_embeddings=False, analyze_symbols=False,
    )
    assert config_hash

    v2 = index_dir / pid / "index-v2.db"
    assert v2.exists(), f"v2 store missing after real index run: {v2}"
    store = open_v2_store_for_write(v2)
    try:
        conn = store._conn
        n_states = conn.execute(
            "SELECT count(*) FROM semantic_states WHERE "
            "publication='published'").fetchone()[0]
        assert n_states == 1
        state = conn.execute(
            "SELECT tusemantic_key, parser_generation, completeness_sha FROM "
            "semantic_states").fetchone()
        cert = conn.execute(
            "SELECT tusemantic_key, parser_generation, fact_set_digest, "
            "outcome FROM tu_certificates WHERE status='certified'"
        ).fetchone()
        assert cert["tusemantic_key"] == state["tusemantic_key"]
        assert cert["parser_generation"] == state["parser_generation"]
        assert cert["fact_set_digest"] == state["completeness_sha"]
        assert cert["outcome"] in ("published_new", "converged")
        # parser identity registry row (diagnostics) present
        assert conn.execute("SELECT count(*) FROM parser_generations"
                            ).fetchone()[0] == 1
    finally:
        store.close()
    # legacy store got its rows (v2 did not disturb the legacy write path)
    assert db_path.exists()
    import pysqlite3
    conn = pysqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
        assert n > 0
    finally:
        conn.close()


def test_s1_full_pipeline_flag_off_no_v2(tmp_path, monkeypatch, _v2_flags):
    """Flag off through the REAL pipeline: no v2 artifact of any kind."""
    monkeypatch.delenv("FW_CONTEXT_SHADOW_V2", raising=False)
    from fw_context_mcp.config import derive_project_id
    from fw_context_mcp.indexer import runner

    data = make_project(tmp_path)
    proj = data["proj"]
    _init_project_id(proj)
    index_dir = tmp_path / "index"
    pid = derive_project_id(proj)
    runner.run(
        compile_commands=proj / "compile_commands.json",
        db_path=index_dir / pid / "index.db", project_root=proj, project_id=pid,
        index_refs=False, index_embeddings=False, analyze_symbols=False,
    )
    v2_files = list(index_dir.rglob("index-v2.db*"))
    assert v2_files == [], f"unexpected v2 artifacts: {v2_files}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _fake_cert(key, generation):
    from types import SimpleNamespace

    from fw_context_mcp.store_v2.certify import CERT_VERSION, ParserIdentity
    ident = ParserIdentity(indexer_generation=1, libclang_id="l",
                           capture_binary_id="c", builtin_digest="b",
                           builtin_count=1, target_triple="t")
    return C.TUCertificate(
        cert_version=CERT_VERSION, status="certified", poison_reason="",
        inputs=None,
        key_result=SimpleNamespace(L0="0" * 64, L1="1" * 64, L2="2" * 64,
                                   L3="3" * 64, root=key, poisoned="",
                                   key_format="fwctx-tukb/v1.l2closure"),
        closure=[], forced_legs=(),
        ncc_serial="", ncc_warnings=[], rsp_warnings=[],
        macro_env_digest="", macro_env_count=0,
        parser_identity=ident,
        source_blob_sha="0" * 64, language="c", context_id="", config_hash="",
        main_file_display="m")


def _fake_facts():
    from fw_context_mcp.store_v2.facts import StateFacts
    return StateFacts(file_table=[(0, "main", "0" * 64)], symbols=[],
                      edges=[], conditions=[])
