"""SLICE-H fp-evidence oracle (T14 TEST_PLAN §8).

Focused tests for the function-pointer evidence domain (T06, ADR-004 layer 3).
Case ids mirror the frozen adjudication oracles:

RED (must fail pre-fix, pass post-fix):
- T06::TS06_01_f06_chains — chains resolve end-to-end AND every
  fp_assignments / indirect_call_sites row carries non-empty certainty +
  evidence (ADR-004: "no more evidence-free rows").
- T06::TS06_04_fn_ptr_type_fallback_reachable — the Phase-3c fn_ptr_type
  fallback must run for rows whose LHS has no symbol row (the research-documented
  dead path: the early-continue in ``db/_refs.py::find_indirect_targets`` skips
  Phase 3c entirely); the resolved row carries certainty='possible' +
  resolution_kind='fn_ptr_type_candidate' + a DB-layer ``_note``.
- Wire seam of T06::TS06_03_note_passthrough (formal wire verification stays in
  SLICE-N): a DB-layer ``_note`` must reach the MCP handler output verbatim —
  never silently dropped at ``mcp/handlers/callgraph.py``.
- T06::TS06_08_flow_insensitive_marking (GREEN case, asserted here too) —
  multiple AST assignments to one slot resolve to one call site as a
  flow-insensitive may-target set: certainty='possible' + per-assignment
  ``order_index``.

GREEN-case guard:
- T06::TS06_09_incremental_full_eq — incremental refresh of one TU leaves the
  fp tables of untouched TUs byte-identical (guards the no-UNIQUE
  delete/reinsert path of indirect_call_sites).

Cross-check (already GREEN at the SLICE-H base via SLICE-A):
- T11::fp_assignment_parameter_rhs_gap — extraction fix landed in SLICE-A
  (TEST_PLAN §8: "extraction fix lives here; T11 re-runs it as cross-check");
  re-run as part of the SLICE-H focused set.

Adversarial (ADR-004 AST-proven boundary):
- an fp assignment whose call site cannot be proven yields
  certainty='unresolved' with NO invented edge and NO fabricated call site.

Fixtures are materialized under pytest ``tmp_path`` (a %TEMP% subdir); every
index DB lives under that tmp tree via the project ``config.toml`` ``db_dir``.
The repository ``.fw-context`` and the global ``~/.fw-context`` registry are
never touched.

NOTE: deliberately NOT marked ``libclang`` — the conftest auto-skip keys on
gcc, which is absent on this host; the pip libclang wheel is self-sufficient.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from fw_context_mcp.indexer.compile_commands import CompilationUnit
from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run
from fw_context_mcp.indexer.symbols import extract_all

PROJECT_ID = "t06fpevidence0a0b0c0d0e0f0a0b0c0d0e0f"

# ── Frozen fixtures ─────────────────────────────────────────────────────────

# F06-shaped chains: var_init, init_list, call_arg, opaque parameter call.
# Line map is frozen and asserted below.
CHAINS_C = """\
typedef void (*cb_t)(int);
static void cb_a(int x) { (void)x; }
static void cb_b(int x) { (void)x; }

static cb_t global_cb = cb_a;

struct ops { void (*write)(int); };
static struct ops dev_ops = { .write = cb_b };

static void register_cb(cb_t cb);

void fire_global(int x) { global_cb(x); }
void fire_write(int x) { dev_ops.write(x); }
void do_register(void) { register_cb(cb_a); }
void opaque(cb_t cb, int x) { cb(x); }
"""
CHAINS_FIRE_GLOBAL_LINE = 12
CHAINS_FIRE_WRITE_LINE = 13
CHAINS_DO_REGISTER_LINE = 14

# Phase-3c reachability: the register_cb call_arg row's LHS is a PARM_DECL
# that has no symbols row, so the pre-fix early-continue skips Phase 3c and
# the row resolves to nothing (no call site, no note, no uncertainty mark).
REACH_C = """\
typedef void (*cb_t)(int);
static void worker(int x) { (void)x; }
static void register_cb(cb_t cb);
static cb_t slot_a;
void setup_a(void) { slot_a = worker; }
void do_register(void) { register_cb(worker); }
void fire_b(int x) { slot_a(x); }
"""
REACH_FIRE_B_LINE = 7

# Flow-insensitive may-target: two AST assignments to one slot, one call site.
MAYTARGET_C = """\
typedef void (*cb_t)(int);
static void tgt_first(int x) { (void)x; }
static void tgt_second(int x) { (void)x; }
static cb_t selector;
void assign_first(void) { selector = tgt_first; }
void assign_second(void) { selector = tgt_second; }
void fire(int x) { selector(x); }
"""
MAYTARGET_FIRE_LINE = 7

# F2 shape (typematch3): assignment through boxA's field, invocation through
# boxB's field — the USR join fails and only the fn_ptr_type fallback can
# connect them, which must carry a DB-layer _note that survives the wire.
NOTE_C = """\
typedef void (*cb_t)(int);
static void worker(int x) { (void)x; }
struct boxA { cb_t slot; };
struct boxB { cb_t slot; };
struct boxA a1;
struct boxB b1;
void assign_a(void) { a1.slot = worker; }
void fire_b(int x) { b1.slot(x); }
"""

# ADR-004 boundary: fn-ptr parameter RHS recorded (SLICE-A), the slot is
# never invoked in-index — nothing may invent a call site for it.
PARAM_NO_CALL_C = """\
typedef struct { void (*push)(int); } Pipe;
static void sink(int x) { (void)x; }
void route(Pipe g_pipe, void (*cb)(int)) {
    g_pipe.push = cb;
}
"""

# Type-erased ISR registration (raw-text fallback): the isr_vec row is not
# AST-proven and must be marked certainty='possible'.
ISR_VEC_C = """\
typedef void (*isr_t)(void);
static void my_isr(void) { volatile int z = 1; (void)z; }
#define IRQ_X 0
void install(void) {
    NVIC_SetVector(IRQ_X, (uint32_t)my_isr);
}
"""

# Second TU for the incremental-equivalence guard.
SECOND_TU_C = """\
typedef void (*cb_t)(int);
static void t1(int x) { (void)x; }
static void t2(int x) { (void)x; }
static cb_t sel;
void a1st(void) { sel = t1; }
void a2nd(void) { sel = t2; }
void fire_sel(int x) { sel(x); }
"""


# ── Harness ────────────────────────────────────────────────────────────────

CONFIG_TMPL = """\
[project]
name = "t06-fp-evidence"
id = "{pid}"

[index]
db_dir = "{db_dir}"
index_refs = true
index_embeddings = false

[llm]
enabled = false
"""


def _write_cc(root: Path, files: list[str]) -> Path:
    fwd = str(root).replace("\\", "/")
    payload = [
        {
            "directory": fwd,
            "file": name,
            "arguments": ["clang", "-std=c11", "-c", name, "-o", f"build/o{i}.o"],
        }
        for i, name in enumerate(files)
    ]
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload), encoding="utf-8")
    return cc


def _make_project(tmp_path: Path, name: str, files: dict[str, str]):
    """Fixture project + isolated index dir + full index → (root, db_path, hash)."""
    root = tmp_path / name
    root.mkdir()
    index_dir = tmp_path / f"{name}_index"
    for fname, text in files.items():
        (root / fname).write_text(text, encoding="utf-8")
    cc = _write_cc(root, list(files))
    (root / ".fw-context").mkdir()
    (root / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(pid=PROJECT_ID, db_dir=str(index_dir).replace("\\", "/")),
        encoding="utf-8",
    )
    db_path = index_dir / PROJECT_ID / "index.db"
    ch = run(
        cc,
        db_path,
        project_root=root,
        project_id=PROJECT_ID,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
    )
    return root, db_path, ch


def _fpa_rows(conn: sqlite3.Connection, ch: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fp_assignments WHERE config_hash=? ORDER BY from_file, from_line, rhs_name",
        (ch,),
    ).fetchall()


def _ics_rows(conn: sqlite3.Connection, ch: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM indirect_call_sites WHERE config_hash=? ORDER BY from_file, from_line, target_name",
        (ch,),
    ).fetchall()


def _extract(tmp_path: Path, tag: str, files: dict[str, str]):
    d = tmp_path / tag
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    main = next(name for name in files if not name.endswith(".h"))
    unit = CompilationUnit(file=d / main, directory=d, language="c",
                           clang_args=["-std=c11"])
    return extract_all(unit, with_refs=True)


# ── T06::TS06_01_f06_chains (evidence columns populated, chains resolve) ────

def test_t06_ts06_01_f06_chains(tmp_path):
    """F06 chains resolve end-to-end at the DB layer AND every fp row carries
    resolution evidence + confidence per ADR-004 (no more evidence-free rows)."""
    _, db_path, ch = _make_project(tmp_path, "chains", {"chains.c": CHAINS_C})
    conn = open_db(db_path)
    try:
        fpa = _fpa_rows(conn, ch)
        ics = _ics_rows(conn, ch)
        assert fpa, "no fp_assignments rows indexed"
        assert ics, "no indirect_call_sites rows indexed"

        # ── ADR-004 layer 3: no evidence-free rows ──────────────────────
        for r in fpa:
            assert r["certainty"] in ("exact", "possible"), (
                f"fp_assignments certainty unpopulated at {r['from_file']}:{r['from_line']}: "
                f"{r['certainty']!r} (row: {dict(r)})")
            assert r["evidence"], (
                f"fp_assignments evidence unpopulated at {r['from_file']}:{r['from_line']}: "
                f"{r['evidence']!r} (row: {dict(r)})")
        for r in ics:
            assert r["certainty"] in ("exact", "possible"), (
                f"indirect_call_sites certainty unpopulated at {r['from_file']}:{r['from_line']}: "
                f"{r['certainty']!r} (row: {dict(r)})")
            assert r["evidence"], (
                f"indirect_call_sites evidence unpopulated at {r['from_file']}:{r['from_line']}: "
                f"{r['evidence']!r} (row: {dict(r)})")

        # ── direct AST assignment / init-list / call-argument evidence ──
        var_init = [r for r in fpa if r["method"] == "var_init" and r["lhs_name"] == "global_cb"]
        assert var_init, f"var_init row for global_cb missing: {[dict(r) for r in fpa]}"
        assert var_init[0]["certainty"] == "exact" and var_init[0]["evidence"] == "ast", (
            f"var_init row evidence wrong: {dict(var_init[0])}")
        init_list = [r for r in fpa if r["method"] == "init_list" and r["rhs_name"] == "cb_b"]
        assert init_list, f"init_list row for cb_b missing: {[dict(r) for r in fpa]}"
        assert init_list[0]["certainty"] == "exact" and init_list[0]["evidence"] == "ast", (
            f"init_list row evidence wrong: {dict(init_list[0])}")
        call_arg = [r for r in fpa if r["method"] == "call_arg"
                    and r["from_usr"] and r["from_line"] == CHAINS_DO_REGISTER_LINE]
        assert call_arg, f"call_arg row at do_register missing: {[dict(r) for r in fpa]}"
        assert call_arg[0]["certainty"] == "exact" and call_arg[0]["evidence"] == "ast", (
            f"call_arg row evidence wrong: {dict(call_arg[0])}")

        # ── end-to-end chains (frozen F06 line map) ─────────────────────
        from fw_context_mcp.indexer.db import find_indirect_targets

        rows = find_indirect_targets(conn, ch, "global_cb")
        chain = [r for r in rows if r["method"] == "var_init"]
        assert chain, f"no chain row for global_cb: {rows}"
        assert chain[0]["call_file"] and chain[0]["call_file"].endswith("chains.c"), (
            f"global_cb chain unresolved: {dict(chain[0])}")
        assert chain[0]["call_line"] == CHAINS_FIRE_GLOBAL_LINE, (
            f"global_cb chain call line wrong: {dict(chain[0])}")
        assert chain[0]["certainty"] == "exact", (
            f"single-target exact join must be certainty='exact': {dict(chain[0])}")
        assert chain[0]["resolution_kind"] == "usr_join", (
            f"exact join must be resolution_kind='usr_join': {dict(chain[0])}")

        rows_w = find_indirect_targets(conn, ch, "write")
        chain_w = [r for r in rows_w if r["method"] == "init_list"]
        assert chain_w, f"no chain row for write field: {rows_w}"
        assert chain_w[0]["call_line"] == CHAINS_FIRE_WRITE_LINE, (
            f"write chain call line wrong: {dict(chain_w[0])}")
    finally:
        conn.close()


# ── T06::TS06_04_fn_ptr_type_fallback_reachable ─────────────────────────────

def test_t06_ts06_04_fn_ptr_type_fallback_reachable(tmp_path):
    """Phase 3c must run for rows whose LHS has no symbols row.  The
    ``register_cb(worker)`` fp row's LHS is a PARM_DECL: pre-fix the
    early-continue in ``find_indirect_targets`` skips Phase 3c entirely
    (the research-documented dead path), so the row resolves to nothing —
    no call site, no uncertainty mark.  Post-fix it resolves through the
    fn_ptr_type match as an honestly-marked candidate."""
    _, db_path, ch = _make_project(tmp_path, "reach", {"reach.c": REACH_C})
    conn = open_db(db_path)
    try:
        from fw_context_mcp.indexer.db import find_indirect_targets

        rows = find_indirect_targets(conn, ch, "worker")
        by_method = {}
        for r in rows:
            by_method.setdefault(r["method"], []).append(r)
        assert "call_arg" in by_method, (
            f"register_cb call_arg row missing from results: {rows}")

        cand = by_method["call_arg"][0]
        # Phase 3c reachability: the type fallback must produce a call site.
        assert cand["call_file"] is not None, (
            "Phase 3c fn_ptr_type fallback unreachable — PARM_DECL-lhs row has "
            f"no call site and no uncertainty mark: {cand}")
        assert cand["certainty"] == "possible", (
            f"type-fallback chain must be certainty='possible': {cand}")
        assert cand["resolution_kind"] == "fn_ptr_type_candidate", (
            f"type-fallback chain must be resolution_kind='fn_ptr_type_candidate': {cand}")
        assert "_note" in cand and "fn_ptr_type" in str(cand["_note"]), (
            f"Phase 3c row must carry the DB-layer _note: {cand}")

        # Control: the exact USR join (slot_a = worker → slot_a(x)) is exact.
        assert "assignment" in by_method, (
            f"slot_a assignment row missing: {rows}")
        exact = by_method["assignment"][0]
        assert exact["call_line"] == REACH_FIRE_B_LINE, (
            f"exact join must resolve to fire_b: {dict(exact)}")
        assert exact["certainty"] == "exact", (
            f"exact join must stay certainty='exact': {dict(exact)}")
        assert exact["resolution_kind"] == "usr_join", (
            f"exact join must be resolution_kind='usr_join': {dict(exact)}")
    finally:
        conn.close()


# ── T06::TS06_08_flow_insensitive_marking ───────────────────────────────────

def test_t06_ts06_08_flow_insensitive_marking(tmp_path):
    """Two AST assignments to one slot, one call site: the may-target set is
    reported with certainty='possible' (never silently exact) and each row
    carries its per-assignment ``order_index`` so 'assigned after call' is
    answerable.  order_index stays within the fp tables (ADR-004)."""
    _, db_path, ch = _make_project(tmp_path, "maytarget", {"maytarget.c": MAYTARGET_C})
    conn = open_db(db_path)
    try:
        fpa = [r for r in _fpa_rows(conn, ch) if r["lhs_name"] == "selector"]
        assert len(fpa) == 2, (
            f"expected 2 selector assignment rows: {[dict(r) for r in fpa]}")
        ois = sorted(r["order_index"] for r in fpa)
        assert ois == [0, 1], (
            f"fp_assignments.order_index must be the per-slot assignment ordinal "
            f"0,1 — got {ois} ({[dict(r) for r in fpa]})")

        from fw_context_mcp.indexer.db import find_indirect_targets

        rows = find_indirect_targets(conn, ch, "selector")
        assert len(rows) == 2, f"expected both may-targets: {rows}"
        for r in rows:
            assert r["call_line"] == MAYTARGET_FIRE_LINE, (
                f"both targets join the same call site: {dict(r)}")
            assert r["certainty"] == "possible", (
                f"flow-insensitive may-target must be certainty='possible': {dict(r)}")
        assert sorted(r["order_index"] for r in rows) == [0, 1], (
            f"query rows must carry the fp order_index through: "
            f"{[dict(r) for r in rows]}")
    finally:
        conn.close()


# ── Wire seam of T06::TS06_03_note_passthrough (formal check stays in N) ────

def test_note_preserved_through_wire_seam(tmp_path):
    """The DB-layer ``_note`` set by the fn_ptr_type fallback must reach the
    MCP handler output verbatim — never silently dropped at the wire
    (handlers/callgraph.py rebuilds output dicts)."""
    root, db_path, ch = _make_project(tmp_path, "note", {"note.c": NOTE_C})
    conn = open_db(db_path)
    try:
        from fw_context_mcp.indexer.db import find_indirect_targets

        db_rows = find_indirect_targets(conn, ch, "slot")
        noted = [r for r in db_rows if r.get("_note")]
        assert noted, (
            "precondition: the DB layer must set _note for the boxA→boxB "
            f"fn_ptr_type fallback; DB rows: {db_rows}")
    finally:
        conn.close()

    from fw_context_mcp.mcp.handlers.callgraph import find_indirect_targets as wire

    out = wire(name="slot", project_root=str(root))
    entries = [e for e in out if "error" not in e and "info" not in e]
    assert entries, f"wire returned no entries: {out}"
    noted_wire = [e for e in entries if e.get("_note")]
    assert noted_wire, (
        "DB-layer _note was dropped at the wire: handler output entries carry "
        f"no _note although the DB layer set it — entries: {entries}")


# ── Adversarial: unprovable call sites are never invented ───────────────────

def test_adversarial_unprovable_param_call_site_not_invented(tmp_path):
    """ADR-004 AST-proven boundary: ``g_pipe.push = cb`` records the fp row;
    the parameter is invoked nowhere in the index, so the chain must report
    certainty='unresolved' with call_file=None and NO invented edge — no
    refs row to the parameter, no fabricated call site."""
    _, db_path, ch = _make_project(tmp_path, "param", {"param.c": PARAM_NO_CALL_C})
    conn = open_db(db_path)
    try:
        from fw_context_mcp.indexer.db import find_indirect_targets

        rows = find_indirect_targets(conn, ch, "push")
        assert rows, "push assignment row missing entirely"
        for r in rows:
            assert r["call_file"] is None, (
                f"call site invented for a never-invoked parameter: {dict(r)}")
            assert r["certainty"] == "unresolved", (
                f"unprovable chain must be certainty='unresolved': {dict(r)}")
            assert r["resolution_kind"] == "unresolved", (
                f"unprovable chain must be resolution_kind='unresolved': {dict(r)}")
        param_usrs = [r["rhs_usr"] for r in rows if r["rhs_usr"]]
        invented = conn.execute(
            """SELECT COUNT(*) FROM refs
               WHERE config_hash=? AND ref_kind IN ('call', 'indirect')
                 AND to_usr IN (SELECT rhs_usr FROM fp_assignments
                                WHERE config_hash=? AND rhs_usr NOT IN
                                      (SELECT usr FROM symbols WHERE config_hash=?))""",
            (ch, ch, ch),
        ).fetchone()[0]
        assert invented == 0, (
            f"{invented} refs edges invented for non-symbol (parameter) fp targets: "
            f"param usrs {param_usrs}")
    finally:
        conn.close()


# ── Type-erased ISR row is not AST-proven (extraction level) ────────────────

def test_fp_evidence_isr_vec_row_is_possible(tmp_path):
    """The raw-text isr_vec path is not AST-proven: its fp_assignments row
    must carry certainty='possible' + evidence='source_line_fallback'."""
    r = _extract(tmp_path, "isr_vec", {"isr_vec.c": ISR_VEC_C})
    isr_rows = [f for f in r.fp_assignments if f.method == "isr_vec"]
    assert isr_rows, (
        "isr_vec fp row missing; have: "
        f"{[(f.method, f.rhs_name) for f in r.fp_assignments]}")
    row = isr_rows[0]
    assert row.certainty == "possible", (
        f"isr_vec row must be certainty='possible' (raw-text fallback): {row}")
    assert row.evidence == "source_line_fallback", (
        f"isr_vec row evidence must name the raw-text fallback: {row}")


# ── T06::TS06_09_incremental_full_eq (fp tables) ────────────────────────────

def test_t06_ts06_09_incremental_full_eq_fp_tables(tmp_path):
    """Incremental refresh of one TU leaves the fp rows of the untouched TU
    byte-identical; a clean full re-index reproduces the exact same rows
    (guards the delete-before-insert path of the fp tables)."""
    root, db_path, ch = _make_project(
        tmp_path, "incr", {"a.c": CHAINS_C, "b.c": SECOND_TU_C})

    def _snapshot() -> dict:
        conn = open_db(db_path)
        try:
            fpa = sorted(
                tuple(r)
                for r in conn.execute(
                    """SELECT config_hash, context_id, from_file, from_line, lhs_usr,
                              lhs_name, rhs_usr, rhs_name, fn_ptr_type, method, from_usr,
                              certainty, evidence, order_index
                       FROM fp_assignments WHERE config_hash=?""",
                    (ch,),
                ).fetchall()
            )
            ics = sorted(
                tuple(r)
                for r in conn.execute(
                    """SELECT config_hash, context_id, from_file, from_line, from_usr,
                              expr_text, target_usr, target_name, fn_ptr_type,
                              certainty, evidence, order_index
                       FROM indirect_call_sites WHERE config_hash=?""",
                    (ch,),
                ).fetchall()
            )
        finally:
            conn.close()
        return {"fpa": fpa, "ics": ics}

    fresh = _snapshot()
    assert fresh["fpa"] and fresh["ics"], "fp tables empty after full index"

    # Incremental refresh of a.c only (mtime Tier-1 staleness).
    src = root / "a.c"
    now = src.stat().st_mtime
    os.utime(src, (now + 5.0, now + 5.0))
    cc = root / "compile_commands.json"
    rerun = run(cc, db_path, project_root=root, project_id=PROJECT_ID,
                index_refs=True, index_embeddings=False, analyze_symbols=False)
    assert rerun == ch, "re-running must reuse the config_hash"
    incremental = _snapshot()
    assert incremental == fresh, (
        "fp rows diverged after incremental refresh of one TU")

    # Clean full re-index from an empty DB.
    db_path.unlink()
    run(cc, db_path, project_root=root, project_id=PROJECT_ID,
        index_refs=True, index_embeddings=False, analyze_symbols=False)
    clean = _snapshot()
    assert clean == fresh, "clean full re-index diverged from the fresh index"
