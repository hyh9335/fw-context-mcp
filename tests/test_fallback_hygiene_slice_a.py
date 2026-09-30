"""SLICE-A fallback-hygiene oracle (T14 TEST_PLAN §1).

Focused tests for the shared fallback cluster of the reference extractor
(``src/fw_context_mcp/indexer/symbols.py``).  Case ids mirror the frozen
adjudication oracles (``.fw-orchestrator/artifacts/<T>/test_spec.yaml``):

RED (must fail pre-fix, pass post-fix):
- T04::F04_no_inactive_branch_call_refs
- T05::T05_inactive_branch_no_ghost_edges
- T06::TS06_02_inactive_branch_no_call_edge
- T06::TS06_05_macro_no_false_edges
- T06::TS06_06_tentative_slot_indexed
- T07::TC07-02_ghost_if0_edge_false_negative
- T07::TC07-07_self_ghost_on_attribute_function
- T08::t08_phantom_dispatch_edge_from_ifdef0_line
Extra (SLICE-A scope: fn-pointer parameter RHS in fp_assignments):
- T11::fp_assignment_parameter_rhs_gap

REGRESSION (run separately, frozen command):
- T05::T05_regression_call_path_dedup → ``python -m pytest
  tests/test_call_path_dedup.py -q --no-header`` (6 passed)

Fixtures are verbatim copies of the adjudication fixtures (T04
fixture_f04/variant.c, T06 fp_probe/{inactive2,macro_calls,typematch}.c,
T07 fixture/dead.c, T08 fixtures_snapshot/F10_dead_branch/*).  They are
materialized under pytest ``tmp_path`` (a %TEMP% subdir), never inside the
repository or the global ~/.fw-context registry.

NOTE: deliberately NOT marked ``libclang`` — the conftest auto-skip keys on
gcc, which is absent on this host; the pip libclang wheel is self-sufficient
(the module docstring of indexer/liblocator loads it without a system gcc).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fw_context_mcp.indexer.compile_commands import CompilationUnit
from fw_context_mcp.indexer.symbols import extract_all

# ── Frozen fixtures (verbatim from the adjudication reproductions) ─────────

VARIANT_C = """\
void send_uart(void);
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

INACTIVE2_C = """\
typedef void (*fp_t)(int);
static void live_fn(int x) { (void)x; }
static void active_callee(int x) { (void)x; }

void caller_live(int x) {
    live_fn(x);
#if FEATURE_ENABLED
    active_callee(x);
#endif
}
"""

MACRO_CALLS_C = """\
typedef void (*cb_t)(int);
static void mcb(int x) { (void)x; }
static cb_t mcb_slot = mcb;

void register_api(void (*h)(int));

#define INVOKE(f, v) f(v)
#define REGISTER(h) register_api(h)

void via_macro(int v) { INVOKE(mcb_slot, v); }
void do_register(void) { REGISTER(mcb); }
"""

TYPEMATCH_C = """\
typedef void (*cb_t)(int);
static void worker(int x) { (void)x; }

static cb_t slot_a;
static cb_t slot_b;

void setup_a(void) { slot_a = worker; }
void fire_b(int x) { slot_b(x); }
"""

DEAD_C = """\
/* T07 reproduction fixture — F07_dead_code_and_linker, freestanding adaptation.
 *
 * Adaptation note (allowed by test_strategy: "adjust harmless flags for the host,
 * may not weaken the semantic assertion"): stdio puts() calls are replaced with
 * volatile stores because this host has no MSVC/mingw libc; the link is a
 * freestanding ELF cross-link with ld.lld. All semantic assertions are preserved.
 *
 * Expected index verdicts (find_dead_code) vs linker GC truth are tabulated in
 * expected.json and checked by make_repro.py.
 */

static void live_b(void) { volatile int x = 1; (void)x; }
static void live_a(void) { live_b(); }

/* Dead SCC: mutual recursion, zero inbound references from the live set.
 * The index records call edges BOTH ways, so find_dead_code flags neither.
 * External linkage so the COMPILER must emit them and the LINKER removes
 * them — the removal evidence comes from --print-gc-sections, not from
 * absence in a possibly-stripped symbol table (A07 rule). */
void dead_b(void);
void dead_a(void) { dead_b(); }
void dead_b(void) { dead_a(); }

/* Ghost reachability: the ONLY call to ghost_target sits inside #if 0.
 * The raw-text bare-call fallback (symbols.py phase 2) records a
 * ref_kind='call' edge from the inactive line, so find_dead_code never
 * flags ghost_target. External linkage -> linker GC removes both. */
void ghost_target(void) { volatile int x = 9; (void)x; }
void ghost_caller(void) {
#if 0
    ghost_target();
#endif
}

/* C constructor attribute: no AST reference exists anywhere.
 * Index: "dead".  Linker: section kept via the .init_array root. */
__attribute__((constructor))
static void global_ctor(void) { volatile int x = 2; (void)x; }

/* used + named section + linker KEEP: index has no notion of any of the
 * three roots.  Index: "dead".  Linker: section kept via KEEP(*(.keepme)). */
__attribute__((section(".keepme"), used))
void kept_by_script(void) { volatile int x = 3; (void)x; }

/* Weak-alias-only reference: alias_of_target is a declaration whose alias
 * attribute names alias_target as a STRING (no AST ref to alias_target).
 * user_of_alias takes the alias's address; the relocation lands in
 * .text.alias_target, so the linker keeps it.  Index: alias_target "dead". */
static void alias_target(void) { volatile int x = 5; (void)x; }
void alias_of_target(void) __attribute__((weak, alias("alias_target")));
void user_of_alias(void) {
    void (*volatile p)(void) = alias_of_target;
    (void)p;
}

/* C vector table entry: handler referenced only from a function-address
 * array.  Index: ref_kind='indirect' with slot_index=0 -> possibly_dead
 * (reason text says "call sites unresolved"; truth is "invoked by hardware").
 * Linker: array section KEEP'd, handler section kept by its relocation. */
static void isr_vector_handler(void) { volatile int x = 6; (void)x; }

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = { isr_vector_handler };

/* Baseline sanity: zero refs in index AND removed by GC — the one case
 * where "dead" is actually true at both layers.  External linkage so the
 * linker (not the compiler) performs the removal. */
void isr_bare(void) { volatile int x = 7; (void)x; }

/* The ENTRY symbol: --entry=_start roots it in the binary, and
 * build_configs.entry_point stores its name, but find_dead_code never
 * consults the entry point.  Index: "dead".  Linker: kept. */
int _start(void) {
    volatile int x = 8; (void)x;
    live_a();
    user_of_alias();
    return 0;
}
"""

EQ_STUB_H = """\
#pragma once
namespace events {
struct EventQueue {
    void call_every(int ms, void (*cb)());
    void dispatch_forever();
};
}
struct ZBLE {
    static void watch_ble();
};
"""

EQ_CASE_CPP = """\
#include "eq_stub.h"

void ZBLE::watch_ble() { }

void live_user(events::EventQueue *q) {
    q->call_every(500, &ZBLE::watch_ble);
#if 0
    q->call_every(999, &ZBLE::watch_ble);
#endif
}

namespace events { void EventQueue::dispatch_forever() { } }
"""

PARAM_FP_C = """\
typedef struct { void (*push)(int); } Pipe;
void route(Pipe g_pipe, void (*cb)(int)) { g_pipe.push = cb; }
"""


# ── Harness ────────────────────────────────────────────────────────────────

def _extract(tmp_path: Path, tag: str, files: dict[str, str],
             language: str, clang_args: list[str]):
    """Materialize fixture files and run the production extractor on one."""
    d = tmp_path / tag
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    main = next(name for name in files if not name.endswith(".h"))
    unit = CompilationUnit(file=d / main, directory=d, language=language,
                           clang_args=clang_args)
    return extract_all(unit, with_refs=True)


def _usr(result, name: str) -> str:
    matches = [s.usr for s in result.symbols if s.name == name]
    assert matches, f"symbol {name!r} not indexed — symbols: " \
                    f"{sorted({s.name for s in result.symbols})}"
    return matches[0]


def _call_edges(result, from_usr: str | None = None):
    return [(r.from_line, r.to_usr) for r in result.references
            if r.ref_kind == "call" and (from_usr is None or r.from_usr == from_usr)]


# ── T04::F04_no_inactive_branch_call_refs ──────────────────────────────────

def test_t04_f04_no_inactive_branch_call_refs(tmp_path):
    """Per-build refs must equal the clang AST call set: build A calls
    send_uart@10 only, build B calls send_usb@8 only.  The raw-text
    fallback must not emit edges from inactive-branch lines."""
    build_a = _extract(tmp_path, "A", {"variant.c": VARIANT_C}, "c", [])
    build_b = _extract(tmp_path, "B", {"variant.c": VARIANT_C}, "c",
                       ["-DIMPL_B=1"])
    usr_uart = _usr(build_a, "send_uart")
    usr_usb = _usr(build_a, "send_usb")
    usr_ts = _usr(build_a, "transport_send")

    edges_a = set(_call_edges(build_a, usr_ts))
    edges_b = set(_call_edges(build_b, usr_ts))
    # Active branch only — exactly the AST call set.
    assert edges_a == {(10, usr_uart)}, f"build A ghost edges: {edges_a}"
    assert edges_b == {(8, usr_usb)}, f"build B ghost edges: {edges_b}"
    # Explicit inactive-branch absence (both directions).
    assert (8, usr_usb) not in edges_a
    assert (10, usr_uart) not in edges_b


# ── T05::T05_inactive_branch_no_ghost_edges ────────────────────────────────

def test_t05_inactive_branch_no_ghost_edges(tmp_path):
    """In build A, refs from transport_send contain send_uart@10 (active)
    and MUST NOT contain send_usb@8 as ref_kind='call'.  Mirror for
    build B.  (No certainty column exists pre-SLICE-B, so the inactive
    site must be absent, not labeled.)"""
    build_a = _extract(tmp_path, "A", {"variant.c": VARIANT_C}, "c", [])
    build_b = _extract(tmp_path, "B", {"variant.c": VARIANT_C}, "c",
                       ["-DIMPL_B=1"])
    usr_uart, usr_usb, usr_ts = (_usr(build_a, n)
                                 for n in ("send_uart", "send_usb", "transport_send"))

    a_uart = [(ln, tu) for ln, tu in _call_edges(build_a, usr_ts) if tu == usr_uart]
    a_usb = [(ln, tu) for ln, tu in _call_edges(build_a, usr_ts) if tu == usr_usb]
    b_uart = [(ln, tu) for ln, tu in _call_edges(build_b, usr_ts) if tu == usr_uart]
    b_usb = [(ln, tu) for ln, tu in _call_edges(build_b, usr_ts) if tu == usr_usb]

    assert a_uart == [(10, usr_uart)], f"build A active edge wrong: {a_uart}"
    assert a_usb == [], f"build A inactive-branch ghost: {a_usb}"
    assert b_usb == [(8, usr_usb)], f"build B active edge wrong: {b_usb}"
    assert b_uart == [], f"build B inactive-branch ghost: {b_uart}"


# ── T06::TS06_02_inactive_branch_no_call_edge ──────────────────────────────

def test_t06_ts06_02_inactive_branch_no_call_edge(tmp_path):
    """-DFEATURE_ENABLED=0 makes ``#if FEATURE_ENABLED`` dead: no call edge
    to active_callee may exist (extraction-level mirror of the frozen SQL
    oracle: count(refs WHERE ref_kind='call' AND to_usr=active_callee AND
    from_line BETWEEN 7 AND 9) == 0)."""
    r = _extract(tmp_path, "inactive2", {"inactive2.c": INACTIVE2_C}, "c",
                 ["-std=c11", "-DFEATURE_ENABLED=0"])
    usr_callee = _usr(r, "active_callee")
    ghosts = [(r_.from_line, r_.to_usr) for r_ in r.references
              if r_.ref_kind == "call" and r_.to_usr == usr_callee
              and 7 <= r_.from_line <= 9]
    assert ghosts == [], f"call edges from inactive branch lines 7-9: {ghosts}"
    # The live call at line 6 is untouched.
    assert (6, _usr(r, "live_fn")) in set(_call_edges(r))


# ── T06::TS06_05_macro_no_false_edges ──────────────────────────────────────

def test_t06_ts06_05_macro_no_false_edges(tmp_path):
    """Macro-expanded call sites produce no phantom callers (no call edge
    from do_register to via_macro or to itself) and the indirect_call_sites
    row keeps an expr_text that starts at the callee expression."""
    r = _extract(tmp_path, "macro", {"macro_calls.c": MACRO_CALLS_C}, "c",
                 ["-std=c11"])
    usr_via = _usr(r, "via_macro")
    usr_do = _usr(r, "do_register")
    ghosts = [(r_.from_line, r_.to_usr) for r_ in r.references
              if r_.ref_kind == "call" and r_.from_usr == usr_do
              and r_.to_usr in (usr_via, usr_do)]
    assert ghosts == [], f"phantom macro edges from do_register: {ghosts}"

    ics_rows = [i for i in r.indirect_call_sites
                if Path(i.from_file).name == "macro_calls.c" and i.from_line == 10]
    assert ics_rows, "expected an indirect_call_sites row at macro_calls.c:10"
    for row in ics_rows:
        assert row.expr_text != "mcb_slot , v )", (
            f"garbled macro expr_text survived: {row.expr_text!r}")
        assert (row.expr_text == "mcb_slot"
                or row.expr_text.startswith("INVOKE")), (
            f"expr_text does not start at the callee expression: {row.expr_text!r}")


# ── T06::TS06_06_tentative_slot_indexed ────────────────────────────────────

def test_t06_ts06_06_tentative_slot_indexed(tmp_path):
    """An uninitialized file-scope fn-ptr variable is a C tentative
    definition and must be indexed (count(symbols WHERE name='slot_a' AND
    kind='varglobal') == 1)."""
    r = _extract(tmp_path, "typematch", {"typematch.c": TYPEMATCH_C}, "c",
                 ["-std=c11"])
    for name in ("slot_a", "slot_b"):
        rows = [s for s in r.symbols if s.name == name and s.kind == "varglobal"]
        assert len(rows) == 1, (
            f"tentative definition {name!r}: expected exactly 1 varglobal "
            f"row, got {len(rows)}; symbols={sorted({s.name for s in r.symbols})}")


# ── T07::TC07-02_ghost_if0_edge_false_negative ─────────────────────────────

def test_t07_tc07_02_ghost_if0_edge_false_negative(tmp_path):
    """The only call to ghost_target sits inside ``#if 0``; the refs row
    (to_usr=ghost_target, ref_kind='call', from_line=31) must not exist."""
    r = _extract(tmp_path, "dead", {"dead.c": DEAD_C}, "c", ["-std=c11"])
    usr_ghost = _usr(r, "ghost_target")
    rows = [(r_.from_line, r_.from_usr) for r_ in r.references
            if r_.ref_kind == "call" and r_.to_usr == usr_ghost
            and r_.from_line == 31]
    assert rows == [], f"ghost #if 0 call edge at dead.c:31: {rows}"


# ── T07::TC07-07_self_ghost_on_attribute_function ──────────────────────────

def test_t07_tc07_07_self_ghost_on_attribute_function(tmp_path):
    """No self call edge (to_usr == from_usr) for attribute-decorated
    functions: __attribute__ shifts the cursor extent start above the
    signature line, so suppression keyed on the extent start misses the
    bare-name match on the stored definition line."""
    r = _extract(tmp_path, "dead", {"dead.c": DEAD_C}, "c", ["-std=c11"])
    for name in ("global_ctor", "kept_by_script"):
        usr = _usr(r, name)
        self_edges = [r_.from_line for r_ in r.references
                      if r_.ref_kind == "call" and r_.to_usr == usr
                      and r_.from_usr == usr]
        assert self_edges == [], f"self ghost edges on {name}: {self_edges}"
    # Control: attribute-free functions had no self edge at base either.
    usr_bare = _usr(r, "isr_bare")
    control = [r_.from_line for r_ in r.references
               if r_.ref_kind == "call" and r_.to_usr == usr_bare
               and r_.from_usr == usr_bare]
    assert control == [], f"control isr_bare gained a self edge: {control}"


# ── T08::t08_phantom_dispatch_edge_from_ifdef0_line ────────────────────────

def test_t08_phantom_dispatch_edge_from_ifdef0_line(tmp_path):
    """After the fallback fix only the live dispatch row (line 6) remains;
    the ``#if 0`` line 8 must not manufacture a reachable async edge."""
    r = _extract(tmp_path, "eq", {"eq_stub.h": EQ_STUB_H,
                                  "eq_case.cpp": EQ_CASE_CPP},
                 "c++", ["-std=c++14"])
    lines = sorted(pd.line for pd in r.pending_dispatches)
    assert 8 not in lines, f"phantom dispatch rows from #if 0 at lines {lines}"
    assert 6 in lines, f"live dispatch row at line 6 missing: {lines}"


# ── T11::fp_assignment_parameter_rhs_gap (SLICE-A scope item) ──────────────

def test_t11_fp_assignment_parameter_rhs_gap(tmp_path):
    """``g_pipe.push = cb`` where cb is a function-pointer PARAMETER must
    record an fp_assignments row (method='assignment', lhs='push',
    rhs_usr = cb's USR).  At base the extractor records function refs only
    and drops the row entirely.  (A PARM_DECL is not a symbol row, so the
    parameter is identified through the fp_assignments row itself.)"""
    r = _extract(tmp_path, "param_fp", {"param_fp.c": PARAM_FP_C}, "c",
                 ["-std=c11"])
    rows = [f for f in r.fp_assignments
            if f.rhs_name == "cb" and f.method == "assignment"
            and f.lhs_name == "push"]
    assert rows, (
        "fp_assignments row for function-pointer parameter RHS missing; "
        f"have: {[(f.lhs_name, f.rhs_name, f.method) for f in r.fp_assignments]}")
    assert rows[0].rhs_usr, "rhs_usr must carry the parameter's USR"
    assert rows[0].from_usr == _usr(r, "route")


# ── Adversarial guard: the hygiene fixes must not erase live edges ─────────

def test_adversarial_active_branch_edges_survive(tmp_path):
    """Guards against over-suppression: live-code edges that existed at
    base must survive the fallback hygiene changes (dead.c live call set,
    macro_calls register_api AST edge, typematch slot assignment)."""
    dead = _extract(tmp_path, "dead", {"dead.c": DEAD_C}, "c", ["-std=c11"])
    usr = {s.name: s.usr for s in dead.symbols}
    edges = set(_call_edges(dead))
    assert (13, usr["live_b"]) in edges          # live_a -> live_b
    assert (21, usr["dead_b"]) in edges          # dead_a -> dead_b
    assert (22, usr["dead_a"]) in edges          # dead_b -> dead_a
    assert (75, usr["live_a"]) in edges          # _start -> live_a
    assert (76, usr["user_of_alias"]) in edges   # _start -> user_of_alias

    macro = _extract(tmp_path, "macro", {"macro_calls.c": MACRO_CALLS_C}, "c",
                     ["-std=c11"])
    usr_register_api = _usr(macro, "register_api")
    usr_do = _usr(macro, "do_register")
    assert any(r_.ref_kind == "call" and r_.to_usr == usr_register_api
               and r_.from_usr == usr_do and r_.from_line == 11
               for r_ in macro.references), (
        "AST edge do_register -> register_api (macro-expanded call) was lost")

    tm = _extract(tmp_path, "typematch", {"typematch.c": TYPEMATCH_C}, "c",
                  ["-std=c11"])
    usr_worker = _usr(tm, "worker")
    fpas = [f for f in tm.fp_assignments
            if f.rhs_usr == usr_worker and f.method == "assignment"]
    assert fpas, "slot_a = worker assignment row was lost"
