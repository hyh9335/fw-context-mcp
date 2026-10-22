"""SLICE-I dead-code reachability oracle (T14 TEST_PLAN §9).

Focused tests for the T07 dead-code reachability L2 layer (ADR-009).
Case ids mirror the frozen adjudication oracles in
``.fw-orchestrator/artifacts/T07/test_spec.yaml``:

RED (must fail pre-fix, pass post-fix):
- T07::TC07-01_dead_scc_graph_unreachable — dead_a/dead_b (mutual
  recursion, zero inbound from the live set) are reported with
  ``status='graph_unreachable'`` + SCC member list +
  ``evidence_level='L2'``; pre-fix they are silently absent because the
  single-layer anti-join only sees their mutual ``call`` refs.
- T07::TC07-03_entry_symbol_not_dead — ``_start`` (the stored
  ``build_configs.entry_point``) is never 'dead' or
  'graph_unreachable'; it is the BFS root with root evidence
  'entry_point'.
- T07::TC07-04_weak_alias_target_rooted — ``alias_target``, referenced
  only through a ``weak, alias("alias_target")`` declaration, is rooted
  through the index-time alias edge (ref_kind='alias') and is not
  reported dead.
- T07::TC07-05_constructor_root — ``global_ctor`` carries a
  ``ref_kind='ctor_root'`` edge (``__attribute__((constructor))``) and
  no ghost self-call on its signature line; the binary oracle part is
  capability-guarded.
- T07::TC07-06_used_retain_keep_root — ``kept_by_script`` stores the
  ``.keepme`` section flag + ``used`` attr flag; the linker-script
  KEEP reader records the pattern; the function is not reported
  dead/graph_unreachable.  Binary oracle part capability-guarded.
- T07::TC07-09_vector_handler_uncertain_not_dead — ``isr_vector_handler``
  (``ref_kind='indirect'`` with ``slot_index=0``) is never 'dead'; its
  reason references the vector slot instead of the misleading
  "call sites unresolved" wording.

GREEN (capability-guarded, SKIP(capability) honest):
- T07::TC07-08_dead_scc_removed_by_gc — the ld.lld --gc-sections binary
  truth for the same fixture (removed/retained section sets).  Passes
  at RED and GREEN by design: it anchors the binary oracle, it does not
  depend on the index fix.

Invariants (guard — may pass at RED, must hold at GREEN):
- status set ⊆ {dead, possibly_dead, graph_unreachable};
- zero-ref symbols without root evidence stay 'dead' (root-less stay dead);
- every reported row carries evidence_level; absolute paths at the MCP layer.

Fixture: the frozen F07/T07 freestanding fixture (dead.c + keep.ld),
materialized verbatim under pytest ``tmp_path`` (a %TEMP% subdir); every
index DB lives under that tmp tree via the project ``config.toml``
``db_dir``.  The repository ``.fw-context`` and the global
``~/.fw-context`` registry are never touched.

NOTE: deliberately NOT marked ``libclang`` — the conftest auto-skip keys
on gcc, which is absent on this host; the pip libclang wheel is
self-sufficient.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from fw_context_mcp.indexer.compile_commands import CompilationUnit
from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run
from fw_context_mcp.indexer.symbols import extract_all

PROJECT_ID = "t07reachability0a0b0c0d0e0f0a0b0c0d"

LLVM_DIR = Path(r"C:\Program Files\LLVM\bin")

# ── Frozen fixture (verbatim from .fw-orchestrator/artifacts/T07/reproduction/
#    fixture/dead.c — line numbers are load-bearing: TC07-02's ghost line is 31,
#    TC07-05/06/07's def lines are 38/43, TC07-09's slot line is 63) ──────────

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

KEEP_LD = """\
ENTRY(_start)

SECTIONS
{
    .isr_vector_out : { KEEP(*(.isr_vector)) }
    .keepme_out : { KEEP(*(.keepme)) }
}
"""

# frozen def lines in DEAD_C (1-based)
LINE_GLOBAL_CTOR = 38
LINE_KEPT_BY_SCRIPT = 43
LINE_GHOST_CALL = 31
LINE_VECTORS_ARRAY = 63


# ── Harness (same isolation pattern as the SLICE-G/H suites) ────────────────

CONFIG_TMPL = """\
[project]
name = "t07-reachability"
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
                "-ffunction-sections", "-fdata-sections",
                "-c", name, "-o", f"build/o{i}.o",
            ],
        }
        for i, name in enumerate(files)
    ]
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload), encoding="utf-8")
    return cc


def _make_project(tmp_path: Path, name: str, files: dict[str, str],
                  store_linker_script: bool = False):
    """Fixture project + isolated index dir + full index → (root, db_path, hash).

    When *store_linker_script* is True, keep.ld is fed through
    ``store_scripts`` so ``build_configs.entry_point`` is stored — the
    TC07-03 setup step (runner skips linker scripts for build-unknown
    projects: the documented C7 limitation, so the test drives the
    reader directly, exactly like the T07 reproduction driver did).
    """
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
    if store_linker_script:
        from fw_context_mcp.indexer import linker_script as ls

        conn_rw = sqlite3.connect(str(db_path))
        conn_rw.row_factory = sqlite3.Row
        try:
            ls.store_scripts(conn_rw, ch, [root / "keep.ld"], root)
            conn_rw.commit()
        finally:
            conn_rw.close()
    return root, db_path, ch


def _dead_by_name(conn: sqlite3.Connection, ch: str, **kw) -> dict[str, dict]:
    from fw_context_mcp.indexer.db import find_dead_code

    rows = find_dead_code(conn, ch, limit=200, project_only=False, **kw)
    return {r["name"]: r for r in rows}


def _refs_for(conn: sqlite3.Connection, ch: str, name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.* FROM refs r
           JOIN symbols s ON s.usr = r.to_usr AND s.config_hash = r.config_hash
           WHERE r.config_hash = ? AND s.name = ?
           ORDER BY r.ref_kind, r.from_line""",
        (ch, name),
    ).fetchall()


# ── T07::TC07-01_dead_scc_graph_unreachable ─────────────────────────────────

def test_t07_tc07_01_dead_scc_graph_unreachable(tmp_path):
    """dead_a/dead_b (mutual recursion, zero live inbound) must be reported
    as graph_unreachable with their SCC member list and evidence_level L2.

    Pre-fix the single-layer anti-join only sees their mutual call refs, so
    BOTH are silently absent from find_dead_code — the exact false negative
    the linker GC exposes (linker removes .text.dead_a/.text.dead_b)."""
    _, db_path, ch = _make_project(tmp_path, "scc", {"dead.c": DEAD_C})
    conn = open_db(db_path)
    try:
        dead = _dead_by_name(conn, ch)
        assert "dead_a" in dead, (
            "dead_a absent from find_dead_code — the dead SCC is invisible to the "
            f"single-layer check (TC07-01): {sorted(dead)}")
        assert "dead_b" in dead, (
            "dead_b absent from find_dead_code — the dead SCC is invisible to the "
            f"single-layer check (TC07-01): {sorted(dead)}")
        for name in ("dead_a", "dead_b"):
            row = dead[name]
            assert row["status"] == "graph_unreachable", (
                f"{name} must be graph_unreachable, got {row['status']!r}: {row}")
            assert row.get("evidence_level") == "L2", (
                f"{name} must carry evidence_level='L2': {row}")
        scc = set(dead["dead_a"].get("scc") or [])
        assert {"dead_a", "dead_b"} <= scc, (
            f"SCC member list must contain both dead functions: {scc}")
        # root-less stay dead — the control: a zero-ref function without any
        # root evidence is still plain 'dead' (baseline behavior preserved).
        assert dead.get("isr_bare", {}).get("status") == "dead", (
            f"zero-ref root-less isr_bare must stay 'dead': {dead.get('isr_bare')}")
    finally:
        conn.close()


# ── T07::TC07-03_entry_symbol_not_dead ──────────────────────────────────────

def test_t07_tc07_03_entry_symbol_not_dead(tmp_path):
    """_start (stored build_configs.entry_point) is the BFS root: never
    'dead', never 'graph_unreachable', with root evidence 'entry_point'
    observable through the reachability-root oracle."""
    root, db_path, ch = _make_project(
        tmp_path, "entry", {"dead.c": DEAD_C, "keep.ld": KEEP_LD},
        store_linker_script=True,
    )
    conn = open_db(db_path)
    try:
        ep = conn.execute(
            "SELECT entry_point FROM build_configs WHERE config_hash=?", (ch,)
        ).fetchone()
        assert ep and ep["entry_point"] == "_start", (
            f"fixture must store entry_point='_start' (TC07-03 setup), got {ep and ep['entry_point']!r}")

        from fw_context_mcp.indexer.db import find_reachability_roots

        dead = _dead_by_name(conn, ch)
        assert "_start" not in dead, (
            f"_start must never be reported dead/graph_unreachable; got {dead.get('_start')}")
        roots = {r["name"]: r for r in find_reachability_roots(conn, ch)}
        assert "_start" in roots, (
            f"_start missing from the reachability root set: {sorted(roots)}")
        assert "entry_point" in (roots["_start"].get("evidence") or ""), (
            f"_start root evidence must name 'entry_point': {roots.get('_start')}")
    finally:
        conn.close()


# ── T07::TC07-04_weak_alias_target_rooted ───────────────────────────────────

def test_t07_tc07_04_weak_alias_target_rooted(tmp_path):
    """alias_target — referenced only through the alias("alias_target")
    STRING on a declaration — must be rooted via the index-time alias edge
    (ref_kind='alias') and never reported dead.  Pre-fix no refs row points
    at alias_target at all, so the anti-join flags it 'dead' while the
    linker keeps .text.alias_target via the alias relocation."""
    _, db_path, ch = _make_project(tmp_path, "alias", {"dead.c": DEAD_C})
    conn = open_db(db_path)
    try:
        alias_refs = _refs_for(conn, ch, "alias_target")
        alias_edge = [r for r in alias_refs if r["ref_kind"] == "alias"]
        assert alias_edge, (
            "no ref_kind='alias' edge to alias_target — the alias attribute "
            f"string is never parsed (TC07-04). refs: "
            f"{[dict(r) for r in alias_refs]}")

        from fw_context_mcp.indexer.db import find_reachability_roots

        dead = _dead_by_name(conn, ch)
        assert "alias_target" not in dead, (
            f"alias_target must not be reported dead; got {dead.get('alias_target')}")
        roots = {r["name"]: r for r in find_reachability_roots(conn, ch)}
        assert "alias_target" in roots, (
            f"alias_target missing from the reachability root set: {sorted(roots)}")
        assert "alias" in (roots["alias_target"].get("evidence") or ""), (
            f"alias_target root evidence must name 'alias': {roots.get('alias_target')}")
    finally:
        conn.close()


# ── T07::TC07-05_constructor_root ───────────────────────────────────────────

def test_t07_tc07_05_constructor_root(tmp_path):
    """global_ctor (__attribute__((constructor))) must be rooted via
    ref_kind='ctor_root' WITHOUT any ghost self-call on its signature line
    (the C6 self-ghost stays fixed — SLICE-A).  The binary half of the
    oracle (.init_array keeps the section through --gc-sections) runs when
    clang+ld.lld are available, else SKIP(capability) honestly."""
    _, db_path, ch = _make_project(tmp_path, "ctor", {"dead.c": DEAD_C})
    conn = open_db(db_path)
    try:
        ctor_refs = _refs_for(conn, ch, "global_ctor")
        assert any(r["ref_kind"] == "ctor_root" for r in ctor_refs), (
            "no ref_kind='ctor_root' ref for global_ctor — the constructor "
            f"attribute is invisible to the index (TC07-05). refs: "
            f"{[dict(r) for r in ctor_refs]}")
        # C6 guard: no ghost self-call on the signature line.
        self_calls = [r for r in _refs_for(conn, ch, "global_ctor")
                      if r["ref_kind"] == "call" and r["from_usr"] == r["to_usr"]]
        assert not self_calls, (
            f"ghost self-call refs on global_ctor must not exist (TC07-07/C6): "
            f"{[dict(r) for r in self_calls]}")

        dead = _dead_by_name(conn, ch)
        assert "global_ctor" not in dead, (
            f"global_ctor must not be reported dead/graph_unreachable; got {dead.get('global_ctor')}")

        from fw_context_mcp.indexer.db import find_reachability_roots

        roots = {r["name"]: r for r in find_reachability_roots(conn, ch)}
        assert "global_ctor" in roots, (
            f"global_ctor missing from the reachability root set: {sorted(roots)}")
        assert "ctor_root" in (roots["global_ctor"].get("evidence") or ""), (
            f"global_ctor root evidence must name 'ctor_root': {roots.get('global_ctor')}")
    finally:
        conn.close()


# ── T07::TC07-06_used_retain_keep_root ──────────────────────────────────────

def test_t07_tc07_06_used_retain_keep_root(tmp_path):
    """kept_by_script (used + section(".keepme") + KEEP in the linker
    script): the section flag and the attr flags are stored on the symbol
    row, the linker-script KEEP reader records the pattern, and the
    function is not reported dead/graph_unreachable.  The binary half of
    the oracle runs when clang+ld.lld are available, else SKIP(capability)."""
    root, db_path, ch = _make_project(
        tmp_path, "keep", {"dead.c": DEAD_C, "keep.ld": KEEP_LD},
        store_linker_script=True,
    )
    conn = open_db(db_path)
    try:
        sym = conn.execute(
            "SELECT section, attr_flags FROM symbols WHERE config_hash=? AND name='kept_by_script' AND is_definition=1",
            (ch,),
        ).fetchone()
        assert sym is not None, "kept_by_script definition missing from symbols"
        assert sym["section"] == ".keepme", (
            f"section flag not stored (TC07-06): section={sym['section']!r}")
        assert "used" in (sym["attr_flags"] or ""), (
            f"'used' attr flag not stored (TC07-06): attr_flags={sym['attr_flags']!r}")

        from fw_context_mcp.indexer import linker_script as ls

        patterns = ls.keep_patterns(root / "keep.ld")
        assert ".keepme" in patterns, (
            f"KEEP reader did not record the .keepme pattern (TC07-06): {patterns}")

        from fw_context_mcp.indexer.db import find_reachability_roots

        dead = _dead_by_name(conn, ch, keep_patterns=patterns)
        assert "kept_by_script" not in dead, (
            f"kept_by_script must not be reported dead/graph_unreachable; got {dead.get('kept_by_script')}")
        roots = {r["name"]: r for r in find_reachability_roots(conn, ch, keep_patterns=patterns)}
        assert "kept_by_script" in roots, (
            f"kept_by_script missing from the reachability root set: {sorted(roots)}")
        ev = " ".join(roots["kept_by_script"].get("evidence") or [])
        assert ("keep" in ev) or ("used" in ev), (
            f"kept_by_script root evidence must name the KEEP/used root: {roots.get('kept_by_script')}")
    finally:
        conn.close()


# ── T07::TC07-08_dead_scc_removed_by_gc (GREEN, capability-guarded) ─────────

def _llvm_tool(name: str) -> str | None:
    p = LLVM_DIR / f"{name}.exe"
    if p.exists():
        return str(p)
    found = shutil.which(name)
    return found


def test_t07_tc07_08_dead_scc_removed_by_gc(tmp_path):
    """Binary oracle O_LINKER_GC: ld.lld --gc-sections removes exactly the
    dead set (dead_a, dead_b, ghost_target, ghost_caller, isr_bare) and
    keeps the rooted set.  Capability-guarded: without clang+ld.lld the
    case is SKIPPED(capability) — an honest skip, never a fabricated pass."""
    clang = _llvm_tool("clang")
    lld = _llvm_tool("ld.lld")
    nm = _llvm_tool("llvm-nm")
    if not (clang and lld and nm):
        pytest.skip("capability: clang + ld.lld + llvm-nm not available on this host")

    fix = tmp_path / "gc"
    fix.mkdir()
    (fix / "dead.c").write_text(DEAD_C, encoding="utf-8")
    (fix / "keep.ld").write_text(KEEP_LD, encoding="utf-8")
    elf = fix / "dead.elf"

    comp = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-std=c11",
         "-ffreestanding", "-nostdlibinc",
         "-ffunction-sections", "-fdata-sections",
         "-c", "dead.c", "-o", "dead.o"],
        cwd=str(fix), capture_output=True, text=True,         encoding="utf-8", errors="replace",
    )
    assert comp.returncode == 0, f"clang compile failed: {comp.stderr[-800:]}"
    link = subprocess.run(
        [lld, "dead.o", "-o", "dead.elf", "--entry=_start",
         "--gc-sections", "--print-gc-sections", "-Map=dead.map",
         "-T", "keep.ld", "-nostdlib", "--noinhibit-exec"],
        cwd=str(fix), capture_output=True, text=True,         encoding="utf-8", errors="replace",
    )
    assert link.returncode == 0 and elf.exists(), (
        f"ld.lld gc link failed: {link.stderr[-800:]}")
    nm_out = subprocess.run(
        [nm, "dead.elf"], cwd=str(fix), capture_output=True, text=True,         encoding="utf-8", errors="replace",
    )

    gc_log = link.stdout + link.stderr
    removed_raw = sorted(set(re.findall(r"removing unused section (\S+)", gc_log)))
    removed = sorted(x.split(":", 1)[1].strip("()") for x in removed_raw if ":" in x)
    present = set(re.findall(r"[0-9a-fA-F]+\s+\w+\s+(\S+)", nm_out.stdout))

    for fn in ("dead_a", "dead_b", "ghost_target", "ghost_caller", "isr_bare"):
        assert f".text.{fn}" in removed, (
            f"binary truth: .text.{fn} must be gc-removed; removed={removed}")
    for fn in ("_start", "global_ctor", "kept_by_script", "alias_target",
               "isr_vector_handler", "live_a", "live_b", "user_of_alias"):
        assert fn in present, (
            f"binary truth: {fn} must survive --gc-sections; nm={sorted(present)}")


# ── T07::TC07-09_vector_handler_uncertain_not_dead ──────────────────────────

def test_t07_tc07_09_vector_handler_uncertain_not_dead(tmp_path):
    """isr_vector_handler (ref_kind='indirect', slot_index=0) is never
    'dead'; post-fix its reason references the vector slot (root evidence
    'vector_slot:0') instead of the misleading 'call sites unresolved'."""
    _, db_path, ch = _make_project(tmp_path, "vector", {"dead.c": DEAD_C})
    conn = open_db(db_path)
    try:
        slot_rows = [r for r in _refs_for(conn, ch, "isr_vector_handler")
                     if r["ref_kind"] == "indirect" and r["slot_index"] is not None]
        assert slot_rows, (
            "no indirect ref with slot_index for isr_vector_handler "
            f"(TC07-09 setup): {[dict(r) for r in _refs_for(conn, ch, 'isr_vector_handler')]}")
        assert slot_rows[0]["slot_index"] == 0

        dead = _dead_by_name(conn, ch)
        row = dead.get("isr_vector_handler")
        assert row is not None, (
            "isr_vector_handler absent from find_dead_code — pre-fix it is "
            "possibly_dead; post-fix it stays reported with vector-slot evidence")
        assert row["status"] != "dead", (
            f"isr_vector_handler must never be plain 'dead': {row}")
        reason = row.get("reason") or ""
        root_ev = row.get("root_evidence") or ""
        assert ("vector" in reason.lower()) or ("vector" in root_ev.lower()), (
            "the possibly_dead reason must reference the vector slot, not the "
            f"misleading 'call sites unresolved' wording (TC07-09): reason={reason!r}, "
            f"root_evidence={root_ev!r}")
        assert "call sites unresolved" not in reason, (
            f"misleading default reason survived for a vector handler: {reason!r}")
        assert root_ev == "vector_slot:0", (
            f"root_evidence must name the slot: {root_ev!r}")
    finally:
        conn.close()


# ── Invariants block (guard; TEST_PLAN §9) ──────────────────────────────────

def test_t07_invariants_status_set_and_absolute_paths(tmp_path):
    """Status set ⊆ {dead, possibly_dead, graph_unreachable}; every row
    carries evidence_level; absolute file paths at the MCP layer; the
    include_possible opt-in is honored (POSSIBLE edges traverse only when
    requested, mirroring the SLICE-G semantics)."""
    root, db_path, ch = _make_project(
        tmp_path, "inv", {"dead.c": DEAD_C, "keep.ld": KEEP_LD},
        store_linker_script=True,
    )
    conn = open_db(db_path)
    try:
        from fw_context_mcp.indexer.db import find_dead_code

        patterns = ["*(.keepme)", "*(.isr_vector)"]
        rows = find_dead_code(conn, ch, limit=200, project_only=False,
                              keep_patterns=patterns)
        allowed = {"dead", "possibly_dead", "graph_unreachable"}
        for r in rows:
            assert r["status"] in allowed, (
                f"status {r['status']!r} outside the invariant set: {r}")
            assert r.get("evidence_level") in ("L1", "L2"), (
                f"row without evidence_level: {r}")
        names = {r["name"] for r in rows}
        # zero-ref, root-less stay dead; rooted/entry/alias/ctor/keep absent
        assert {"ghost_target", "ghost_caller", "isr_bare"} <= names
        assert not ({"_start", "global_ctor", "kept_by_script",
                     "alias_target"} & names), (
            f"rooted functions leaked into the dead list: {sorted(names)}")
        # deterministic order: status groups then name
        keys = [(r["status"], r["name"]) for r in rows]
        assert keys == sorted(keys), "find_dead_code rows are not deterministically ordered"

        # MCP layer: absolute paths + include_possible param accepted.
        from fw_context_mcp.mcp.handlers import callgraph as cg

        out = cg.find_dead_code(project_root=str(root), limit=200,
                                project_only=False)
        assert isinstance(out, list) and out
        infos = [r for r in out if "info" in r]
        rows_out = [r for r in out if "info" not in r]
        assert rows_out, f"handler returned no rows: {out}"
        for r in rows_out:
            fp = r.get("file") or r.get("file_path") or ""
            assert Path(fp).is_absolute(), (
                f"MCP layer must return absolute paths (invariant): {r}")
        # opt-in param exists and keeps every invariant: include_possible may
        # only REMOVE rows (reclassify as reachable), never fabricate new
        # reported ones, and every surviving row stays well-formed.
        out2 = cg.find_dead_code(project_root=str(root), limit=200,
                                 project_only=False, include_possible=True)
        assert isinstance(out2, list)
        rows2 = [r for r in out2 if "info" not in r]
        for r in rows2:
            assert r["status"] in {"dead", "possibly_dead", "graph_unreachable"}, (
                f"include_possible row outside the invariant set: {r}")
            fp2 = r.get("file") or r.get("file_path") or ""
            assert Path(fp2).is_absolute(), (
                f"include_possible row must keep absolute paths: {r}")
        assert len(rows2) <= len(rows_out), (
            "include_possible may only reclassify rows away, never add: "
            f"exact-only={len(rows_out)} possible={len(rows2)}")
    finally:
        conn.close()


# ── F-I-2: zero-ref POSSIBLE-only candidate reclassification ────────────────

def test_t07_include_possible_reclassifies_zero_ref_candidate(tmp_path):
    """A zero-ref POSSIBLE-only candidate must reclassify with
    include_possible=True and stay 'dead' under the default exact-only run.

    Mechanism (verifier F-I-2): name-fallback refs are suppressed by
    ADR-004 design — slice G routes them to call_sites — so every
    POSSIBLE-only candidate has ZERO refs rows.  The reachable verdict
    must therefore be consulted BEFORE the refs anti-join gate, or the
    opt-in can never reclassify exactly the candidates it exists for.
    """
    root, db_path, ch = _make_project(tmp_path, "possible", {"dead.c": DEAD_C})
    conn = open_db(db_path)
    try:
        caller = conn.execute(
            "SELECT usr FROM symbols WHERE config_hash=? AND name='global_ctor' "
            "AND is_definition=1", (ch,),
        ).fetchone()
        cand = conn.execute(
            "SELECT usr FROM symbols WHERE config_hash=? AND name='ghost_caller' "
            "AND is_definition=1", (ch,),
        ).fetchone()
        assert caller is not None and cand is not None, (
            "fixture symbols missing — cannot build the POSSIBLE edge")
        caller_usr, cand_usr = caller["usr"], cand["usr"]
        # Precondition: the candidate has ZERO refs rows (nothing references
        # it in the fixture) and the caller is a ctor_root.
        n_refs = conn.execute(
            "SELECT COUNT(*) FROM refs WHERE config_hash=? AND to_usr=?",
            (ch, cand_usr),
        ).fetchone()[0]
        assert n_refs == 0, (
            f"test premise broken: candidate already has {n_refs} refs rows")
        # Default: exact-only edges — the zero-ref candidate is 'dead'.
        dead_default = _dead_by_name(conn, ch)
        assert dead_default.get("ghost_caller", {}).get("status") == "dead", (
            f"zero-ref candidate must be dead by default: "
            f"{dead_default.get('ghost_caller')}")
        # The POSSIBLE-only observation: a name-fallback call site from the
        # rooted caller to the candidate (exactly what slice G stores —
        # ADR-004 keeps this OUT of refs).
        from fw_context_mcp.indexer.db import insert_call_sites_batch

        insert_call_sites_batch(conn, [(
            ch, "dead.c", 9, 5, caller_usr, "possible", "name_fallback",
            None, json.dumps([cand_usr]), "token_fallback",
        )])
        conn.commit()
        # The insert must not change the default verdict: POSSIBLE rows are
        # invisible to the exact-only edge set (ADR-004/ADR-005 opt-in).
        dead_after = _dead_by_name(conn, ch)
        assert dead_after.get("ghost_caller", {}).get("status") == "dead", (
            f"default run must ignore POSSIBLE call sites: "
            f"{dead_after.get('ghost_caller')}")
        # Opt-in: the zero-ref POSSIBLE-only candidate is reachable through
        # the ctor caller and MUST NOT be reported anymore (F-I-2 repair).
        dead_optin = _dead_by_name(conn, ch, include_possible=True)
        assert "ghost_caller" not in dead_optin, (
            f"include_possible must reclassify the zero-ref POSSIBLE-only "
            f"candidate as reachable (F-I-2): {dead_optin.get('ghost_caller')}")
        # Control: the dead SCC keeps its graph_unreachable verdict — the
        # opt-in reclassifies only what a POSSIBLE edge actually reaches.
        assert dead_optin.get("dead_a", {}).get("status") == "graph_unreachable", (
            f"dead SCC must survive the opt-in run untouched: "
            f"{dead_optin.get('dead_a')}")
    finally:
        conn.close()


# ── Extractor unit checks (attr scan ground truth, no DB) ───────────────────

def test_t07_attr_extractor_unit(tmp_path):
    """The attribute token scan must lift section/attr flags and the alias
    target name from the frozen fixture shapes (constructor prefix attr,
    section+used prefix attr, weak+alias trailing attr on a declaration)."""
    d = tmp_path / "unit"
    d.mkdir()
    (d / "unit.c").write_text(DEAD_C, encoding="utf-8")
    unit = CompilationUnit(file=d / "unit.c", directory=d, language="c",
                           clang_args=["-std=c11"])
    res = extract_all(unit, with_refs=True)
    syms = {s.name: s for s in res.symbols}

    ctor = syms["global_ctor"]
    assert "constructor" in (ctor.attr_flags or ""), (
        f"constructor attr not lifted: {ctor.attr_flags!r}")
    kept = syms["kept_by_script"]
    assert kept.section == ".keepme", f"section not lifted: {kept.section!r}"
    assert "used" in (kept.attr_flags or ""), f"used flag not lifted: {kept.attr_flags!r}"
    ref_kinds = sorted({r.ref_kind for r in res.references})
    assert "ctor_root" in ref_kinds, f"ctor_root ref missing: {ref_kinds}"
    assert "alias" in ref_kinds, f"alias ref missing: {ref_kinds}"
    alias_edges = [r for r in res.references if r.ref_kind == "alias"]
    alias_usrs = {s.usr: s.name for s in res.symbols}
    assert alias_edges, "no alias edge extracted"
    assert alias_usrs.get(alias_edges[0].to_usr) == "alias_target", (
        f"alias edge must target alias_target: "
        f"{[(alias_usrs.get(e.to_usr), e.from_usr is not None) for e in alias_edges]}")
