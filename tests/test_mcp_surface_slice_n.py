"""T14 SLICE-N — T11 MCP surface & index-lifecycle parity oracle (ADR-006).

The oracle drives the REAL FastMCP protocol stack: every wire case opens an
in-memory ``ClientSession`` (``mcp.shared.memory``) against the module-level
``fw_context_mcp.mcp.server.mcp`` instance, so ``isError`` semantics, tool
registry contents, and schema validation behave exactly as over stdio.

Scope (SLICES.md SLICE-N block + TEST_PLAN §14):
1. wire_no_private_tool_names            — ``reindex_file_impl`` must not be public (a).
2. wire_registry_parity_readme_docs_docstring — 4-surface registry parity (g).
3. wire_business_error_is_isError_true   — handler error dicts → isError=true (c).
4. wire_maintenance_error_is_isError_true — maintenance business errors too (b)+(c).
5. wire_variant_star_source_annotated    — source tools carry the ADR-003 envelope (e).
6. wire_indirect_targets_preserves_db_note — dict passthrough keeps ``_note`` (f).
7. lifecycle_ollama_down_does_not_leave_indexing — try/finally finalize (d).
8. maintenance_tools_serialize_with_server_lock — _wrap_tool + _MAINT_LOCK (b).
9. main_guard_serves_stdio               — ``python -m fw_context_mcp.mcp.server`` (g).
GREEN block (must pass before AND after — regression guards): unknown-tool
isError, schema-violation isError, wildcard callgraph annotation, initialize
handshake, resources listing, get_active_build shape, T06 TS06_03/TS06_07.

Fixtures are throwaway C projects under %TEMP%\\fw_impl_n\\ (never inside the
repo tree — resolve_project_root would climb to the repo's .git), indexed with
``python -m fw_context_mcp.cli``; PYTHONPATH is pinned to THIS worktree's src
so the subprocesses cannot silently resolve the editable main checkout.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import queue as queue_mod
from pathlib import Path

import pytest

import fw_context_mcp

# ── Worktree pinning (anti-drift) ────────────────────────────────────────────
# pytest WITHOUT PYTHONPATH silently resolves the editable MAIN checkout
# (D:\\github\\fw-context-mcp\\src).  Pin the expected src to THIS test
# file's worktree and fail loudly when the import disagrees.
_WT_SRC = Path(__file__).resolve().parents[1] / "src"
_PKG_FILE = Path(fw_context_mcp.__file__).resolve()
assert _PKG_FILE.is_relative_to(_WT_SRC), (
    "fw_context_mcp resolved OUTSIDE this worktree's src: "
    f"{_PKG_FILE} (expected under {_WT_SRC}). "
    "Run pytest with PYTHONPATH pointing at the worktree src."
)

import fw_context_mcp.mcp.server as srv  # noqa: E402  (import AFTER the pin check)

from mcp.shared.memory import (  # noqa: E402
    create_connected_server_and_client_session,
)

SCRATCH = Path(os.environ.get("TEMP", str(Path.home()))) / "fw_impl_n"
SCRATCH.mkdir(parents=True, exist_ok=True)


# ── Protocol helpers ─────────────────────────────────────────────────────────
async def _wire(tool: str, args: dict):
    async with create_connected_server_and_client_session(srv.mcp) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def wire_call(tool: str, args: dict):
    """One full MCP round trip (initialize + call_tool) per invocation."""
    return asyncio.run(_wire(tool, args))


def wire_names() -> list[str]:
    async def _list():
        async with create_connected_server_and_client_session(srv.mcp) as session:
            await session.initialize()
            res = await session.list_tools()
            return [t.name for t in res.tools]

    return asyncio.run(_list())


def wire_body(result) -> object:
    """Parse the text content of a CallToolResult as JSON."""
    return json.loads(result.content[0].text)


def records_of(body) -> list[dict]:
    """Normalize dict / single-item-list / list handler payloads to records."""
    if isinstance(body, dict):
        return [body]
    return [r for r in body if isinstance(r, dict)]


@pytest.fixture(autouse=True)
def _fresh_server_locks():
    """Bind fresh asyncio locks per test: asyncio.Lock objects are bound to
    the first event loop that awaits them, and every wire_call spins its own
    loop via asyncio.run."""
    old_server = srv._SERVER_LOCK
    old_maint = getattr(srv, "_MAINT_LOCK", None)
    srv._SERVER_LOCK = asyncio.Lock()
    srv._MAINT_LOCK = asyncio.Lock()
    yield
    srv._SERVER_LOCK = old_server
    if old_maint is not None:
        srv._MAINT_LOCK = old_maint
    else:
        del srv._MAINT_LOCK


# ── Fixture builder (adapted from T11 research setup_fixture.py) ─────────────
UTIL_H = """#define UTIL_H
int util_add(int a, int b);
int util_mul(int a, int b);
#ifdef FEATURE_SLOW
int util_slow(int a, int b);
#endif
#endif
"""

UTIL_C = """#include "util.h"
int util_add(int a, int b) { return a + b; }
#ifdef FEATURE_SLOW
int util_slow(int a, int b) { return a * b + 1; }
#else
int util_mul(int a, int b) { return a * b; }
#endif
"""

MAIN_C = """#include "util.h"
int main(void) {
    return util_add(1, util_mul(2, 3));
}
"""

# boxA/boxB slot pattern (slice-H proven): the Phase 3c fn_ptr_type fallback
# attaches a DB-layer ``_note`` to find_indirect_targets("slot") rows.
NOTE_C = """\\
typedef void (*cb_t)(int);
static void worker(int x) { (void)x; }
struct boxA { cb_t slot; };
struct boxB { cb_t slot; };
struct boxA a1;
struct boxB b1;
void assign_a(void) { a1.slot = worker; }
void fire_b(int x) { b1.slot(x); }
"""

MV_CONFIG_EXTRA = """
default_variant = "master"

[[build.variants]]
name = "master"

[[build.variants]]
name = "slave"
"""


def _run_cli(cmd: list[str], cwd: Path, log: Path, index_home: Path) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_WT_SRC)
    env["FW_CONTEXT_INDEX_DIR"] = str(index_home)
    env["PYTHONIOENCODING"] = "utf-8"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(cmd)}  (cwd={cwd})\n")
        fh.flush()
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=900,
        )
        fh.write(proc.stdout)
        fh.write("\n--- stderr ---\n")
        fh.write(proc.stderr)
        fh.write(f"\n[exit code: {proc.returncode}]\n")
    return proc.returncode


def _seed_llm_analysis(fixture: Path, index_home: Path, name: str = "util_add") -> None:
    """Insert a pre-computed llm_analysis row for *name* in EVERY build of the
    fixture so explain_symbol takes the instant (non-LLM) path and returns a
    plain data record (no ``warning``).  Data-only seeding of a throwaway DB —
    no schema edits."""
    dbs = sorted((index_home).glob("**/index.db"))
    assert dbs, f"no index.db under {index_home}"
    for db_path in dbs:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id, config_hash FROM symbols WHERE name = ?", (name,)
            ).fetchall()
            for symbol_id, _cfg in rows:
                conn.execute(
                    """INSERT OR REPLACE INTO llm_analysis
                       (symbol_id, summary, inputs, outputs, model, content_hash)
                       VALUES (?, 'adds two ints', 'a, b', 'a+b', 'fixture-seed', 'seed')""",
                    (symbol_id,),
                )
            conn.commit()
        finally:
            conn.close()


def _build_mv_fixture() -> Path:
    fixture = SCRATCH / "fixture_mv"
    index_home = SCRATCH / "index_home_mv"
    log = SCRATCH / "fixture_build.log"
    if (fixture / ".fw-context" / "config.toml").exists():
        return fixture  # already built in a previous session
    fixture.mkdir(parents=True, exist_ok=True)
    index_home.mkdir(parents=True, exist_ok=True)
    for fname, text in (("util.h", UTIL_H), ("util.c", UTIL_C),
                        ("main.c", MAIN_C), ("note.c", NOTE_C)):
        (fixture / fname).write_text(text, encoding="utf-8")
    cc_dir = fixture / ".fw-context" / "build"
    cc_dir.mkdir(parents=True, exist_ok=True)
    for variant, tag, slow in (("master", "BASE", 1), ("slave", "ALT", 0)):
        cc = [
            {"directory": str(fixture),
             "command": f"clang -std=c11 -I. -DFEATURE_SLOW={slow} -D{tag}_BUILD=1 -c util.c -o util{tag}.o",
             "file": "util.c"},
            {"directory": str(fixture),
             "command": f"clang -std=c11 -I. -D{tag}_BUILD=1 -c main.c -o main{tag}.o",
             "file": "main.c"},
            {"directory": str(fixture),
             "command": f"clang -std=c11 -I. -D{tag}_BUILD=1 -c note.c -o note{tag}.o",
             "file": "note.c"},
        ]
        (cc_dir / f"compile_commands.{variant}.json").write_text(
            json.dumps(cc, indent=1), encoding="utf-8")
    # Deterministic fixture: LLM off; the index DB lives in the scratch home.
    (fixture / ".fw-context" / "local.toml").write_text(
        "[llm]\nenabled = false\n\n[index]\n"
        f'db_dir = "{index_home.as_posix()}"\n',
        encoding="utf-8")
    py = sys.executable
    _run_cli([py, "-m", "fw_context_mcp.cli", "init", "--non-interactive",
              "--skip-doctor", "--skip-build"], fixture, log, index_home)
    cfg = fixture / ".fw-context" / "config.toml"
    base = cfg.read_text(encoding="utf-8")
    if "[[build.variants]]" not in base:
        base = base.replace("[build]", "[build]" + MV_CONFIG_EXTRA, 1)
        cfg.write_text(base, encoding="utf-8")
    for variant in ("master", "slave"):
        rc = _run_cli([py, "-m", "fw_context_mcp.cli", "index", "--variant", variant,
                       "--no-analyze", "--no-embeddings"], fixture, log, index_home)
        assert rc == 0, f"fixture index ({variant}) failed — see {log}"
    _seed_llm_analysis(fixture, index_home)
    return fixture


def _build_poison_fixture() -> tuple[Path, Path]:
    """Single-build fixture whose project config forces an LLM probe against a
    deterministically closed loopback port (deterministic httpx.ConnectError
    even on a host where Ollama happens to run)."""
    fixture = SCRATCH / "fixture_poison"
    index_home = SCRATCH / "index_home_poison"
    log = SCRATCH / "fixture_poison_build.log"
    if (fixture / ".fw-context" / "config.toml").exists():
        return fixture, index_home
    fixture.mkdir(parents=True, exist_ok=True)
    index_home.mkdir(parents=True, exist_ok=True)
    for fname, text in (("util.h", UTIL_H), ("util.c", UTIL_C), ("main.c", MAIN_C)):
        (fixture / fname).write_text(text, encoding="utf-8")
    cc_dir = fixture / ".fw-context" / "build"
    cc_dir.mkdir(parents=True, exist_ok=True)
    cc = [
        {"directory": str(fixture),
         "command": "clang -std=c11 -I. -c util.c -o util.o", "file": "util.c"},
        {"directory": str(fixture),
         "command": "clang -std=c11 -I. -c main.c -o main.o", "file": "main.c"},
    ]
    (cc_dir / "compile_commands.json").write_text(json.dumps(cc), encoding="utf-8")
    (fixture / ".fw-context" / "local.toml").write_text(
        '[llm]\nenabled = true\nollama_url = "http://127.0.0.1:9"\n\n[index]\n'
        f'db_dir = "{index_home.as_posix()}"\n',
        encoding="utf-8")
    py = sys.executable
    _run_cli([py, "-m", "fw_context_mcp.cli", "init", "--non-interactive",
              "--skip-doctor"], fixture, log, index_home)
    return fixture, index_home


@pytest.fixture(scope="session")
def mv_fixture() -> Path:
    return _build_mv_fixture()


@pytest.fixture(scope="session")
def poison_fixture() -> tuple[Path, Path]:
    return _build_poison_fixture()


# ═════════════════════════════ RED block ═════════════════════════════════════

def test_wire_no_private_tool_names():
    """(a) ADR-006: ``_*`` / ``*_impl`` names never appear on the wire."""
    names = wire_names()
    private = [n for n in names if n.startswith("_") or n.endswith("_impl")]
    assert not private, (
        f"private helper names are publicly registered MCP tools: {private}. "
        "reindex_file_impl is an in-process alias, not a wire tool (ADR-006 a).")


def test_wire_registry_parity_readme_docs_docstring():
    """(g) ADR-006: wire == README-MCP.md == docs/tools.md == server docstring."""
    wt = _WT_SRC.parent
    wire = set(wire_names())

    readme_lines = (wt / "README-MCP.md").read_text(encoding="utf-8").splitlines()
    readme_names: set[str] = set()
    for line in readme_lines:
        if line.startswith("| ") and "`" in line:
            readme_names.update(re.findall(r"`([a-zA-Z_][a-zA-Z_0-9]*)`", line))
    readme_count_match = re.search(r"\*\*(\d+) tools\*\*", "\n".join(readme_lines))
    assert readme_count_match, "README-MCP.md must state its tool count"
    readme_count = int(readme_count_match.group(1))

    docs_lines = (wt / "docs" / "tools.md").read_text(encoding="utf-8").splitlines()
    in_mcp = False
    docs_names: set[str] = set()
    for line in docs_lines:
        if line.startswith("## "):
            in_mcp = line.strip().lower() == "## mcp tools"
            continue
        if in_mcp:
            m = re.match(r"^#### `([a-zA-Z_][a-zA-Z_0-9]*)`", line)
            if m:
                docs_names.add(m.group(1))

    doc = srv.__doc__ or ""
    doc_match = re.search(r"Serves (\d+) MCP tools", doc)
    assert doc_match, "server.py module docstring must state its tool count"
    doc_count = int(doc_match.group(1))

    problems = []
    if wire != readme_names:
        problems.append(
            f"wire-vs-README drift: wire-only={sorted(wire - readme_names)} "
            f"README-only={sorted(readme_names - wire)}")
    if wire != docs_names:
        problems.append(
            f"wire-vs-docs/tools.md drift: wire-only={sorted(wire - docs_names)} "
            f"docs-only={sorted(docs_names - wire)}")
    if len(wire) != readme_count:
        problems.append(f"README count literal {readme_count} != wire {len(wire)}")
    if len(wire) != doc_count:
        problems.append(f"docstring count literal {doc_count} != wire {len(wire)}")
    if "reindex_file_impl" in doc:
        problems.append("server docstring still advertises the private alias reindex_file_impl")
    assert not problems, "4-surface registry parity broken (ADR-006 g): " + "; ".join(problems)


def test_wire_business_error_is_isError_true(mv_fixture):
    """(c) ADR-006: a handler business error leaves the wire as isError=true."""
    result = wire_call("get_source", {
        "name": "no_such_symbol_xyz_n", "project_root": str(mv_fixture)})
    assert result.isError is True, (
        "business error payload rode a SUCCESS result (isError=false): "
        f"isError={result.isError} body={result.content[0].text[:300]}")
    body = wire_body(result)
    assert isinstance(body, dict) and "error" in body, (
        f"isError=true content must still carry the JSON error body: {body}")


def test_wire_maintenance_error_is_isError_true(tmp_path):
    """(b)+(c) ADR-006: maintenance handlers report errors as isError=true.
    ``list_variants`` catches the missing-index failure and returns a business
    error dict ({"builds": [], "error": ...}) — the exact C3b shape that used
    to ride a SUCCESS result.  (reset_index RAISES for this case, which the
    SDK already converts — the dict-returning path is the parity gap.)"""
    result = wire_call("list_variants", {"project_root": str(tmp_path)})
    assert result.isError is True, (
        "maintenance business error rode a SUCCESS result (isError=false): "
        f"isError={result.isError} body={result.content[0].text[:300]}")
    body = wire_body(result)
    assert isinstance(body, dict) and "error" in body


def test_wire_variant_star_source_annotated(mv_fixture):
    """(e) ADR-006: EVERY source tool under variant='*' returns one record per
    build scope, each carrying the ADR-003 provenance envelope (variant /
    config_hash).  SLICE-E fanned out get_source / get_symbol_context /
    read_file; explain_symbol and get_file_map still bypass scoped execution
    and answer from one arbitrary build, unannotated."""
    mv = str(mv_fixture)
    cases = [
        ("get_source", {"name": "util_add"}),
        ("get_symbol_context", {"name": "util_add"}),
        ("read_file", {"file_path": "util.c"}),
        ("get_file_map", {"file_path": "util.c"}),
        ("explain_symbol", {"name": "util_add"}),
    ]
    problems = []
    for tool, args in cases:
        result = wire_call(tool, {**args, "variant": "*", "project_root": mv})
        body = wire_body(result)
        recs = records_of(body)
        data = [r for r in recs if "error" not in r and "warning" not in r]
        if not data:
            problems.append(f"{tool}: no annotatable data records under variant='*': {str(body)[:200]}")
            continue
        for rec in data:
            if not rec.get("variant"):
                problems.append(
                    f"{tool}: record without the provenance envelope: "
                    f"keys={sorted(rec)[:12]}")
            if not rec.get("config_hash"):
                problems.append(
                    f"{tool}: record without config_hash provenance: keys={sorted(rec)[:12]}")
    assert not problems, "variant='*' source annotation broken (ADR-006 e): " + "; ".join(problems)


def test_wire_indirect_targets_preserves_db_note(mv_fixture):
    """(f) + T06::TS06_03: the DB-layer ``_note`` of the fn_ptr_type fallback
    must reach the WIRE verbatim (not only the direct handler call that
    SLICE-H already proved)."""
    result = wire_call("find_indirect_targets", {
        "name": "slot", "project_root": str(mv_fixture)})
    assert result.isError is not True, (
        f"find_indirect_targets failed: {result.content[0].text[:300]}")
    body = wire_body(result)
    recs = [r for r in records_of(body) if "error" not in r and "info" not in r]
    assert recs, f"no data rows for the boxA→boxB fallback: {str(body)[:300]}"
    assert any(r.get("_note") for r in recs), (
        "DB-layer _note was dropped between the DB layer and the wire "
        f"(T06 TS06_03): rows={str(body)[:400]}")


def test_lifecycle_ollama_down_does_not_leave_indexing(poison_fixture):
    """(d) ADR-006: an aborted postprocess (httpx.ConnectError — not in
    SAFE_EXCEPT) must still stamp finalize_manifest.  ``fw-context index`` may
    exit non-zero, but no build may stay stuck at manifest_verification
    'indexing' (the C4 poison loop: every retry re-aborts the same build)."""
    fixture, index_home = poison_fixture
    log = SCRATCH / "poison_index_run.log"
    py = sys.executable
    rc = _run_cli([py, "-m", "fw_context_mcp.cli", "index"], fixture, log, index_home)
    db_path = next(index_home.glob("**/index.db"), None)
    assert db_path is not None, f"no index.db produced — see {log}"
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT config_hash, manifest_verification FROM build_configs"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "poison build row missing — the index never reached build_configs"
    stuck = [(c, v) for c, v in rows if v == "indexing"]
    assert not stuck, (
        f"index exited rc={rc} but left builds stuck at 'indexing': {stuck} "
        "(C4 poison loop — httpx.ConnectError escaped the postprocess step "
        "net and finalize_manifest never stamped the build)")


def test_maintenance_tools_serialize_with_server_lock(tmp_path):
    """(b) ADR-006: maintenance tools run under _wrap_tool + _MAINT_LOCK and
    SERIALIZE against _SERVER_LOCK holders (C7): while a query holds the
    server lock, a maintenance call must wait instead of racing it; a wrapped
    query keeps its 5 s busy rejection.  Both scenarios share ONE event loop
    (asyncio.Lock objects bind to their first loop)."""
    async def _both_scenarios():
        async with create_connected_server_and_client_session(srv.mcp) as session:
            await session.initialize()
            await srv._SERVER_LOCK.acquire()
            try:
                maint = asyncio.create_task(session.call_tool(
                    "list_variants", {"project_root": str(tmp_path)}))
                done, _ = await asyncio.wait({maint}, timeout=1.5)
                maint_ran_while_locked = maint in done
            finally:
                srv._SERVER_LOCK.release()
            maint_result = await asyncio.wait_for(maint, timeout=20)

        async with create_connected_server_and_client_session(srv.mcp) as session:
            await session.initialize()
            await srv._SERVER_LOCK.acquire()
            try:
                lookup = asyncio.create_task(session.call_tool(
                    "lookup_symbol", {"name": "x", "project_root": str(tmp_path)}))
                lookup_result = await asyncio.wait_for(lookup, timeout=8)
            finally:
                srv._SERVER_LOCK.release()
        return maint_ran_while_locked, maint_result, lookup_result

    maint_ran_while_locked, maint_result, lookup_result = asyncio.run(_both_scenarios())
    assert not maint_ran_while_locked, (
        "maintenance tool executed WHILE a query held _SERVER_LOCK (C7 race: "
        "list_variants/reindex_file can read a half-mutated index under a "
        "concurrent query)")
    maint_body = wire_body(maint_result)
    assert isinstance(maint_body, dict) and "error" in maint_body, (
        f"list_variants on a non-indexed dir should be a business error: {maint_body}")
    assert "busy" in lookup_result.content[0].text or (
        "processing another query" in lookup_result.content[0].text), (
        "wrapped tool must be busy-rejected while the server lock is held: "
        f"{lookup_result.content[0].text[:200]}")


def _readline_with_timeout(stream, timeout_s: float) -> str:
    q: queue_mod.Queue = queue_mod.Queue()

    def _pump():
        try:
            q.put(stream.readline())
        except Exception as exc:  # pragma: no cover - pump failure
            q.put("")

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout_s)
    except queue_mod.Empty:
        return ""


def test_main_guard_serves_stdio(tmp_path):
    """(g) ADR-006: ``python -m fw_context_mcp.mcp.server`` must serve stdio,
    not exit silently (README tells the assistant to start the server; the
    module had no __main__ guard)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_WT_SRC)
    env["FW_CONTEXT_INDEX_DIR"] = str(tmp_path / "idx")
    proc = subprocess.Popen(
        [sys.executable, "-m", "fw_context_mcp.mcp.server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(tmp_path), env=env,
        text=True, encoding="utf-8",
    )
    try:
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "slice-n-probe", "version": "0"}}}
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = _readline_with_timeout(proc.stdout, 90)
        assert line, (
            "python -m fw_context_mcp.mcp.server produced no initialize "
            "response — the module is a silent no-op (no __main__ guard)")
        resp = json.loads(line)
        name = (resp.get("result") or {}).get("serverInfo", {}).get("name")
        assert name == "fw-context", f"unexpected serverInfo: {resp}"
    finally:
        proc.kill()
        proc.wait(timeout=15)


# ═══════════════════════ GREEN block (regression guards) ═════════════════════

def test_wire_unknown_tool_isError_true():
    result = wire_call("no_such_tool_n", {})
    assert result.isError is True, "unknown tool must be an isError=true call result"


def test_wire_schema_violation_isError_true(mv_fixture):
    result = wire_call("lookup_symbol", {
        "name": "x", "bogus_arg_not_in_schema": 1,
        "project_root": str(mv_fixture)})
    assert result.isError is True, (
        "schema violation must be an isError=true call result, got "
        f"isError={result.isError} text={result.content[0].text[:200]}")


def test_wire_variant_star_callgraph_annotated(mv_fixture):
    """T06::TS06_07 on the wire: scoped callgraph tools carry the envelope."""
    result = wire_call("find_callers", {
        "name": "util_add", "variant": "*", "project_root": str(mv_fixture)})
    assert result.isError is not True, (
        f"find_callers failed: {result.content[0].text[:300]}")
    body = wire_body(result)
    recs = [r for r in records_of(body) if "error" not in r and "info" not in r]
    assert recs, f"no data records: {str(body)[:200]}"
    for rec in recs:
        assert rec.get("variant"), f"callgraph record without variant: keys={sorted(rec)[:10]}"


def test_regression_initialize_handshake():
    async def _init():
        async with create_connected_server_and_client_session(srv.mcp) as session:
            res = await session.initialize()
            return res

    res = asyncio.run(_init())
    assert res.serverInfo.name == "fw-context"


def test_regression_resources_list():
    async def _res():
        async with create_connected_server_and_client_session(srv.mcp) as session:
            await session.initialize()
            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            return resources, templates

    resources, templates = asyncio.run(_res())
    uris = {str(r.uri) for r in resources.resources}
    assert "fw-context://stats" in uris
    assert "fw-context://projects" in uris
    assert any("symbols/{name}" in str(t.uriTemplate)
               for t in templates.resourceTemplates)


def test_regression_get_active_build_shape(mv_fixture):
    result = wire_call("get_active_build", {"project_root": str(mv_fixture)})
    assert result.isError is not True, (
        f"get_active_build failed: {result.content[0].text[:300]}")
    body = wire_body(result)
    for key in ("status", "config_hash", "project_root", "symbol_count",
                "manifest_verification"):
        assert key in body, f"get_active_build lost key {key}: {sorted(body)[:15]}"
    assert body["status"] in ("ready", "reindexing", "reindex_needed")
    # Multi-variant fixture: the variant discovery surface must be present.
    assert body.get("multi") is True, (
        f"fixture_mv is multi-variant; expected multi=true: {body.get('multi')}")
