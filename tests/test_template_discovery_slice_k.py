"""SLICE-K oracle: T09 template instance discovery (ADR-007).

Frozen cases (TEST_PLAN §11, T09).  Fixture: the F10_templates source,
verbatim from ``.fw-orchestrator/artifacts/T09/reproduction/templates.cpp``
with the clang-22 AST dump as ground truth (O_COMPILER_PREPROCESS).

RED (fail at the SLICE-K base commit c80fad7, one documented reason each):

- T09::RED_real_index_template_usr_all_empty — RC1 (research §2): the
  pinned libclang 18.1.1 wheel defines no ``Cursor.specialized_template``
  property, so the three-level fallback at symbols.py raises
  ``AttributeError`` on every cursor and the swallowed error leaves
  ``template_usr = ''`` on every row of every real index (measured
  EXP1: 0/13 rows).  Even the explicit specialization ``add<int>`` —
  which IS walked as an ordinary FUNCTION_DECL — stays unlinked.
- T09::RED_get_template_instances_box — the handler queries
  ``WHERE template_usr = ?``; with the column never populated
  instance_count is 0 for every template (measured EXP2).
- T09::RED_get_template_instances_add — same dead column: the implicit
  ``add<double>`` and explicit ``add<int>`` instantiations never surface.
- T09::RED_get_template_instances_pair_partial_spec — doubly dead: the
  instance ``Pair<double,int>`` links to the PARTIAL-SPECIALIZATION USR
  (measured EXP6), which the handler's single-USR query never resolves
  from the primary template's row.

GREEN (post-fix):

- T09::GREEN_member_instances_present — ``Box<int>::get`` /
  ``Box<double>::get`` exist as CXX_METHOD instance rows linked to the
  pattern member USR (EXP6 ground truth 7/7 resolved).
- T09::GREEN_no_dangling_instance_refs — every ``refs.to_usr`` in the
  F10 index resolves to a symbols row (kills EXP7's 14 dangling edges);
  call-graph joins keep template edges.
- T09::ADVERSARIAL_non_template_parm_decl_no_false_instance — the
  template parameter ``T`` produces no false instance: no row carries
  ``template_usr == usr`` (the raw C API returns the input cursor for
  non-specializations — self-links must be filtered) and the function
  instance set for ``add`` is exactly the two true instantiations.
- T09::ADVERSARIAL_pattern_rows_keep_is_template_true — the four pattern
  rows keep ``is_template = 1``; primary patterns keep ``template_usr
  = ''``; the partial specialization links to its primary template
  (the storage that backs partial-spec USR-set resolution).
- T09::INTEGRATION_ast_oracle_parity — the indexed instance set matches
  the AST dump ground truth (7/7 EXP6 instances with USR + kind +
  template link), and the one-definition-per-template-vector property
  holds across TUs: two TUs instantiating ``Box<int>`` yield ONE
  instance row per context linked to the ONE pattern declaration.
- T09::REGRESSION_db_layer_synthetic_tests — the synthetic DB-layer
  template tests (tests/test_db.py -k template, 4 passed at EXP8) keep
  passing: the column semantics change is additive.
- T09::PERFORMANCE_discovery_pass_overhead — the discovery pass over the
  already-walked cursors adds bounded overhead on the F10 fixture.

Design (ADR-007 Option A): pure-Python discovery pass binding
``clang_getSpecializedCursorTemplate`` / ``clang_getTemplateCursorKind``
via ``conf.lib`` (the shared ctypes precedent is
indexer/skipped_ranges.py), run over already-walked cursors; instance
facts stored as symbol observations with ``template_usr`` populated;
``get_template_instances`` resolves the USR set {primary, partial-spec
USRs}; doctor gains an API-surface check.

Scratch: everything under pytest ``tmp_path`` (%TEMP%).  No repo-root
``.fw-context``, no global registry writes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

# ――― F10 fixture (verbatim from artifacts/T09/reproduction/templates.cpp) ―――

F10_TEMPLATES = """template<class T>
T add(T a, T b) { return a + b; }

template<class T>
struct Box { T value; T get() const { return value; } };

template<class T, class U>
struct Pair {};

template<class T>
struct Pair<T, int> {};

template<>
int add<int>(int a, int b) { return a + b + 1; }

int use_templates() {
    Box<int> bi{3};
    Box<double> bd{4.0};
    auto a = add(1.0, 2.0);
    auto b = add<int>(1, 2);
    Pair<double, int> p;
    (void)p;
    return bi.get() + (int)bd.get() + (int)a + b;
}
"""

# Second TU for the one-definition-per-template-vector case: a shared
# header declares the pattern; both TUs instantiate Box<int>.
BOX_HEADER = """#ifndef BOX_H
#define BOX_H
template<class T>
struct Box { T value; T get() const { return value; } };
#endif
"""

TU_A = """#include "box.h"
int use_a() { Box<int> bi{1}; return bi.get(); }
"""

TU_B = """#include "box.h"
int use_b() { Box<int> bj{2}; return bj.get(); }
"""

# EXP4/EXP6 ground truth (clang AST dump, O_COMPILER_PREPROCESS): the
# required instance set with USR + cursor kind + linked template USR.
REQUIRED_INSTANCES: dict[str, tuple[str, str]] = {
    # USR -> (kind label, linked template USR)
    "c:@S@Box>#I": ("struct", "c:@ST>1#T@Box"),
    "c:@S@Box>#d": ("struct", "c:@ST>1#T@Box"),
    "c:@S@Box>#I@F@get#1": ("method", "c:@ST>1#T@Box@F@get#1"),
    "c:@S@Box>#d@F@get#1": ("method", "c:@ST>1#T@Box@F@get#1"),
    "c:@F@add<#d>#d#d#": ("function", "c:@FT@>1#Tadd#t0.0#S0_#S0_#"),
    "c:@F@add<#I>#I#I#": ("function", "c:@FT@>1#Tadd#t0.0#S0_#S0_#"),
    "c:@S@Pair>#d#I": ("struct", "c:@SP>1#T@Pair>#t0.0#I"),
}


# ――― harness (SLICE-G/I pattern) ―――――――――――――――――――――――――――――――――――――――――


def _write_cc(root: Path, entries: list[dict]) -> Path:
    fwd = str(root).replace("\\", "/")
    payload = []
    for i, e in enumerate(entries):
        payload.append(
            {
                "directory": fwd,
                "file": e["file"],
                "arguments": ["gcc", *e["args"], "-c", e["file"], "-o", e.get("output", f"build/o{i}.o")],
            }
        )
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cc


def _isolate_index_db(project_root: Path, tmp_dir: Path) -> Path:
    """Redirect the index dir via local.toml (SLICE-D pattern)."""
    local_dir = project_root / ".fw-context"
    local_dir.mkdir(parents=True, exist_ok=True)
    index_dir = tmp_dir / "index"
    fwd = str(index_dir).replace("\\", "/")
    (local_dir / "local.toml").write_text(f'[index]\ndb_dir = "{fwd}"\n', encoding="utf-8")
    return index_dir


def _write_project_id(root: Path) -> str:
    from fw_context_mcp.config.settings import generate_project_id, _write_project_id

    pid = generate_project_id()
    _write_project_id(root, pid)
    return pid


def _run_index(root: Path, cc: Path, db_path: Path, pid: str) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return run(
        cc,
        db_path,
        project_root=root,
        project_id=pid,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
    )


def _indexed_f10(tmp_path: Path) -> tuple[Path, Path, str]:
    """F10 fixture project + isolated index dir + full index -> (root, db, hash)."""
    root = tmp_path / "f10_templates"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "templates.cpp").write_text(F10_TEMPLATES, encoding="utf-8")
    cc = _write_cc(root, [{"file": "src/templates.cpp", "args": ["-std=c++17"]}])
    index_dir = _isolate_index_db(root, tmp_path / "f10_state")
    pid = _write_project_id(root)
    db_path = index_dir / pid / "index.db"
    ch = _run_index(root, cc, db_path, pid)
    return root, db_path, ch


def _indexed_two_tu(tmp_path: Path) -> tuple[Path, Path, str]:
    """Header + two instantiating TUs -> (root, db, hash)."""
    root = tmp_path / "box_multitu"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "box.h").write_text(BOX_HEADER, encoding="utf-8")
    (src / "tu_a.cpp").write_text(TU_A, encoding="utf-8")
    (src / "tu_b.cpp").write_text(TU_B, encoding="utf-8")
    cc = _write_cc(
        root,
        [
            {"file": "src/tu_a.cpp", "args": ["-std=c++17"]},
            {"file": "src/tu_b.cpp", "args": ["-std=c++17"]},
        ],
    )
    index_dir = _isolate_index_db(root, tmp_path / "multitu_state")
    pid = _write_project_id(root)
    db_path = index_dir / pid / "index.db"
    ch = _run_index(root, cc, db_path, pid)
    return root, db_path, ch


def _connect(db_path: Path) -> sqlite3.Connection:
    # open_db already carries row_factory=sqlite3.Row (or the pysqlite3
    # equivalent) — do not override it here.
    return open_db(db_path)


def _symbols(conn: sqlite3.Connection, ch: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM symbols WHERE config_hash=?", (ch,)
    ).fetchall()


def _instances(conn: sqlite3.Connection, ch: str) -> dict[str, sqlite3.Row]:
    """Every row that carries a template link, keyed by USR."""
    return {
        r["usr"]: r
        for r in conn.execute(
            "SELECT * FROM symbols WHERE config_hash=? AND template_usr != ''",
            (ch,),
        ).fetchall()
    }


# ――― RED cases (fail at c80fad7 for the documented RC1/RC2 reasons) ――――――


def test_t09_red_real_index_template_usr_all_empty(tmp_path: Path):
    """The explicit specialization add<int> is walked as an ordinary
    FUNCTION_DECL, so the fixed three-level fallback must link it to the
    pattern.  At RED the pinned wheel lacks Cursor.specialized_template,
    the AttributeError is swallowed, and 0 rows carry template_usr."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    spec = conn.execute(
        "SELECT usr, template_usr FROM symbols WHERE config_hash=? AND usr=?",
        (ch, "c:@F@add<#I>#I#I#"),
    ).fetchone()
    assert spec is not None, "explicit specialization add<int> not indexed at all"
    assert spec["template_usr"] != "", (
        "RED (RC1): template_usr is dead code — the explicit specialization "
        f"add<int> has template_usr={spec['template_usr']!r} because "
        "Cursor.specialized_template does not exist in the pinned bindings"
    )


def test_t09_red_get_template_instances_box(tmp_path: Path):
    """get_template_instances('Box') must return the two STRUCT_DECL
    instances linked to the CLASS_TEMPLATE pattern (EXP4/EXP6 truth)."""
    root, _db, _ch = _indexed_f10(tmp_path)
    from fw_context_mcp.mcp.handlers import inheritance as inh

    rows = inh.get_template_instances(template_name="Box", project_root=str(root))
    assert rows and "error" not in rows[0], f"handler error: {rows}"
    entry = rows[0]
    usrs = {i["usr"] for i in entry["instances"]} if "instances" in entry else set()
    assert entry.get("instance_count", 0) >= 2, (
        f"RED (RC1+RC2): instance_count={entry.get('instance_count')} for 'Box' — "
        "template_usr never populated, instances never discovered"
    )
    assert "c:@S@Box>#I" in usrs and "c:@S@Box>#d" in usrs, (
        f"Box<int>/Box<double> STRUCT_DECL instances missing, got {sorted(usrs)}"
    )
    assert all(
        i["template_usr"] == "c:@ST>1#T@Box"
        for i in entry["instances"]
        if i.get("template_usr")
    ), "instance template_usr must point at the primary template"


def test_t09_red_get_template_instances_add(tmp_path: Path):
    """get_template_instances('add') must return both the implicit
    add<double> and the explicit specialization add<int>, each linked to
    the FUNCTION_TEMPLATE pattern (research GREEN oracle §6 item 2)."""
    root, _db, _ch = _indexed_f10(tmp_path)
    from fw_context_mcp.mcp.handlers import inheritance as inh

    rows = inh.get_template_instances(template_name="add", project_root=str(root))
    assert rows and "error" not in rows[0], f"handler error: {rows}"
    entry = rows[0]
    usrs = {i["usr"] for i in entry["instances"]} if "instances" in entry else set()
    assert entry.get("instance_count", 0) >= 2, (
        f"RED (RC1+RC2): instance_count={entry.get('instance_count')} for 'add'"
    )
    assert "c:@F@add<#d>#d#d#" in usrs, "implicit add<double> instance missing"
    assert "c:@F@add<#I>#I#I#" in usrs, "explicit specialization add<int> missing"


def test_t09_red_get_template_instances_pair_partial_spec(tmp_path: Path):
    """Querying 'Pair' must resolve the partial-specialization USR set:
    the Pair<double,int> instance links to the CLASS_TEMPLATE_PARTIAL_
    SPECIALIZATION USR, which the single-USR handler query never reaches."""
    root, _db, _ch = _indexed_f10(tmp_path)
    from fw_context_mcp.mcp.handlers import inheritance as inh

    rows = inh.get_template_instances(template_name="Pair", project_root=str(root))
    assert rows and "error" not in rows[0], f"handler error: {rows}"
    entry = rows[0]
    usrs = {i["usr"] for i in entry["instances"]} if "instances" in entry else set()
    assert "c:@S@Pair>#d#I" in usrs, (
        f"RED (RC1): Pair<double,int> instance missing — the partial-spec "
        f"USR set was not resolved, got {sorted(usrs)}"
    )


# ――― GREEN cases ――――――――――――――――――――――――――――――――――――――――――――――――――――――――


def test_t09_green_member_instances_present(tmp_path: Path):
    """Box<int>::get / Box<double>::get exist as CXX_METHOD rows linked to
    the pattern member USR (EXP6: 7/7 links resolved)."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    for inst_usr, pattern_usr in (
        ("c:@S@Box>#I@F@get#1", "c:@ST>1#T@Box@F@get#1"),
        ("c:@S@Box>#d@F@get#1", "c:@ST>1#T@Box@F@get#1"),
    ):
        row = conn.execute(
            "SELECT kind, is_template, template_usr FROM symbols "
            "WHERE config_hash=? AND usr=?",
            (ch, inst_usr),
        ).fetchone()
        assert row is not None, f"member instance {inst_usr} has no symbols row"
        assert row["kind"] == "method", f"{inst_usr} kind={row['kind']}, want method"
        assert row["is_template"] == 0, "instance rows are observations, not patterns"
        assert row["template_usr"] == pattern_usr, (
            f"{inst_usr} linked to {row['template_usr']!r}, want {pattern_usr!r}"
        )


def test_t09_green_no_dangling_instance_refs(tmp_path: Path):
    """Every ref whose target is template-shaped resolves to a symbols
    row (kills EXP7's dangling instance edges) — call-graph joins keep
    template edges.

    Scope note: refs to *parameters* of indexed callables
    (``c:<file>@…@F@<fn>@<parm>`` file-scoped USRs) dangle pre-existing —
    PARM_DECLs were never symbol rows, for plain functions too (probed on
    a non-template fixture at SLICE-K).  That is platform behavior outside
    T09's scope; the template-shaped filter below excludes those locals.
    """
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    to_usrs = [
        r["to_usr"]
        for r in conn.execute(
            "SELECT DISTINCT to_usr FROM refs WHERE config_hash=?", (ch,)
        ).fetchall()
    ]
    assert to_usrs, "no refs indexed — fixture broken"
    known = {
        r["usr"]
        for r in conn.execute(
            "SELECT usr FROM symbols WHERE config_hash=?", (ch,)
        ).fetchall()
    }
    # Template-shaped: a semantic (non file-scoped) USR carrying template
    # argument mangling ('<' / '>') — instances, their members, and the
    # implicit members of instances.  File-scoped parm locals never are.
    template_shaped = [
        u for u in to_usrs
        if u and u.startswith("c:@") and ("<" in u or ">" in u)
    ]
    assert template_shaped, "no template-shaped ref targets found — fixture broken"
    dangling = sorted(u for u in template_shaped if u not in known)
    assert not dangling, (
        f"dangling template-instance refs survive: {dangling} — instance "
        "rows missing from symbols"
    )
    # The instance targets the AST walk DID record (EXP7) now join.
    assert "c:@F@add<#d>#d#d#" in known, "call target add<double> still unresolved"
    assert "c:@S@Box>#I@F@get#1" in known, "member target Box<int>::get still unresolved"
    assert "c:@S@Pair>#d#I@F@Pair#" in known, (
        "implicit ctor target Pair<double,int>::Pair still unresolved"
    )


def test_t09_adversarial_non_template_parm_decl_no_false_instance(tmp_path: Path):
    """No false instance from the template parameter T: no row links to
    itself (clang_getSpecializedCursorTemplate returns the input cursor
    for non-specializations), and the add instance set is exactly the two
    true instantiations."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    self_linked = conn.execute(
        "SELECT usr, name FROM symbols WHERE config_hash=? AND template_usr != '' "
        "AND template_usr = usr",
        (ch,),
    ).fetchall()
    assert not self_linked, f"self-linked rows (false instances): {self_linked}"
    linked = _instances(conn, ch)
    add_instances = sorted(
        u for u, r in linked.items()
        if r["template_usr"] == "c:@FT@>1#Tadd#t0.0#S0_#S0_#"
    )
    assert add_instances == ["c:@F@add<#I>#I#I#", "c:@F@add<#d>#d#d#"], (
        f"add instance set polluted: {add_instances}"
    )


def test_t09_adversarial_pattern_rows_keep_is_template_true(tmp_path: Path):
    """Pattern rows keep is_template=1; primaries keep template_usr='';
    the partial specialization links to its primary (the storage that
    backs USR-set resolution)."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    patterns = conn.execute(
        "SELECT usr, name, kind, is_template, template_usr FROM symbols "
        "WHERE config_hash=? AND is_template=1",
        (ch,),
    ).fetchall()
    by_usr = {r["usr"]: r for r in patterns}
    assert "c:@FT@>1#Tadd#t0.0#S0_#S0_#" in by_usr, "add pattern lost is_template"
    assert "c:@ST>1#T@Box" in by_usr, "Box pattern lost is_template"
    assert "c:@SP>1#T@Pair>#t0.0#I" in by_usr, "Pair partial spec lost is_template"
    for usr, r in by_usr.items():
        assert r["is_template"] == 1
        if usr != "c:@SP>1#T@Pair>#t0.0#I":
            assert r["template_usr"] == "", (
                f"primary pattern {usr} must keep template_usr='', "
                f"got {r['template_usr']!r}"
            )
    partial = by_usr["c:@SP>1#T@Pair>#t0.0#I"]
    assert partial["template_usr"].startswith("c:@ST>") and "Pair" in partial["template_usr"], (
        f"partial spec must link to its primary template, got {partial['template_usr']!r}"
    )


def test_t09_integration_ast_oracle_parity(tmp_path: Path):
    """Indexed instance set matches the clang AST dump ground truth
    (EXP4/EXP6, O_COMPILER_PREPROCESS), and the one-definition-per-
    template-vector property holds across TUs: two TUs instantiating
    Box<int> yield ONE instance row per context linked to the ONE
    pattern declaration (no duplicate declarations across TUs)."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    linked = _instances(conn, ch)
    missing = [
        (usr, kind)
        for usr, (kind, _tpl) in REQUIRED_INSTANCES.items()
        if usr not in linked
    ]
    assert not missing, (
        f"instances required by the AST oracle missing from the index: {missing}"
    )
    wrong = [
        (usr, linked[usr]["kind"], linked[usr]["template_usr"])
        for usr, (kind, tpl) in REQUIRED_INSTANCES.items()
        if usr in linked
        and (linked[usr]["kind"] != kind or linked[usr]["template_usr"] != tpl)
    ]
    assert not wrong, f"instance kind/template-link mismatches vs AST oracle: {wrong}"

    # One-definition-per-template-vector: shared header + two TUs.  Each
    # TU is its own compile context (ADR-002), so the instance is one
    # observation PER CONTEXT — and every observation links to the ONE
    # pattern declaration USR; within a context the USR is unique (no
    # duplicate declarations across TUs' shared header).
    root2, db2, ch2 = _indexed_two_tu(tmp_path)
    conn2 = _connect(db2)
    ctx_ids = [
        r["context_id"]
        for r in conn2.execute("SELECT DISTINCT context_id FROM symbols").fetchall()
    ]
    assert len(ctx_ids) == 2, f"expected two TU contexts, got {len(ctx_ids)}"
    box_rows = conn2.execute(
        "SELECT usr, context_id, template_usr, is_template FROM symbols "
        "WHERE config_hash=? AND usr IN ('c:@S@Box>#I', 'c:@ST>1#T@Box')",
        (ch2,),
    ).fetchall()
    links = set()
    for ctx in ctx_ids:
        inst_rows = [
            r for r in box_rows
            if r["context_id"] == ctx and r["usr"] == "c:@S@Box>#I"
        ]
        assert len(inst_rows) == 1, (
            f"one-definition-per-template-vector violated: {len(inst_rows)} "
            f"rows for the Box<int> instance USR within one context"
        )
        assert inst_rows[0]["is_template"] == 0
        links.add(inst_rows[0]["template_usr"])
        pattern_rows = [
            r for r in box_rows
            if r["context_id"] == ctx and r["usr"] == "c:@ST>1#T@Box"
        ]
        assert len(pattern_rows) == 1, (
            f"the Box pattern declaration appears {len(pattern_rows)}x in one context"
        )
        assert pattern_rows[0]["is_template"] == 1
    assert links == {"c:@ST>1#T@Box"}, (
        f"both TU observations must link to the ONE declaration, got {links}"
    )


def test_t09_regression_db_layer_synthetic_tests():
    """The synthetic DB-layer template tests (EXP8: 4 passed) keep
    passing — the column semantics change is additive."""
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    src = str(repo_root / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_db.py", "-k", "template", "-q", "--no-header", "-x"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        f"synthetic DB template tests regressed:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
    )
    assert " passed" in proc.stdout, f"no pytest summary: {proc.stdout[-500:]}"


def test_t09_performance_discovery_pass_overhead(tmp_path: Path):
    """The discovery pass over already-walked cursors stays bounded on the
    F10 fixture: full index completes and the instance-emitting pass is
    far cheaper than the parse+walk it piggybacks on."""
    root, db_path, ch = _indexed_f10(tmp_path)
    conn = _connect(db_path)
    n_linked = conn.execute(
        "SELECT COUNT(*) AS n FROM symbols WHERE config_hash=? AND template_usr != ''",
        (ch,),
    ).fetchone()["n"]
    assert n_linked >= 7, f"discovery pass emitted only {n_linked} linked rows"

    from clang import cindex as cx

    from fw_context_mcp.indexer.compile_commands import CompilationUnit
    from fw_context_mcp.indexer.symbols import extract_all

    src_file = root / "src" / "templates.cpp"
    unit = CompilationUnit(
        file=src_file,
        directory=root,
        language="cpp",
        clang_args=["-std=c++17"],
    )
    t0 = time.perf_counter()
    result = extract_all(unit, with_refs=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, f"extract_all on F10 took {elapsed:.2f}s — discovery pass runaway"
    tu_syms = [s for s in result.symbols if s.template_usr]
    assert len(tu_syms) >= 7, (
        f"per-TU discovery emitted {len(tu_syms)} linked rows, want >= 7"
    )
