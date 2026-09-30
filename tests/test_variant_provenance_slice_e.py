"""SLICE-E RED/GREEN oracle — T04 variant provenance (TEST_PLAN §5).

Strict chain: RED cases must fail at the integration base commit (d09eacb)
for the reasons documented on each test, then pass after the SLICE-E fix.
PASS pins (frozen cases marked ``pre_fix_status: PASS`` in T04 test_spec.yaml)
must hold at BOTH commits and pin the documented behavior so the provenance
work cannot silently change it.

Frozen cases implemented here (T04::test_spec.yaml):

RED (fail pre-fix, one documented reason each):
- F04_wildcard_results_carry_provenance_envelope — lookup_symbol/find_callers
  annotate variant/image but omit config_hash; get_source/read_file/
  get_symbol_context return ONE build unannotated (the ``scopes[0]`` collapse
  at ``mcp/shared/connection.py:180``), scope chosen arbitrarily among
  same-second builds.
- F04_scope_order_deterministic_on_tie — ``get_builds_for_scope`` breaks
  ``created_at`` ties by ``rowid`` (insertion order, process-dependent); the
  frozen oracle requires ``ORDER BY created_at DESC, config_hash``.
- F04_group_equivalent_variants_two_groups — the grouping API does not exist
  (no implementation in src).
- Variant switch records (dispatch RED) — the provisional in/out records do
  not exist; a call edge whose target set changes across variant builds
  leaves no per-context in/out evidence.
- Exact-USR resolution (dispatch RED) — ``qn_to_usr`` collapses overloads
  last-declared-wins (T05 research, symbols.py name index); the resolvers
  cannot express "ambiguous exact match" for variant-specific edges.

PASS pins / GREEN:
- F04_single_build_three_commands_collapse (PASS pin) — one build, three
  commands collapses to ONE config_hash; refs keep both branch edges.
- F04_usr_cross_build_stability (PASS pin) — USRs identical across builds.
- F04_fail_closed_variant_selection (PASS pin) — a variant declared in
  config but absent from the DB fails closed with an error record.
- F04_find_callers_no_ghost_caller — no ghost caller for the inactive branch
  (root cause fixed by SLICE-A; kept as the G4 cross-track guard at the
  query layer).
- F04_three_builds_distinct_config_hash / F04_incremental_full_equivalence_refs
  (GREEN) — the O_INCREMENTAL_FULL_EQ contracts.
- test_fresh_vs_incremental_variant_rows_equality (verify hook, inherits the
  O_INCREMENTAL_FULL_EQ gate) — variant rows (symbols, ctx_calls, switch
  records) are equal between a fresh three-build index and an incrementally
  refreshed one.

Isolation: every DB lives under ``tmp_path`` (module-scoped index dirs under
``tmp_path_factory``); the fixture project's ``.fw-context/config.toml``
redirects its index dir there.  The repo-root ``.fw-context`` DB is never
touched.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

PROJECT_ID = "t04f04fixture0a0b0c0d0e0f0a0b0c0d0e0f"

VARIANT_C = """void send_uart(void);
void send_usb(void);

int stable_value(void) { return 7; }

void transport_send(void) {
#ifdef IMPL_B
    send_usb();
#else
    send_uart();
#endif
}
"""

CONFIG_TMPL = """[project]
name = "t04-f04-fixture"
id = "{pid}"

[build]
[[build.variants]]
name = "A"
[[build.variants]]
name = "B"
[[build.variants]]
name = "C"

[index]
db_dir = "{db_dir}"
index_refs = true
index_embeddings = false

[llm]
enabled = false
"""


# ── fixture writers ──────────────────────────────────────────────────────────


def _write_fixture(root: Path, index_dir: Path) -> None:
    """variant.c + .fw-context/config.toml with variants A/B/C declared."""
    (root / ".fw-context").mkdir(parents=True, exist_ok=True)
    (root / "variant.c").write_text(VARIANT_C, encoding="utf-8")
    (root / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(pid=PROJECT_ID, db_dir=str(index_dir).replace("\\", "/")),
        encoding="utf-8",
    )


def _write_cc(root: Path, entries: list[list[str]]) -> Path:
    """compile_commands.json — one entry per item; each build gets its own."""
    fwd = str(root).replace("\\", "/")
    payload = [
        {
            "directory": fwd,
            "arguments": ["clang", "-std=c11", "-I.", *args, "-c", "variant.c", "-o", f"variant_{i}.o"],
            "file": "variant.c",
        }
        for i, args in enumerate(entries)
    ]
    cc = root / f"cc_{len(payload)}_{hash(tuple(tuple(e) for e in entries)) & 0xFFFF}.json"
    cc.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cc


# The per-variant single-command compile commands (F04 three-build shape).
def _cc_for_variant(root: Path, args: list[str]) -> Path:
    return _write_cc(root, [args])


# The single-build three-command compile commands (F04 A02-overlap shape).
_CC_ABC_ARGS: list[list[str]] = [["-DIMPL_B=1"], [], ["-DUNRELATED=123"]]


def _run_build(root: Path, cc: Path, index_dir: Path, variant: str) -> str:
    db_path = index_dir / PROJECT_ID / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return run(
        cc,
        db_path,
        project_root=root,
        project_id=PROJECT_ID,
        project_name="t04-f04-fixture",
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
        variant=variant,
    )


# ── module-scoped indexes ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def three_build(tmp_path_factory) -> dict:
    """F04 fixture indexed as three builds A/B/C into one DB (shared, read-only)."""
    root = tmp_path_factory.mktemp("f04_root")
    index_dir = tmp_path_factory.mktemp("f04_index")
    _write_fixture(root, index_dir)
    hashes = {}
    for label, args in (("A", []), ("B", ["-DIMPL_B=1"]), ("C", ["-DUNRELATED=123"])):
        hashes[label] = _run_build(root, _cc_for_variant(root, args), index_dir, label)
    return {
        "root": root,
        "index_dir": index_dir,
        "db_path": index_dir / PROJECT_ID / "index.db",
        "hashes": hashes,
    }


@pytest.fixture(scope="module")
def single_build_b(tmp_path_factory) -> dict:
    """F04 fixture with ONLY variant B indexed (DB lacks build A)."""
    root = tmp_path_factory.mktemp("f04_single_root")
    index_dir = tmp_path_factory.mktemp("f04_single_index")
    _write_fixture(root, index_dir)
    cc = _cc_for_variant(root, ["-DIMPL_B=1"])
    ch = _run_build(root, cc, index_dir, "B")
    return {
        "root": root,
        "db_path": index_dir / PROJECT_ID / "index.db",
        "hash_B": ch,
    }


def _ctx_of(db_path: Path, config_hash: str) -> str:
    """The (single) non-canonical context_id of a one-command build."""
    conn = open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT context_id FROM build_contexts WHERE config_hash=? AND context_id != ''",
            (config_hash,),
        ).fetchall()
        assert len(rows) == 1, f"expected one context for build, got {rows}"
        return rows[0][0]
    finally:
        conn.close()


# ══ RED 1 — F04_wildcard_results_carry_provenance_envelope ═══════════════════


def test_F04_wildcard_results_carry_provenance_envelope(three_build) -> None:
    """Every wildcard record carries variant + image + config_hash; source
    tools fan out one annotated record per scope.

    RED at d09eacb: lookup_symbol/find_callers omit config_hash; the three
    source tools return ONE unannotated dict (scopes[0] collapse at
    connection.py:180) instead of a per-scope list.
    """
    from fw_context_mcp.mcp.handlers._lookup import lookup_symbol
    from fw_context_mcp.mcp.handlers.callgraph import find_callers
    from fw_context_mcp.mcp.handlers.source import (
        get_source,
        get_symbol_context,
        read_file,
    )

    root = str(three_build["root"])

    def _annotated(recs: list) -> list[dict]:
        return [
            r
            for r in recs
            if isinstance(r, dict) and "error" not in r and "warning" not in r
        ]

    # lookup_symbol — list fan-out with full envelope
    recs = lookup_symbol(name="transport_send", project_root=root, variant="*")
    recs = _annotated(recs)
    assert recs, "lookup_symbol variant='*' returned no records"
    for r in recs:
        assert r.get("variant") is not None, f"lookup record lacks variant: {sorted(r)}"
        assert r.get("image") is not None, f"lookup record lacks image: {sorted(r)}"
        assert r.get("config_hash"), f"lookup record lacks config_hash: {sorted(r)}"
    assert {r["variant"] for r in recs} == {"A", "B", "C"}

    # find_callers — same envelope rule
    recs = _annotated(find_callers(name="send_uart", project_root=root, variant="*"))
    assert recs, "find_callers send_uart variant='*' returned no records"
    for r in recs:
        assert r.get("variant") is not None, f"caller record lacks variant: {sorted(r)}"
        assert r.get("image") is not None, f"caller record lacks image: {sorted(r)}"
        assert r.get("config_hash"), f"caller record lacks config_hash: {sorted(r)}"

    # Source tools — one annotated record per scope, as a LIST.
    gs = get_source(name="transport_send", project_root=root, variant="*")
    assert isinstance(gs, list), (
        f"get_source variant='*' must return a per-scope list, got {type(gs).__name__}"
    )
    gs = _annotated(gs)
    assert len(gs) == 3, f"expected one annotated record per scope, got {len(gs)}"
    for r in gs:
        assert r.get("variant") is not None and r.get("image") is not None
        assert r.get("config_hash"), f"get_source record lacks config_hash: {sorted(r)}"
    by_variant = {r["variant"]: r for r in gs}
    assert "send_uart" in (by_variant["A"].get("source") or "")
    assert "send_usb" not in (by_variant["A"].get("source") or "")
    assert "send_usb" in (by_variant["B"].get("source") or "")
    assert "send_uart" not in (by_variant["B"].get("source") or "")

    rf = read_file(file_path="variant.c", project_root=root, variant="*")
    assert isinstance(rf, list), (
        f"read_file variant='*' must return a per-scope list, got {type(rf).__name__}"
    )
    rf = _annotated(rf)
    assert len(rf) == 3, f"expected one annotated record per scope, got {len(rf)}"
    for r in rf:
        assert r.get("config_hash"), f"read_file record lacks config_hash: {sorted(r)}"
    rf_by_variant = {r["variant"]: r for r in rf}
    # The DECLARATION (line 2) is active in every build — the branch decides
    # only the indented CALL line inside transport_send's body.
    assert "\n    send_usb();" in (rf_by_variant["B"].get("content") or "")
    assert "\n    send_usb();" not in (rf_by_variant["A"].get("content") or "")

    sc = get_symbol_context(name="transport_send", project_root=root, variant="*")
    assert isinstance(sc, list), (
        f"get_symbol_context variant='*' must return a per-scope list, "
        f"got {type(sc).__name__}"
    )
    sc = _annotated(sc)
    assert len(sc) == 3
    for r in sc:
        assert r.get("config_hash"), f"get_symbol_context record lacks config_hash: {sorted(r)}"


# ══ RED 2 — F04_scope_order_deterministic_on_tie ═════════════════════════════


def test_F04_scope_order_deterministic_on_tie(three_build) -> None:
    """created_at ties break by config_hash, never by rowid (process order).

    RED at d09eacb: ORDER BY created_at DESC, rowid DESC — rowid is
    insertion order.  The tie probe row is inserted LAST with a
    lexicographically-minimal config_hash, so a rowid tie-break places it
    LAST while the frozen oracle (created_at DESC, config_hash) places it
    FIRST.  Deterministic in both directions.
    """
    from fw_context_mcp.indexer.db import get_builds_for_scope

    db_path: Path = three_build["db_path"]
    hashes = three_build["hashes"]
    conn = open_db(db_path)
    try:
        # Force the tie: every build shares one created_at second.
        conn.execute(
            "UPDATE build_configs SET created_at='2026-01-01T00:00:00' WHERE project_id=?",
            (PROJECT_ID,),
        )
        # Tie probe: lexicographically smallest hash, inserted last (max rowid).
        conn.execute(
            """INSERT INTO build_configs
                   (config_hash, project_id, created_at, compile_commands_path)
               VALUES (?, ?, '2026-01-01T00:00:00', 'tie_probe')""",
            ("0" * 64, PROJECT_ID),
        )
        conn.commit()
        try:
            rows = get_builds_for_scope(conn, PROJECT_ID, "*")
        finally:
            # Leave the shared module fixture as other tests expect it.
            conn.execute("DELETE FROM build_configs WHERE config_hash=?", ("0" * 64,))
            conn.commit()
    finally:
        conn.close()
    scope_hashes = [r["config_hash"] for r in rows]
    assert scope_hashes[0] == "0" * 64, (
        "created_at ties must break by config_hash ASC; got "
        f"{[h[:8] for h in scope_hashes]}"
    )
    assert scope_hashes[1:] == sorted(hashes.values()), (
        f"tie order must be config_hash ASC, got {[h[:8] for h in scope_hashes[1:]]}"
    )


# ══ RED 3 — F04_group_equivalent_variants_two_groups ═════════════════════════


def test_F04_group_equivalent_variants_two_groups(three_build) -> None:
    """Behavior-fingerprint grouping splits {A, C} from {B} with hash provenance.

    RED at d09eacb: no grouping implementation exists in src (ImportError).
    """
    from fw_context_mcp.indexer.db._variants import group_equivalent_variants

    db_path: Path = three_build["db_path"]
    conn = open_db(db_path)
    try:
        out = group_equivalent_variants(conn, "transport_send", project_id=PROJECT_ID)
    finally:
        conn.close()
    groups = out["groups"]
    assert len(groups) == 2, f"expected exactly two groups, got {len(groups)}"
    member_sets = sorted(tuple(sorted(g["variants"])) for g in groups)
    assert ("A", "C") in member_sets and ("B",) in member_sets, (
        f"groups must be {{A,C}} and {{B}}, got {member_sets}"
    )
    for g in groups:
        assert g.get("config_hashes"), f"group {g['variants']} lacks config_hash provenance"
        assert len(g["config_hashes"]) == len(g["variants"])


# ══ RED 4/5 — provisional variant switch records (dispatch) ══════════════════


def test_variant_switch_rows_three_in_three_out(three_build) -> None:
    """A call edge whose target set changes across variant builds leaves
    provisional per-context in/out records: 3 in + 3 out for the F04 fixture.

    RED at d09eacb: no such rows exist (no writer).

    Edge uart@10:  in {A, C}, out {B}.   Edge usb@8:  in {B}, out {A, C}.
    Every row carries its build context_id and a variant_switch source mark;
    confirmed here because both contexts of each differing variant pair
    observe the caller.
    """
    from fw_context_mcp.indexer.variant_switch import (
        REF_KIND_IN,
        REF_KIND_OUT,
        SOURCE_CONFIRMED,
    )

    db_path: Path = three_build["db_path"]
    hashes = three_build["hashes"]
    ctx = {label: _ctx_of(db_path, ch) for label, ch in hashes.items()}
    conn = open_db(db_path)
    try:
        rows = conn.execute(
            """SELECT config_hash, context_id, to_usr, from_line, from_usr,
                      ref_kind, source
               FROM refs WHERE ref_kind LIKE 'variant_switch%'
               ORDER BY from_line, ref_kind, context_id"""
        ).fetchall()
    finally:
        conn.close()
    assert rows, "no variant switch records — the provisional pass never ran"

    in_rows = [r for r in rows if r["ref_kind"] == REF_KIND_IN]
    out_rows = [r for r in rows if r["ref_kind"] == REF_KIND_OUT]
    assert len(in_rows) == 3, f"expected 3 provisional in-rows, got {len(in_rows)}"
    assert len(out_rows) == 3, f"expected 3 provisional out-rows, got {len(out_rows)}"

    def _label(context_id: str) -> str:
        for label, cid in ctx.items():
            if cid == context_id:
                return label
        raise AssertionError(f"unknown context_id {context_id}")

    for r in rows:
        assert r["source"] == SOURCE_CONFIRMED, (
            f"switch row at line {r['from_line']} should be confirmed "
            f"(both variant-pair contexts observe the caller), got {r['source']}"
        )
        assert r["context_id"], "switch rows must carry their context_id"
        assert r["from_usr"], "switch rows must carry the caller USR"

    uart_in = {_label(r["context_id"]) for r in in_rows if r["from_line"] == 10}
    uart_out = {_label(r["context_id"]) for r in out_rows if r["from_line"] == 10}
    usb_in = {_label(r["context_id"]) for r in in_rows if r["from_line"] == 8}
    usb_out = {_label(r["context_id"]) for r in out_rows if r["from_line"] == 8}
    assert uart_in == {"A", "C"} and uart_out == {"B"}, (uart_in, uart_out)
    assert usb_in == {"B"} and usb_out == {"A", "C"}, (usb_in, usb_out)


def test_variant_switch_unconfirmed_stays_provisional(three_build, tmp_path) -> None:
    """A switch whose variant-pair evidence is incomplete stays provisional.

    When one context of the differing variant pair no longer observes the
    caller (its symbols rows are gone — lost/incomplete context data), the
    pair cannot agree, and the recomputed records carry the provisional
    source mark instead of the confirmed one.  They stay visible in the DB
    and excluded from production ref queries either way.
    """
    from fw_context_mcp.indexer.variant_switch import (
        SOURCE_CONFIRMED,
        SOURCE_PROVISIONAL,
        record_variant_switches,
    )

    db_path: Path = three_build["db_path"]
    copy_db = tmp_path / "mutated" / PROJECT_ID / "index.db"
    copy_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db_path, copy_db)
    hashes = three_build["hashes"]
    ctx_b = _ctx_of(db_path, hashes["B"])

    conn = open_db(copy_db)
    try:
        # Simulate the B context losing its caller observation.
        conn.execute(
            "DELETE FROM symbols WHERE config_hash=? AND context_id=? AND name='transport_send'",
            (hashes["B"], ctx_b),
        )
        conn.commit()
        record_variant_switches(conn, project_id=PROJECT_ID)
        conn.commit()
        statuses = {
            r["ref_kind"]: r["source"]
            for r in conn.execute(
                "SELECT DISTINCT ref_kind, source FROM refs "
                "WHERE ref_kind LIKE 'variant_switch%'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert statuses, "switch records must remain visible in the DB"
    for kind, src in statuses.items():
        assert src == SOURCE_PROVISIONAL, (
            f"{kind}: incomplete variant-pair evidence must stay provisional "
            f"({SOURCE_PROVISIONAL}), got {src}"
        )
    assert SOURCE_CONFIRMED not in statuses.values()


def test_production_queries_exclude_variant_switch_rows(three_build) -> None:
    """Switch records are DB-visible annotation, never production query rows.

    The default ref query (no kind filter) excludes them; an explicit kind
    request reaches them (the comparison surface).
    """
    from fw_context_mcp.indexer.db import find_refs
    from fw_context_mcp.indexer.variant_switch import REF_KIND_IN, REF_KIND_OUT

    db_path: Path = three_build["db_path"]
    hash_a = three_build["hashes"]["A"]
    hash_b = three_build["hashes"]["B"]
    conn = open_db(db_path)
    try:
        default_rows = find_refs(conn, hash_a, "send_usb")
        assert not any(
            str(r["ref_kind"]).startswith("variant_switch") for r in default_rows
        ), "default find_refs must exclude variant switch rows"
        # Build A does not take send_usb — the explicit kind request reaches
        # the OUT record; the IN record belongs to build B.
        explicit = find_refs(conn, hash_a, "send_usb", ref_kind=[REF_KIND_OUT])
        assert explicit, "explicit kind request must reach the switch rows"
        assert {r["ref_kind"] for r in explicit} == {REF_KIND_OUT}
        in_b = find_refs(conn, hash_b, "send_usb", ref_kind=[REF_KIND_IN])
        assert in_b and {r["ref_kind"] for r in in_b} == {REF_KIND_IN}
    finally:
        conn.close()


# ══ RED 6 — exact-USR resolution for variant-specific edges (dispatch) ═══════


def test_exact_usr_resolution_rejects_ambiguous_exact_key() -> None:
    """qn_to_usr collapses overloads last-declared-wins (T05 research, the
    symbols.py name index).  For variant-specific edges the fallback
    resolvers must prefer an exact, UNAMBIGUOUS USR match: one candidate
    resolves, several candidates sharing the qualified name abstain
    (never a possibly-wrong edge).

    RED at d09eacb: the resolvers take no candidate map — the exact key
    silently returns the last-declared overload's USR.
    """
    from fw_context_mcp.indexer.symbols import _resolve_method_usr, _resolve_partial_qn

    qn_to_usr = {"ns::send": "USR_LAST_DECLARED"}
    qn_overloads = {"ns::send": ["USR_SEND_INT", "USR_SEND_CHAR"]}

    assert (
        _resolve_partial_qn("ns::send", qn_to_usr, qn_overloads=qn_overloads) is None
    ), "ambiguous exact qualified name must not fabricate an edge"
    assert (
        _resolve_method_usr("send", qn_to_usr, qn_overloads=qn_overloads) is None
    ), "ambiguous bare-name exact match must not fabricate an edge"

    single = {"f": ["USR_ONE"]}
    assert _resolve_partial_qn("f", {"f": "USR_ONE"}, qn_overloads=single) == "USR_ONE"
    assert _resolve_method_usr("f", {"f": "USR_ONE"}, qn_overloads=single) == "USR_ONE"


# ══ PASS pins (frozen cases documented as pre-fix PASS) ══════════════════════


def test_F04_single_build_three_commands_collapse(tmp_path) -> None:
    """PASS pin: one build over three commands collapses to ONE config_hash;
    refs keep both branch edges (per-context rows since SLICE-C); the
    canonical projection yields exactly one canonical transport_send.
    """
    root = tmp_path / "abc_root"
    index_dir = tmp_path / "abc_index"
    _write_fixture(root, index_dir)
    cc = _write_cc(root, _CC_ABC_ARGS)
    ch = _run_build(root, cc, index_dir, "AB3")
    conn = open_db(index_dir / PROJECT_ID / "index.db")
    try:
        n_hashes = conn.execute(
            "SELECT COUNT(*) FROM build_configs WHERE project_id=?", (PROJECT_ID,)
        ).fetchone()[0]
        assert n_hashes == 1, f"three commands must collapse to one config_hash, got {n_hashes}"
        edges = {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT from_line, to_usr FROM refs WHERE config_hash=? AND ref_kind='call'",
                (ch,),
            ).fetchall()
        }
        to_names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM symbols WHERE config_hash=? AND usr IN "
                "(SELECT to_usr FROM refs WHERE config_hash=? AND ref_kind='call')",
                (ch, ch),
            ).fetchall()
        }
        assert {"send_uart", "send_usb"} <= to_names, f"both branch edges must exist: {edges}"
        canonical = conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT usr FROM symbols
                   WHERE config_hash=? AND name='transport_send'
                   GROUP BY config_hash, usr
               )""",
            (ch,),
        ).fetchone()[0]
        assert canonical == 1, "the canonical projection yields one transport_send"
    finally:
        conn.close()


def test_F04_usr_cross_build_stability(three_build) -> None:
    """PASS pin: USRs are identical across builds A/B/C."""
    db_path: Path = three_build["db_path"]
    conn = open_db(db_path)
    try:
        by_name: dict[str, set[str]] = {}
        for name in ("stable_value", "transport_send", "send_uart", "send_usb"):
            rows = conn.execute(
                # send_uart/send_usb exist only as declarations — no
                # is_definition row; USR stability is about identity.
                "SELECT DISTINCT usr FROM symbols WHERE name=?", (name,)
            ).fetchall()
            by_name[name] = {r[0] for r in rows}
    finally:
        conn.close()
    for name, usrs in by_name.items():
        assert usrs, f"{name}: no symbol row found"
        assert len(usrs) == 1, f"{name}: USR must be identical across builds, got {usrs}"
        assert next(iter(usrs)).startswith("c:@F@"), f"{name}: USR shape {usrs}"


def test_F04_fail_closed_variant_selection(single_build_b) -> None:
    """PASS pin: a declared-but-unindexed variant fails closed with an error."""
    from fw_context_mcp.mcp.handlers.callgraph import find_callers

    out = find_callers(name="send_usb", project_root=str(single_build_b["root"]), variant="A")
    assert isinstance(out, list) and len(out) == 1, f"fail-closed shape: {out}"
    assert "error" in out[0], f"expected a fail-closed error, got {out}"
    assert "Variant 'A'" in out[0]["error"], out[0]["error"]


def test_F04_find_callers_no_ghost_caller(three_build) -> None:
    """G4 guard at the query layer: build A never calls send_usb.

    Root cause fixed by SLICE-A (raw-text fallback skipped ranges); kept
    green through INTEGRATE per TEST_PLAN §0.
    """
    from fw_context_mcp.mcp.handlers.callgraph import find_callers

    root = str(three_build["root"])
    out = find_callers(name="send_usb", project_root=root, variant="A")
    for r in out:
        if isinstance(r, dict) and "error" not in r and "warning" not in r:
            assert r.get("caller") != "transport_send", (
                f"ghost caller in build A: {r}"
            )


# ══ GREEN — O_INCREMENTAL_FULL_EQ contracts ══════════════════════════════════


def test_F04_three_builds_distinct_config_hash(three_build) -> None:
    """Three builds → three distinct config_hash values, variant stamped,
    per-build ifdef-filtered bodies."""
    db_path: Path = three_build["db_path"]
    hashes = three_build["hashes"]
    assert len(set(hashes.values())) == 3, f"hashes must be distinct: {hashes}"
    conn = open_db(db_path)
    try:
        stamps = {
            r["config_hash"]: (r["variant"], r["image"])
            for r in conn.execute(
                "SELECT config_hash, variant, image FROM build_configs WHERE project_id=?",
                (PROJECT_ID,),
            ).fetchall()
        }
        assert set(stamps.values()) == {("A", ""), ("B", ""), ("C", "")}, stamps
        bodies: dict[str, str] = {}
        for label, ch in hashes.items():
            rows = conn.execute(
                "SELECT source FROM symbols WHERE config_hash=? AND name='transport_send' "
                "AND is_definition=1",
                (ch,),
            ).fetchall()
            assert len(rows) >= 1
            bodies[label] = "\n".join(r[0] for r in rows)
    finally:
        conn.close()
    assert "send_uart" in bodies["A"] and "send_usb" not in bodies["A"]
    assert "send_usb" in bodies["B"] and "send_uart" not in bodies["B"]
    assert bodies["C"] == bodies["A"], "UNRELATED=123 must not change the body"


def _canonical_variant_snapshot(conn: sqlite3.Connection, config_hash: str) -> dict:
    """(variant -> {symbols, refs}) canonical map for the equality gates.

    Derived variant-switch rows are excluded from the ref comparison: they
    are recomputed per DB shape and compared by their own hook.

    ``context_id`` is normalized to a per-build ordinal: it is an opaque
    identity token H(config_hash, main_file, ...) (ADR-001), so the merged
    DB and the clean DB carry different values for the same context shape.
    The ordinal preserves every distinction while ignoring the token.
    """
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT context_id FROM build_contexts WHERE config_hash=? ORDER BY context_id",
            (config_hash,),
        ).fetchall()
    ]
    ordinal = {cid: i for i, cid in enumerate(ids)}

    def _norm(rows: list) -> list:
        out = []
        for r in rows:
            row = tuple(r)
            row = row[:-1] + (ordinal.get(row[-1], row[-1]),)
            out.append(row)
        return sorted(out)

    symbols = _norm(
        conn.execute(
            """SELECT usr, name, kind, line, is_definition, source, context_id
               FROM symbols WHERE config_hash=?""",
            (config_hash,),
        ).fetchall()
    )
    refs = _norm(
        conn.execute(
            """SELECT to_usr, from_file, from_line, from_usr, ref_kind, context_id
               FROM refs
               WHERE config_hash=? AND ref_kind NOT LIKE 'variant_switch%'""",
            (config_hash,),
        ).fetchall()
    )
    return {"symbols": symbols, "refs": refs}


def test_F04_incremental_full_equivalence_refs(tmp_path) -> None:
    """Merged A/B/C in one DB vs each variant alone in a clean DB: the
    canonicalized (variant -> {symbol rows, refs}) maps must be equal."""
    clean_root = tmp_path / "clean_root"
    _write_fixture(clean_root, tmp_path / "clean_index")
    clean_hashes = {}
    for label, args in (("A", []), ("B", ["-DIMPL_B=1"]), ("C", ["-DUNRELATED=123"])):
        single_index = tmp_path / f"clean_index_{label}"
        _write_fixture(clean_root, single_index)
        clean_hashes[label] = _run_build(clean_root, _cc_for_variant(clean_root, args), single_index, label)

    merged_hashes = three_build_hashes(tmp_path)
    conn_m = open_db(merged_hashes["db_path"])
    try:
        for label in ("A", "B", "C"):
            conn_c = open_db(tmp_path / f"clean_index_{label}" / PROJECT_ID / "index.db")
            try:
                merged = _canonical_variant_snapshot(conn_m, merged_hashes["hashes"][label])
                clean = _canonical_variant_snapshot(conn_c, clean_hashes[label])
            finally:
                conn_c.close()
            assert merged == clean, (
                f"variant {label}: merged index rows must equal clean-index rows"
            )
    finally:
        conn_m.close()


def three_build_hashes(tmp_path) -> dict:
    """A fresh three-build index in a private tmp dir (not the shared module fixture)."""
    root = tmp_path / "eq_root"
    index_dir = tmp_path / "eq_index"
    _write_fixture(root, index_dir)
    hashes = {}
    for label, args in (("A", []), ("B", ["-DIMPL_B=1"]), ("C", ["-DUNRELATED=123"])):
        hashes[label] = _run_build(root, _cc_for_variant(root, args), index_dir, label)
    return {"root": root, "db_path": index_dir / PROJECT_ID / "index.db", "hashes": hashes}


def test_ctx_calls_variant_column_same_src_context(three_build) -> None:
    """ctx_calls — the comparison surface: per-context call rows carrying the
    build's variant column, keyed on the same-src-context identity
    (config_hash, context_id, from_file, from_line, from_usr, to_usr);
    derived switch rows never appear.

    RED at d09eacb: the helper does not exist.
    """
    from fw_context_mcp.indexer.ops import ctx_calls

    db_path: Path = three_build["db_path"]
    hashes = three_build["hashes"]
    conn = open_db(db_path)
    try:
        rows = ctx_calls(conn)
        only_a = ctx_calls(conn, config_hash=hashes["A"])
    finally:
        conn.close()
    assert rows, "ctx_calls returned no rows"
    keys = {
        (r["config_hash"], r["context_id"], r["from_file"], r["from_line"], r["from_usr"], r["to_usr"])
        for r in rows
    }
    assert len(keys) == len(rows), "ctx_calls rows must be unique per same-src-context key"
    for r in rows:
        assert r["variant"] in {"A", "B", "C"}, f"variant column missing: {r}"
        assert r["image"] == ""
    assert {r["variant"] for r in rows} == {"A", "B", "C"}
    assert all(r["config_hash"] == hashes["A"] for r in only_a)
    by_variant = {
        v: {(r["from_line"], r["to_usr"].rsplit("@", 1)[-1]) for r in rows if r["variant"] == v}
        for v in ("A", "B", "C")
    }
    assert all(line == 10 for line, _ in by_variant["A"]), by_variant["A"]
    assert all(line == 8 for line, _ in by_variant["B"]), by_variant["B"]
    assert by_variant["C"] == by_variant["A"], "C must equal A"


def test_fresh_vs_incremental_variant_rows_equality(tmp_path) -> None:
    """Verify hook (O_INCREMENTAL_FULL_EQ, dispatch): a build's variant rows
    are equal between the fresh index and an incrementally refreshed one —
    symbols, ctx_calls, and the derived variant-switch records.

    Same fixture root and compile commands throughout: the snapshot is
    taken after the fresh A/B/C runs, then build A's TU is touched (mtime
    Tier-1 staleness) and re-run, and the post-refresh state must match.
    """
    import os

    from fw_context_mcp.indexer.ops import ctx_calls

    root = tmp_path / "incr_root"
    index_dir = tmp_path / "incr_index"
    _write_fixture(root, index_dir)
    hashes = {}
    for label, args in (("A", []), ("B", ["-DIMPL_B=1"]), ("C", ["-DUNRELATED=123"])):
        hashes[label] = _run_build(root, _cc_for_variant(root, args), index_dir, label)

    def _snapshot(ch: str) -> dict:
        conn = open_db(index_dir / PROJECT_ID / "index.db")
        try:
            sym = sorted(
                tuple(r)
                for r in conn.execute(
                    """SELECT usr, name, kind, line, is_definition, source, context_id
                       FROM symbols WHERE config_hash=?""",
                    (ch,),
                ).fetchall()
            )
            calls = ctx_calls(conn, config_hash=ch)
            switches = sorted(
                tuple(r)
                for r in conn.execute(
                    """SELECT config_hash, context_id, to_usr, from_file, from_line,
                              from_usr, ref_kind, source
                       FROM refs WHERE ref_kind LIKE 'variant_switch%'"""
                ).fetchall()
            )
        finally:
            conn.close()
        return {"symbols": sym, "ctx_calls": calls, "switches": switches}

    fresh = {label: _snapshot(hashes[label]) for label in ("A", "B", "C")}
    assert fresh["A"]["switches"], "the fresh index must carry switch records"

    # Incremental refresh of build A — genuinely re-parsed via mtime.
    src = root / "variant.c"
    now = src.stat().st_mtime
    os.utime(src, (now + 5.0, now + 5.0))
    rerun = _run_build(root, _cc_for_variant(root, []), index_dir, "A")
    assert rerun == hashes["A"], "re-running build A must reuse its config_hash"

    incremental = {label: _snapshot(hashes[label]) for label in ("A", "B", "C")}
    for label in ("A", "B", "C"):
        assert incremental[label]["symbols"] == fresh[label]["symbols"], (
            f"variant {label}: symbol rows diverged after incremental refresh"
        )
        assert incremental[label]["ctx_calls"] == fresh[label]["ctx_calls"], (
            f"variant {label}: ctx_calls diverged after incremental refresh"
        )
    assert incremental["A"]["switches"] == fresh["A"]["switches"], (
        "variant switch records diverged after incremental refresh"
    )
