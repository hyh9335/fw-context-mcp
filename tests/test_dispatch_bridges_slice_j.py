"""SLICE-J dispatch-bridge registry v2 oracle (T14 TEST_PLAN §10, ADR-010).

Bridge-registry v2 with ``object_arg``/``handler_arg`` rules so object-mediated
registration (``k_work_submit(&w)`` → handler via object linkage) and
handler-arg signatures produce ``ref_kind='dispatch'`` edges with per-edge
provenance (``refs.source = 'bridge:<api_qn>'``), instead of today's
wrong-edge or silent skip (``_dispatch_bridges.py:45-71``).  Queue semantics
(EVENT_SEND/EVENT_RECEIVE, work-queue ordering) stay explicitly OUT of scope
(ADR-010; ``t08_f09_queue_semantics_absent`` asserts the absence is
documented).  Schema READ-ONLY: bridge facts live in existing columns
(``refs.source``, ``fp_assignments.method='register'``) — no new table, no
new ref_kind (ADR-004's refs vocabulary stays closed; ADR-010 "annotations on
rows").

Case ids mirror the frozen adjudication RED/GREEN list (TEST_PLAN §10):

RED (must fail pre-fix, pass post-fix):
- T08::t08_f08_no_pending_dispatch_for_object_mediated_apis
- T08::t08_f08_no_async_dispatch_edge_fire_to_handler
- T08::t08_f08_static_work_object_linkage_lost
- T08::t08_f09_xtimerstart_bridge_structurally_dead
- T08::t08_xtaskcreate_false_dispatch_edge
- T08::t08_user_bridge_cannot_rescue_register_api
- T08::t08_bridge_skip_is_silent
- T08::t08_dispatch_edge_traversal_inside_path  (T08 verify hook)
- T08::t08_trace_data_flow_docstring_stale

GREEN:
- T08::t08_f09_queue_semantics_absent (documented absence)
- T08::t08_incremental_full_eq_dispatch_rows
- T08::t08_production_tree_clean

Fixtures are the T08 research fixtures (``artifacts/T08/reproduction/
fixtures_snapshot/``), verbatim where the oracle needs the broken state.
Two tests add a stub TU defining the RTOS-internal dispatch entry point
(``z_work_q_main`` / ``prvTimerCallback``) — the same pattern the research's
F09_task_exit fixture used for ``prvTaskExitError`` — so the *traversal*
oracle can answer once the bridge produces the entry→handler edge.  The
entry-absent case is covered by ``t08_bridge_skip_is_silent`` (skip is
recorded, never silent).
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures (T08 research snapshot, F08_zephyr_async_stub / F08_zephyr_bridge /
# F09_freertos_async_stub / F09_task_exit)
# ═══════════════════════════════════════════════════════════════════════════

ZEPHYR_STUB_H = """\
#pragma once

struct k_work;

typedef void (*k_work_handler_t)(struct k_work *);

struct k_work { k_work_handler_t handler; };

void k_work_init(struct k_work *work, k_work_handler_t handler);

int k_work_submit(struct k_work *work);

int k_work_submit_to_queue(void *queue, struct k_work *work);

#define K_WORK_DEFINE(name, fn) struct k_work name = { fn }
"""

ZEPHYR_CASE_C = """\
#include "zephyr_stub.h"

static void handler(struct k_work *w) { (void)w; }

static struct k_work dynamic_work;

static K_WORK_DEFINE(static_work, handler);


void setup_dynamic(void) { k_work_init(&dynamic_work, handler); }

void fire_dynamic(void) { k_work_submit(&dynamic_work); }

void fire_static(void) { k_work_submit(&static_work); }
"""

# Zephyr's system-workqueue runner, defined so the builtin bridge entry point
# ``z_work_q_main`` resolves.  Same trick as the research F09_task_exit
# fixture (task_exit.c defines prvTaskExitError).
WORKQ_STUB_C = """\
#include "zephyr_stub.h"

void z_work_q_main(void) { for (;;) { } }
"""

FREERTOS_STUB_H = """\
#pragma once
typedef void *TaskHandle_t;
typedef void *TimerHandle_t;
typedef void *QueueHandle_t;
typedef void (*TaskFunction_t)(void *);
typedef void (*TimerCallbackFunction_t)(TimerHandle_t);
int xTaskCreate(TaskFunction_t fn, const char *name, unsigned stack, void *arg,
                unsigned prio, TaskHandle_t *out);
TimerHandle_t xTimerCreate(const char *name, unsigned period, int reload,
                           void *id, TimerCallbackFunction_t cb);
int xTimerStart(TimerHandle_t timer, unsigned wait);
int xQueueSend(QueueHandle_t q, const void *item, unsigned wait);
int xQueueReceive(QueueHandle_t q, void *item, unsigned wait);
"""

FREERTOS_CASE_C = """\
#include "freertos_stub.h"
static QueueHandle_t q;
static TimerHandle_t timer;

static void worker(void *arg) { (void)arg; }
static void timer_cb(TimerHandle_t t) { (void)t; }

void setup(void) {
    xTaskCreate(worker, "w", 256, 0, 1, 0);
    timer = xTimerCreate("t", 10, 0, 0, timer_cb);
}

void start_timer(void) { xTimerStart(timer, 0); }
void producer(int v) { xQueueSend(q, &v, 0); }
void consumer(void) { int v; xQueueReceive(q, &v, 0); }
"""

# FreeRTOS timer-service callback, defined so the builtin bridge entry point
# ``prvTimerCallback`` resolves (research F09_task_exit pattern).
TIMER_SERVICE_C = """\
#include "freertos_stub.h"

void prvTimerCallback(TimerHandle_t t) { (void)t; }
"""

TASK_EXIT_C = """\
#include "freertos_stub.h"

void prvTaskExitError(void) { for (;;) { } }

void bad_task(void) { prvTaskExitError(); }
"""

# F08_zephyr_bridge: user bridge config + stub impl that defines the entry
ZEPHYR_BRIDGE_CONFIG_APPEND = """\

[call_graph.dispatch_bridges]
k_work_init = "work_loop_runner"
"""

STUB_IMPL_C = """\
#include "zephyr_stub.h"

void work_loop_runner(void) { }

int k_work_submit(struct k_work *work) { (void)work; work_loop_runner(); return 0; }
"""


# ── Harness (same isolation pattern as the SLICE-G/H/I suites) ──────────────

CONFIG_TMPL = """\
[project]
name = "{name}"
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
            "arguments": [
                "clang", "--target=x86_64-unknown-linux-gnu", "-std=c11",
                "-ffreestanding", "-nostdlibinc",
                "-c", name, "-o", f"build/o{i}.o",
            ],
        }
        for i, name in enumerate(files)
    ]
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload), encoding="utf-8")
    return cc


def _make_project(tmp_path: Path, name: str, files: dict[str, str],
                  config_append: str = ""):
    """Fixture project + isolated index dir + full index → (root, db_path, hash)."""
    root = tmp_path / name
    root.mkdir()
    index_dir = tmp_path / f"{name}_index"
    for fname, text in files.items():
        (root / fname).write_text(text, encoding="utf-8")
    cc = _write_cc(root, [f for f in files if f.endswith((".c", ".cpp"))])
    (root / ".fw-context").mkdir()
    (root / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(name=name, pid=name.replace("-", ""), db_dir=str(index_dir).replace("\\", "/"))
        + config_append,
        encoding="utf-8",
    )
    db_path = index_dir / "index.db"
    ch = run(cc, db_path, project_root=root, project_id=name.replace("-", ""),
             index_refs=True, index_embeddings=False, analyze_symbols=False,
             force=True)
    return root, db_path, ch


def _rows(conn, sql: str, args: tuple = ()) -> list[dict]:
    # Description-based rows: fw_context_mcp redirects sys.modules["sqlite3"]
    # to pysqlite3, so stdlib sqlite3.Row must never touch these cursors.
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _dispatch_rows(conn: sqlite3.Connection, ch: str) -> list[dict]:
    return _rows(
        conn,
        "SELECT to_usr, from_usr, from_file, from_line, source FROM refs "
        "WHERE config_hash = ? AND ref_kind = 'dispatch'",
        (ch,),
    )


def _usr_by_name(conn: sqlite3.Connection, ch: str, name: str) -> str:
    rows = _rows(
        conn,
        "SELECT usr FROM symbols WHERE config_hash = ? AND name = ? "
        "ORDER BY is_definition DESC LIMIT 1",
        (ch, name),
    )
    return rows[0]["usr"] if rows else ""


# ═══════════════════════════════════════════════════════════════════════════
# RED — the nine adjudicated failures
# ═══════════════════════════════════════════════════════════════════════════


def test_t08_f08_no_pending_dispatch_for_object_mediated_apis(tmp_path):
    """O-08-1 (Zephyr half): the detection stage must queue PendingDispatch
    records for the registry-annotated Zephyr sites — k_work_submit(&w)
    twice (object-mediated trigger) and k_work_init(&w, handler) once
    (register).  Pre-fix the fn-ptr-arg gate drops all three: submit carries
    no fn-ptr argument and k_work_init is not in the v1 registry at all.
    """
    from fw_context_mcp.indexer.compile_commands import CompilationUnit
    from fw_context_mcp.indexer.symbols import extract_all

    root, db_path, ch = _make_project(
        tmp_path, "f08_detect",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C},
    )
    args = ["-std=c11"]
    unit = CompilationUnit(file=root / "zephyr_case.c", directory=root,
                           language="c", clang_args=args)
    result = extract_all(unit, with_refs=True)
    pd = result.pending_dispatches
    by_api = {}
    for p in pd:
        by_api.setdefault(p.callee_qn, []).append(p)

    submits = by_api.get("k_work_submit", [])
    assert len(submits) == 2, (
        "k_work_submit(&dynamic_work) and k_work_submit(&static_work) must "
        f"each produce an object-mediated PendingDispatch, got {len(submits)}: "
        f"{[(p.callee_qn, p.object_usr) for p in pd]}")
    assert all(p.object_usr for p in submits), (
        "object-mediated triggers must record the object argument USR: "
        f"{[(p.callee_qn, p.object_usr) for p in submits]}")
    inits = by_api.get("k_work_init", [])
    assert len(inits) == 1, (
        "k_work_init(&dynamic_work, handler) must produce a REGISTER "
        f"PendingDispatch, got {len(inits)}: {[(p.callee_qn, p.object_usr, p.target_usr) for p in pd]}")
    assert inits[0].object_usr and inits[0].target_usr, (
        "the k_work_init record must carry BOTH the object USR and the "
        f"handler USR: {inits[0]}")


def test_t08_f08_no_async_dispatch_edge_fire_to_handler(tmp_path):
    """O-08-2: after indexing F08 (+ the workqueue stub so the entry point
    resolves), a dispatch row z_work_q_main → handler must exist per work
    item (one per fire site), and find_call_path('fire_dynamic', 'handler')
    must answer.  Pre-fix: zero dispatch rows (v1 drops object-mediated
    triggers) and no path.
    """
    from fw_context_mcp.indexer.db import find_call_path

    _, db_path, ch = _make_project(
        tmp_path, "f08_edge",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C,
         "workq.c": WORKQ_STUB_C},
    )
    conn = open_db(db_path)
    try:
        d = _dispatch_rows(conn, ch)
        entry = _usr_by_name(conn, ch, "z_work_q_main")
        handler = _usr_by_name(conn, ch, "handler")
        per_work = [r for r in d if r["from_usr"] == entry
                    and r["to_usr"] == handler]
        assert len(per_work) >= 2, (
            "a dispatch row per work item (dynamic + static) must exist: "
            f"{d}")
        assert all(str(r["source"]).startswith("bridge:k_work_submit") for r in per_work), (
            "bridge-produced dispatch edges must carry per-edge provenance in "
            f"refs.source: {[r['source'] for r in per_work]}")
        path = find_call_path(conn, ch, "fire_dynamic", "handler", max_depth=8)
        assert path, "fire_dynamic → handler must be answerable across the "
        "async boundary"
        chains = [p["chain"] for p in path]
        assert any("z_work_q_main" in c for c in chains), (
            f"path must cross z_work_q_main, got: {chains}")
    finally:
        conn.close()


def test_t08_f08_static_work_object_linkage_lost(tmp_path):
    """K_WORK_DEFINE(static_work, handler) expands to a struct initializer
    whose fn reference sits in a positional init list at file scope: the
    init-list walker records an indirect ref with from_usr=NULL and no
    fp_assignments row, so the static work item can never be linked to its
    handler (research §1.3).  Post-fix the object being initialized is the
    LHS (fp_assignments, method='init_list') and the fire_static trigger
    resolves through that linkage.
    """
    _, db_path, ch = _make_project(
        tmp_path, "f08_static",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C,
         "workq.c": WORKQ_STUB_C},
    )
    conn = open_db(db_path)
    try:
        sw = _usr_by_name(conn, ch, "static_work")
        handler = _usr_by_name(conn, ch, "handler")
        assert sw and handler, "fixture symbols must index"
        link = _rows(
            conn,
            "SELECT lhs_usr, rhs_usr, method FROM fp_assignments "
            "WHERE config_hash = ? AND lhs_usr = ? AND rhs_usr = ?",
            (ch, sw, handler),
        )
        assert link, (
            "static_work → handler object linkage must be recorded in "
            "fp_assignments (init-list object linkage); got none")
        # The linkage must feed the trigger resolution: fire_static's submit
        # line produces its own dispatch row.
        d = _dispatch_rows(conn, ch)
        fire_line = _rows(
            conn,
            "SELECT from_line FROM refs WHERE config_hash = ? AND from_usr = "
            "(SELECT usr FROM symbols WHERE config_hash = ? AND name = "
            "'fire_static' ORDER BY is_definition DESC LIMIT 1) "
            "AND ref_kind = 'call'",
            (ch, ch),
        )
        fire_lines = {r["from_line"] for r in fire_line}
        assert any(r["from_line"] in fire_lines for r in d), (
            f"fire_static's trigger must resolve via the static_work linkage; "
            f"dispatch rows {d}, fire lines {fire_lines}")
    finally:
        conn.close()


def test_t08_f09_xtimerstart_bridge_structurally_dead(tmp_path):
    """O-08-3 (FreeRTOS): xTimerStart is in the v1 registry but its bridge is
    structurally dead — xTimerStart(timer, 0) carries a handle, never a
    fn-ptr argument, so v1 queues no PendingDispatch and the timer callback
    is never wired to prvTimerCallback.  Registry v2 links the chain
    xTimerCreate(…, timer_cb) [register, object_return] → timer variable →
    xTimerStart(timer) [trigger, object_arg] → dispatch row
    prvTimerCallback → timer_cb, and start_timer → timer_cb becomes
    answerable.
    """
    from fw_context_mcp.indexer.db import find_call_path

    _, db_path, ch = _make_project(
        tmp_path, "f09_timer",
        {"freertos_stub.h": FREERTOS_STUB_H, "freertos_case.c": FREERTOS_CASE_C,
         "timer_service.c": TIMER_SERVICE_C},
    )
    conn = open_db(db_path)
    try:
        entry = _usr_by_name(conn, ch, "prvTimerCallback")
        cb = _usr_by_name(conn, ch, "timer_cb")
        d = _dispatch_rows(conn, ch)
        wired = [r for r in d if r["from_usr"] == entry and r["to_usr"] == cb]
        assert wired, (
            "xTimerStart's trigger must wire prvTimerCallback → timer_cb via "
            f"the xTimerCreate object linkage; dispatch rows: {d}")
        assert all(str(r["source"]).startswith("bridge:xTimerStart") for r in wired), (
            "provenance must name the trigger API: "
            f"{[r['source'] for r in wired]}")
        path = find_call_path(conn, ch, "start_timer", "timer_cb", max_depth=8)
        assert path, "start_timer → timer_cb must be answerable across the "
        "timer-service bridge"
        assert any("prvTimerCallback" in p["chain"] for p in path), (
            f"path must cross prvTimerCallback, got: {[p['chain'] for p in path]}")
    finally:
        conn.close()


def test_t08_xtaskcreate_false_dispatch_edge(tmp_path):
    """RC2 (research §8): v1 maps xTaskCreate* → prvTaskExitError, a false
    dispatch edge that makes bad_task → prvTaskExitError → worker answer as
    a 'call path' although prvTaskExitError never dispatches anything — the
    task body is invoked by the scheduler, not by the exit-error stub.
    Registry v2 removes the rule.  The legitimate indirect fn-ptr edge
    (bad_task's file, setup's call argument → worker) must survive.
    """
    from fw_context_mcp.indexer.db import find_call_path

    _, db_path, ch = _make_project(
        tmp_path, "f09_task_exit",
        {"freertos_stub.h": FREERTOS_STUB_H, "freertos_case.c": FREERTOS_CASE_C,
         "task_exit.c": TASK_EXIT_C},
    )
    conn = open_db(db_path)
    try:
        ptee = _usr_by_name(conn, ch, "prvTaskExitError")
        worker = _usr_by_name(conn, ch, "worker")
        false_rows = [r for r in _dispatch_rows(conn, ch)
                      if r["from_usr"] == ptee and r["to_usr"] == worker]
        assert not false_rows, (
            "xTaskCreate → prvTaskExitError is a false dispatch edge and must "
            f"not produce a dispatch row: {false_rows}")
        path = find_call_path(conn, ch, "bad_task", "worker", max_depth=8)
        bad = [p["chain"] for p in path if "prvTaskExitError" in p["chain"]]
        assert not bad, (
            "the false chain bad_task → prvTaskExitError → worker must not be "
            f"reported: {bad}")
        # Correctness gain without reachability loss: the direct fn-ptr
        # argument edge setup → worker still exists.
        setup_edges = _rows(
            conn,
            "SELECT 1 FROM refs WHERE config_hash = ? AND ref_kind = 'indirect' "
            "AND to_usr = ? AND from_usr = (SELECT usr FROM symbols WHERE "
            "config_hash = ? AND name = 'setup' ORDER BY is_definition DESC "
            "LIMIT 1)",
            (ch, worker, ch),
        )
        assert setup_edges, "setup → worker indirect fn-ptr edge must survive"
    finally:
        conn.close()


def test_t08_user_bridge_cannot_rescue_register_api(tmp_path):
    """F08_zephyr_bridge: a user bridge k_work_init = work_loop_runner must
    rescue the registration API.  v1 never queues a PendingDispatch for
    k_work_init (not in the registry), so the user bridge silently produces
    zero dispatch edges.  Registry v2 treats k_work_init as a REGISTER rule
    with handler_arg semantics, and the user entry supplies the execution
    context: dispatch row work_loop_runner → handler at the registration
    site.
    """
    _, db_path, ch = _make_project(
        tmp_path, "f08_bridge",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C,
         "stub_impl.c": STUB_IMPL_C},
        config_append=ZEPHYR_BRIDGE_CONFIG_APPEND,
    )
    conn = open_db(db_path)
    try:
        runner = _usr_by_name(conn, ch, "work_loop_runner")
        handler = _usr_by_name(conn, ch, "handler")
        d = _dispatch_rows(conn, ch)
        rescued = [r for r in d if r["from_usr"] == runner
                   and r["to_usr"] == handler]
        assert rescued, (
            "user bridge k_work_init → work_loop_runner must produce a "
            f"dispatch edge to the registered handler; dispatch rows: {d}")
    finally:
        conn.close()


def test_t08_bridge_skip_is_silent(tmp_path, caplog):
    """F08-as-is (no workqueue stub): every trigger pending resolves to the
    builtin entry z_work_q_main, which is absent from this build.  v1 drops
    the entry silently; registry v2 must record a skip reason (warning log
    with the entry name + persisted dispatch_skip_report in manifest.json).
    """
    from fw_context_mcp.indexer.manifest import _manifest_path

    root, db_path, ch = _make_project(
        tmp_path, "f08_skip",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C},
    )
    db_dir = db_path.parent
    cc = root / "compile_commands.json"
    with caplog.at_level(logging.WARNING, logger="fw_context_mcp"):
        run(cc, db_path, project_root=root, project_id="f08skip",
            index_refs=True, index_embeddings=False, analyze_symbols=False,
            force=True)
    skip_logs = [r for r in caplog.records
                 if "dispatch bridge" in r.getMessage().lower()
                 and "skip" in r.getMessage().lower()]
    assert skip_logs, (
        "skipped bridge entries must be surfaced as an index note naming the "
        "entry point and the reason, not dropped silently")
    assert any("z_work_q_main" in r.getMessage() for r in skip_logs), (
        "the skip note must name the missing entry symbol: "
        f"{[r.getMessage() for r in skip_logs]}")
    manifest = json.loads(_manifest_path(db_dir, ch).read_text(encoding="utf-8"))
    report = manifest.get("dispatch_skip_report")
    assert report, f"manifest must persist the skip report, got {manifest.keys()}"
    assert any("z_work_q_main" in json.dumps(entry) for entry in report), (
        f"skip report must name z_work_q_main: {report}")


def test_t08_dispatch_edge_traversal_inside_path(tmp_path):
    """T08 verify hook: the async hop is traversed *inside* find_call_path
    without ever becoming a proven call.  fire_dynamic → z_work_q_main is a
    QUERY-TIME transit synthesized from the trigger-site join (no persisted
    refs row), and z_work_q_main → handler is the persisted dispatch edge
    with bridge provenance.
    """
    from fw_context_mcp.indexer.db import find_call_path

    _, db_path, ch = _make_project(
        tmp_path, "f08_traverse",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C,
         "workq.c": WORKQ_STUB_C},
    )
    conn = open_db(db_path)
    try:
        fire = _usr_by_name(conn, ch, "fire_dynamic")
        entry = _usr_by_name(conn, ch, "z_work_q_main")
        persisted_hops = _rows(
            conn,
            "SELECT 1 FROM refs WHERE config_hash = ? AND from_usr = ? AND "
            "to_usr = ? AND ref_kind IN ('call','indirect','member','ref')",
            (ch, fire, entry),
        )
        assert not persisted_hops, (
            "the transit hop fire_dynamic → z_work_q_main is a dispatch-kind "
            "query-time edge, never a proven call")
        path = find_call_path(conn, ch, "fire_dynamic", "handler", max_depth=8)
        assert path, "the bridged path must be answerable"
        chains = [p["chain"] for p in path]
        full = any(
            "fire_dynamic" in c and "z_work_q_main" in c and "handler" in c
            for c in chains)
        assert full, f"path must be fire_dynamic → z_work_q_main → handler, got: {chains}"
        entry_usr = entry
        handler_usr = _usr_by_name(conn, ch, "handler")
        mid = [r for r in _dispatch_rows(conn, ch)
               if r["from_usr"] == entry_usr and r["to_usr"] == handler_usr]
        assert mid and mid[0]["source"].startswith("bridge:"), (
            f"the middle edge must be a persisted dispatch row with bridge "
            f"provenance: {mid}")
    finally:
        conn.close()


def test_t08_trace_data_flow_docstring_stale():
    """The trace_data_flow contract still claims a blanket "Cannot follow
    async flows", but dispatch-bridged async flows ARE followed since the
    bridge registry feeds find_call_path's BFS (research §Option A).  The
    docstring must state that dispatch-bridged flows are followed while
    queue/message semantics stay unmodeled (ADR-010 OUT-of-scope).
    """
    from fw_context_mcp.mcp.handlers import callgraph

    doc = inspect.getdoc(callgraph.trace_data_flow) or ""
    low = doc.lower()
    assert "dispatch" in low and "follow" in low, (
        "trace_data_flow docstring must state that dispatch-bridged async "
        f"flows are followed, got:\n{doc}")
    assert "queue" in low, (
        "trace_data_flow docstring must keep documenting that queue "
        f"semantics are not modeled (ADR-010), got:\n{doc}")
    assert "cannot follow" in low, (
        "the async-flow limitation must remain qualified — only the "
        f"dispatch-bridged subset is followable, got:\n{doc}")


# ═══════════════════════════════════════════════════════════════════════════
# GREEN — the three adjudicated passes
# ═══════════════════════════════════════════════════════════════════════════


def test_t08_f09_queue_semantics_absent(tmp_path):
    """Documented absence (ADR-010): producer → xQueueSend and consumer →
    xQueueReceive are NOT connected — no EVENT_SEND/EVENT_RECEIVE edges, no
    queue-ordering model — and that absence is DOCUMENTED in the registry
    module docstring, not merely unimplemented.
    """
    from fw_context_mcp.indexer.db import find_call_path

    _, db_path, ch = _make_project(
        tmp_path, "f09_queue",
        {"freertos_stub.h": FREERTOS_STUB_H, "freertos_case.c": FREERTOS_CASE_C},
    )
    conn = open_db(db_path)
    try:
        kinds = [r["ref_kind"] for r in _rows(
            conn, "SELECT DISTINCT ref_kind FROM refs WHERE config_hash = ?",
            (ch,))]
        assert not any(k.startswith("event_") for k in kinds), (
            f"queue semantics must not be modeled: {kinds}")
        path = find_call_path(conn, ch, "producer", "consumer", max_depth=8)
        assert not path, "queue producer/consumer must stay unconnected"
    finally:
        conn.close()
    from fw_context_mcp.indexer import _dispatch_bridges as bridges

    module_doc = (bridges.__doc__ or "").lower()
    assert "queue" in module_doc and (
        "out of scope" in module_doc or "not modeled" in module_doc), (
        "the registry module must document queue semantics as out of scope "
        f"(ADR-010), got:\n{bridges.__doc__}")


def test_t08_incremental_full_eq_dispatch_rows(tmp_path):
    """O_INCREMENTAL_FULL_EQ: incremental re-index equals a full rebuild for
    every dispatch-domain row.  Mutation A removes the k_work_init
    registration; mutation B re-adds it.  Canonical rows (refs incl.
    dispatch + fp_assignments) must match the reference rebuild exactly.
    """
    root, db_path, ch = _make_project(
        tmp_path, "f08_incr",
        {"zephyr_stub.h": ZEPHYR_STUB_H, "zephyr_case.c": ZEPHYR_CASE_C,
         "workq.c": WORKQ_STUB_C},
    )
    case = root / "zephyr_case.c"
    cc = root / "compile_commands.json"
    original = case.read_text(encoding="utf-8")
    mutated = original.replace(
        "void setup_dynamic(void) { k_work_init(&dynamic_work, handler); }\n\n",
        "",
    )
    assert mutated != original

    def snap_rows(db: Path):
        conn = open_db(db)
        try:
            cur = conn.execute(
                "SELECT to_usr, from_file, from_line, from_usr, ref_kind, "
                "source FROM refs WHERE config_hash = ? "
                "AND ref_kind IN ('dispatch','register')",
                (ch,),
            )
            refs = sorted(map(tuple, cur.fetchall()))
            cur = conn.execute(
                "SELECT from_file, from_line, lhs_usr, lhs_name, rhs_usr, "
                "rhs_name, fn_ptr_type, method, certainty, evidence "
                "FROM fp_assignments WHERE config_hash = ? AND "
                "method IN ('register','init_list')",
                (ch,),
            )
            fpa = sorted(map(tuple, cur.fetchall()))
        finally:
            conn.close()
        return refs, fpa

    # Bump mtimes monotonically (+60 s each): the indexer's Tier-1 fast path
    # treats a new mtime within MTIME_TOLERANCE_S (1 s) of the stored one as
    # unchanged, so small relative bumps can legitimately skip a re-parse.
    import time as _time

    _next_ts = [_time.time() + 120]

    def bump_mtime(p: Path) -> None:
        _next_ts[0] += 60
        os.utime(p, (_next_ts[0], _next_ts[0]))

    # ── Mutation A: drop the registration line, incremental ────────────────
    case.write_text(mutated, encoding="utf-8")
    bump_mtime(case)
    run(cc, db_path, project_root=root, project_id="f08incr",
        index_refs=True, index_embeddings=False, analyze_symbols=False,
        force=False)
    inc_refs_a, inc_fpa_a = snap_rows(db_path)

    # Reference rebuild for mutation A
    ref_dir = tmp_path / "f08_incr_ref"
    ref_db = ref_dir / "index.db"
    run(cc, ref_db, project_root=root, project_id="f08incr",
        index_refs=True, index_embeddings=False, analyze_symbols=False,
        force=True)
    ref_refs_a, ref_fpa_a = snap_rows(ref_db)

    assert inc_refs_a == ref_refs_a, (
        "mutation A: dispatch/register refs must be identical between "
        f"incremental ({inc_refs_a}) and rebuild ({ref_refs_a})")
    assert inc_fpa_a == ref_fpa_a, (
        "mutation A: register/init_list fp rows must be identical between "
        f"incremental ({inc_fpa_a}) and rebuild ({ref_fpa_a})")

    # ── Mutation B: re-add the registration line, incremental ──────────────
    case.write_text(original, encoding="utf-8")
    bump_mtime(case)
    run(cc, db_path, project_root=root, project_id="f08incr",
        index_refs=True, index_embeddings=False, analyze_symbols=False,
        force=False)
    inc_refs_b, inc_fpa_b = snap_rows(db_path)
    assert inc_refs_a != inc_refs_b, "mutation B must restore dispatch rows"

    run(cc, ref_db, project_root=root, project_id="f08incr",
        index_refs=True, index_embeddings=False, analyze_symbols=False,
        force=True)
    ref_refs_b, ref_fpa_b = snap_rows(ref_db)
    assert inc_refs_b == ref_refs_b, (
        f"mutation B: incremental {inc_refs_b} != rebuild {ref_refs_b}")
    assert inc_fpa_b == ref_fpa_b, (
        f"mutation B: incremental {inc_fpa_b} != rebuild {ref_fpa_b}")


def test_t08_production_tree_clean():
    """G5: the T08 slice indexes only sacrificial fixtures under tmp_path —
    the production worktree must stay clean (no .fw-context index dir in the
    repo tree, no stray git status noise introduced by this suite).
    """
    repo_root = Path(__file__).resolve().parents[1]
    assert not (repo_root / ".fw-context" / "index.db").exists(), (
        "the worktree must not carry an index DB at repo-root .fw-context")
    assert not (repo_root / "src" / ".fw-context").exists(), (
        "no index directory may be created under src/")
