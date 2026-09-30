"""T14 SLICE-G oracle: call-site storage + uncertainty (T05, ADR-004).

Frozen cases (TEST_PLAN §7, T05::test_spec.yaml).  Fixtures verbatim from
``.fw-orchestrator/artifacts/T05/reproduction/`` (f05_cpp_calls,
f05_defined, dependent_v1, errrecover_v3, errrecover_v4).

RED (fail at the SLICE-G base commit 2b87528, one documented reason each):

- T05::T05_F05_qualified_resolution_graph_tools — RC2:
  ``_resolve_target_usr`` (db/_callgraph.py:90-130) matches a qualified
  name through a bare-name ``s.name = plain_name`` OR with no
  qualified-precedence in ORDER BY, so ``Derived::run`` / ``Other::run``
  resolve to whichever same-name symbol has equal ref counts first
  (Base::run, inserted first).  find_call_path then reports
  ``target_usr=c:@S@Base@F@run#`` for a query that named Derived/Other.
- T05::T05_virtual_dispatch_possible_edges — the ``call_sites`` table is
  empty (nothing populates it at parse time) and the graph tools have no
  ``include_possible`` / ``include_unresolved`` parameters (TypeError =
  the RED proof of the missing surface).
- T05::T05_template_body_call_site_persisted — the uninstantiated
  FUNCTION_TEMPLATE body produces no span on hosts where
  ``is_definition()`` is False, so the raw-text fallback never sees
  ``t.execute(1)``; no call_sites row exists for generic@3 (T05 F6,
  ADR-007 cursor.extent note).
- T05::T05_unresolved_site_drop_vs_fabricate — F7/A05: the undeclared
  member call ``f->work()`` is dropped entirely in v3 (no call_sites
  row), and in v4 the token fallback fabricates an exact-looking
  ref_kind='call' edge to the unrelated ``Impl::work`` in refs (refs must
  stay AST-proven per ADR-004 layer 1).
- T05::T05_bare_name_ambiguity_surfaced — find_callers('run') with three
  same-name methods silently returns one deterministic pick with no
  candidates record (T05 F8).

PIN (GREEN at the base commit — fixed by SLICE-A, kept as regression):

- T05::T05_F05_exact_overload_single_edge — the per-site token-fallback
  gate already suppresses the fabricated sibling-overload edge; refs from
  exact_overload@15 target exactly send(int).

GREEN-phase behavior (ADR-004 layer 2): every call expression persists a
``call_sites`` row per context with
``certainty ∈ {exact, possible, unresolved}``, a ``resolution_kind``, the
``candidate_usrs`` JSON list, and ``origin ∈ {ast, token_fallback,
raw_text_fallback}``; refs keep only AST-proven edges; graph tools default
to exact edges plus an ``unresolved_count`` and merge POSSIBLE sites only
on ``include_possible=True`` (dispatch expansion synthesized from the
overrides table at query time, resolution_kind='dispatch_synthetic').

Scratch: everything under pytest ``tmp_path`` (%TEMP%).  No repo-root
``.fw-context``, no global registry writes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

# ── T05 fixtures (verbatim from artifacts/T05/reproduction/) ──────────────

F05_CPP_CALLS = (
    "struct Base {\n"                  # 1
    "    virtual void run();\n"        # 2
    "    void send(int);\n"            # 3
    "    void send(double);\n"         # 4
    "};\n"                             # 5
    "\n"                               # 6
    "struct Derived final : Base {\n"  # 7
    "    void run() override;\n"       # 8
    "};\n"                             # 9
    "\n"                               # 10
    "struct Other {\n"                 # 11
    "    void run();\n"                # 12
    "};\n"                             # 13
    "\n"                               # 14
    "void exact_overload(Base& b) { b.send(1); }\n"   # 15
    "void virtual_call(Base* b) { b->run(); }\n"      # 16
    "void final_virtual(Derived* d) { d->run(); }\n"  # 17
    "void unrelated(Other* o) { o->run(); }\n"        # 18
)

F05_DEFINED = (
    "struct Base {\n"
    "    virtual void run() {}\n"      # 2
    "    void send(int) {}\n"
    "    void send(double) {}\n"
    "};\n"
    "struct Derived final : Base {\n"
    "    void run() override {}\n"     # 7
    "};\n"
    "void virtual_call(Base* b) { b->run(); }\n"      # 9
    "void final_virtual(Derived* d) { d->run(); }\n"  # 10
)

DEPENDENT_V1 = (
    "struct Alpha { void execute(int); };\n"  # 1
    "\n"                                       # 2
    "template<class T> void generic(T& t) { t.execute(1); }\n"  # 3
    "\n"                                       # 4
    "int main() { return 0; }\n"               # 5
)

ERRRECOVER_V3 = (
    "struct Fwd;\n"                            # 1
    "void drive(Fwd* f) { f->work(); }\n"      # 2
    "int main() { return 0; }\n"               # 3
)

ERRRECOVER_V4 = (
    "struct Fwd;\n"                            # 1
    "struct Impl { void work(); };\n"          # 2
    "void drive(Fwd* f) { f->work(); }\n"      # 3
    "int main() { return 0; }\n"               # 4
)


# ── harness (SLICE-F pattern) ──────────────────────────────────────────────


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


def _one_tu(root: Path, name: str, source: str) -> list[dict]:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text(source, encoding="utf-8")
    return [{"file": f"src/{name}", "args": ["-std=c++14"], "output": f"build/{name}.o"}]


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


def _isolate_index_db(project_root: Path, tmp_dir: Path) -> Path:
    """Redirect the index dir via local.toml (SLICE-D pattern)."""
    local_dir = project_root / ".fw-context"
    local_dir.mkdir(parents=True, exist_ok=True)
    index_dir = tmp_dir / "index"
    fwd = str(index_dir).replace("\\", "/")
    (local_dir / "local.toml").write_text(f'[index]\ndb_dir = "{fwd}"\n', encoding="utf-8")
    return index_dir


def _indexed_project(tmp_path: Path, name: str, tu_name: str, source: str) -> tuple[Path, Path, str]:
    """Fixture project + isolated index dir + full index → (root, db, hash)."""
    root = tmp_path / name
    root.mkdir()
    cc = _write_cc(root, _one_tu(root, tu_name, source))
    index_dir = _isolate_index_db(root, tmp_path / f"{name}_state")

    from fw_context_mcp.config.settings import generate_project_id, _write_project_id

    pid = generate_project_id()
    for attempt in range(5):
        try:
            _write_project_id(root, pid)
            break
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    else:
        _write_project_id(root, pid)
    db_path = index_dir / pid / "index.db"
    ch = _run_index(root, cc, db_path, pid)
    return root, db_path, ch


def _usr_of(conn: sqlite3.Connection, ch: str, qualified_name: str) -> str:
    row = conn.execute(
        "SELECT usr FROM symbols WHERE config_hash=? AND qualified_name=? LIMIT 1",
        (ch, qualified_name),
    ).fetchone()
    assert row is not None, f"symbol {qualified_name} not indexed"
    return row["usr"]


def _call_refs(conn: sqlite3.Connection, ch: str, from_usr: str) -> set[str]:
    return {
        r["to_usr"]
        for r in conn.execute(
            """SELECT to_usr FROM refs WHERE config_hash=? AND ref_kind='call'
               AND from_usr=?""",
            (ch, from_usr),
        ).fetchall()
    }


def _cs_rows(conn: sqlite3.Connection, ch: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM call_sites WHERE config_hash=?", (ch,)
    ).fetchall()


# ── T05::T05_F05_exact_overload_single_edge (PIN — SLICE-A fixed F1) ───────


def test_t05_f05_exact_overload_single_edge(tmp_path: Path):
    """refs from exact_overload@15 target exactly send(int); never send(double).

    PIN: GREEN at the SLICE-G base commit — SLICE-A's per-site token
    fallback gate removed the fabricated sibling-overload edge.  Kept as
    the frozen regression gate while the call_sites work lands.
    """
    root, db, ch = _indexed_project(tmp_path, "f05_exact", "calls.cpp", F05_CPP_CALLS)
    conn = open_db(db)
    try:
        usr_exact = _usr_of(conn, ch, "exact_overload")
        targets = _call_refs(conn, ch, usr_exact)
        assert targets == {"c:@S@Base@F@send#I#"}, (
            f"exact_overload must call exactly send(int); got {sorted(targets)}"
        )
    finally:
        conn.close()


# ── T05::T05_F05_qualified_resolution_graph_tools (RC2) ────────────────────


def test_t05_f05_qualified_resolution_graph_tools(tmp_path: Path):
    """find_call_path must never answer a Derived/Other query with Base's USR.

    RC2: the bare-name OR in _resolve_target_usr lets ``Derived::run`` and
    ``Other::run`` resolve to ``Base::run`` (equal ref counts, first row
    wins).  GREEN: the target endpoint is Derived's (or Other's) USR —
    either as the path's target_usr or in the explicit no-path resolution
    record — never the base's.
    """
    root, db, ch = _indexed_project(tmp_path, "f05_rc2", "calls.cpp", F05_CPP_CALLS)
    conn = open_db(db)
    try:
        usr_base = _usr_of(conn, ch, "Base::run")
        usr_derived = _usr_of(conn, ch, "Derived::run")
        usr_other = _usr_of(conn, ch, "Other::run")
    finally:
        conn.close()

    from fw_context_mcp.mcp.handlers import callgraph as cg

    for to_name, expected in (("Derived::run", usr_derived), ("Other::run", usr_other)):
        rows = cg.find_call_path(
            from_name="virtual_call", to_name=to_name, project_root=str(root)
        )
        assert isinstance(rows, list) and rows, f"no response for {to_name}"
        endpoints = {
            r["target_usr"] for r in rows
            if isinstance(r, dict) and r.get("target_usr")
        } | {
            r["to_usr"] for r in rows
            if isinstance(r, dict) and r.get("to_usr")
        }
        assert usr_base not in endpoints, (
            f"'{to_name}' resolved to the BASE USR {usr_base} (RC2 bare-name OR); "
            f"response: {rows}"
        )
        assert endpoints == set() or endpoints == {expected}, (
            f"'{to_name}' must resolve to {expected}; got {endpoints}: {rows}"
        )


# ── T05::T05_virtual_dispatch_possible_edges (A05 semantics) ───────────────


def test_t05_virtual_dispatch_possible_edges(tmp_path: Path):
    """Defined fixture: static EXACT edge stored; POSSIBLE dispatch target
    to Derived::run exposed only via include_possible; unresolved_count
    always observable; the default response stays exact-only."""
    root, db, ch = _indexed_project(tmp_path, "f05_def", "calls.cpp", F05_DEFINED)
    conn = open_db(db)
    try:
        usr_base = _usr_of(conn, ch, "Base::run")
        usr_derived = _usr_of(conn, ch, "Derived::run")
        usr_vcall = _usr_of(conn, ch, "virtual_call")

        # (1) static edge: refs keeps the AST-proven call ...
        assert usr_base in _call_refs(conn, ch, usr_vcall), "static edge missing"
        # ... and call_sites carries the exact site row (ADR-004 layer 2).
        exact_rows = [
            r for r in _cs_rows(conn, ch)
            if r["from_usr"] == usr_vcall and r["certainty"] == "exact"
            and r["target_usr"] == usr_base
        ]
        assert exact_rows, (
            "no exact call_sites row for virtual_call→Base::run — the table is "
            "empty at base (nothing populates it at parse time)"
        )
    finally:
        conn.close()

    from fw_context_mcp.mcp.handlers import callgraph as cg

    # (2) default response: exact only — virtual_call must NOT appear.
    default_rows = cg.find_callers(name="Derived::run", project_root=str(root))
    default_callers = {
        r.get("caller") for r in default_rows if isinstance(r, dict) and r.get("caller")
    }
    assert not any("virtual_call" in c for c in default_callers), (
        f"default response leaked a POSSIBLE dispatch caller: {default_rows}"
    )

    # (3) include_possible=True merges the dispatch-synthesized caller.
    #     At base the parameter does not exist → TypeError is the RED proof.
    possible_rows = cg.find_callers(
        name="Derived::run", project_root=str(root), include_possible=True
    )
    possible_hits = [
        r for r in possible_rows
        if isinstance(r, dict)
        and r.get("certainty") == "possible"
        and r.get("resolution_kind") == "dispatch_synthetic"
        and r.get("caller") == "virtual_call"
    ]
    assert possible_hits, (
        f"include_possible must expose virtual_call as a dispatch_synthetic "
        f"POSSIBLE caller of Derived::run; got {possible_rows}"
    )
    assert possible_hits[0].get("candidate_usrs") in (None, [], [usr_derived])

    # (4) unresolved_count observable on the default response.
    assert any(
        isinstance(r, dict) and "unresolved_count" in r for r in default_rows
    ), f"default response never reports unresolved_count: {default_rows}"


# ── T05::T05_template_body_call_site_persisted (F6, ADR-007) ───────────────


def test_t05_template_body_call_site_persisted(tmp_path: Path):
    """dependent_v1: the uninstantiated template body's call site persists
    as an UNRESOLVED call_sites row (no candidate — a dependent name must
    never resolve), attributed to generic and carrying the context_id."""
    root, db, ch = _indexed_project(tmp_path, "dep_v1", "uncertainty.cpp", DEPENDENT_V1)
    conn = open_db(db)
    try:
        usr_generic = _usr_of(conn, ch, "generic")
        ctx_id = conn.execute(
            "SELECT context_id FROM files WHERE config_hash=? LIMIT 1", (ch,)
        ).fetchone()
        rows = [
            r for r in _cs_rows(conn, ch)
            if r["from_usr"] == usr_generic and r["from_line"] == 3
        ]
        assert rows, (
            "no call_sites row for the template-body call site generic@3 — "
            "the FUNCTION_TEMPLATE body produces no span on this host "
            "(is_definition() False) so the raw-text fallback never sees it"
        )
        row = rows[0]
        assert row["certainty"] == "unresolved", (
            f"dependent template-body site must be unresolved, got {row['certainty']}"
        )
        assert json.loads(row["candidate_usrs"] or "[]") == [], (
            "a dependent name must carry NO candidate (never fabricated)"
        )
        assert row["context_id"], "call_sites rows must carry the context_id (F)"
        assert ctx_id is not None and row["context_id"] == ctx_id["context_id"]
    finally:
        conn.close()


# ── T05::T05_unresolved_site_drop_vs_fabricate (F7 / A05) ──────────────────


def test_t05_unresolved_site_drop_vs_fabricate(tmp_path: Path):
    """v3: the undeclared member call persists as UNRESOLVED (not dropped).
    v4: the site is POSSIBLE with the Impl::work candidate — and refs gets
    NO exact-looking call edge to Impl::work (refs stay AST-proven)."""
    root3, db3, ch3 = _indexed_project(
        tmp_path, "err_v3", "uncertainty.cpp", ERRRECOVER_V3
    )
    conn3 = open_db(db3)
    try:
        usr_drive3 = _usr_of(conn3, ch3, "drive")
        rows3 = [
            r for r in _cs_rows(conn3, ch3)
            if r["from_usr"] == usr_drive3 and r["from_line"] == 2
        ]
        assert rows3, (
            "errrecover_v3: the f->work() call site is dropped entirely — no "
            "call_sites row persists the uncertainty"
        )
        assert rows3[0]["certainty"] == "unresolved"
        assert json.loads(rows3[0]["candidate_usrs"] or "[]") == []
    finally:
        conn3.close()

    root4, db4, ch4 = _indexed_project(
        tmp_path, "err_v4", "uncertainty.cpp", ERRRECOVER_V4
    )
    conn4 = open_db(db4)
    try:
        usr_drive4 = _usr_of(conn4, ch4, "drive")
        usr_impl_work = _usr_of(conn4, ch4, "Impl::work")
        rows4 = [
            r for r in _cs_rows(conn4, ch4)
            if r["from_usr"] == usr_drive4 and r["from_line"] == 3
        ]
        assert rows4, "errrecover_v4: no call_sites row for the f->work() site"
        row = rows4[0]
        assert row["certainty"] == "possible", (
            f"exactly one same-name candidate → POSSIBLE, got {row['certainty']}"
        )
        assert json.loads(row["candidate_usrs"] or "[]") == [usr_impl_work], (
            f"candidate list must name Impl::work; got {row['candidate_usrs']}"
        )
        # refs stay AST-proven: the fabricated exact-looking edge must go.
        fabricated = _call_refs(conn4, ch4, usr_drive4)
        assert usr_impl_work not in fabricated, (
            "F7 fabrication: refs carries an exact-looking call edge to the "
            "unrelated Impl::work from the undeclared f->work() site"
        )
    finally:
        conn4.close()


# ── T05::T05_bare_name_ambiguity_surfaced (F8) ─────────────────────────────


def test_t05_bare_name_ambiguity_surfaced(tmp_path: Path):
    """find_callers('run') with three same-name methods must surface the
    ambiguity (a candidates record), never a silent single pick."""
    root, db, ch = _indexed_project(tmp_path, "f05_amb", "calls.cpp", F05_CPP_CALLS)
    conn = open_db(db)
    try:
        expected = {
            _usr_of(conn, ch, "Base::run"),
            _usr_of(conn, ch, "Derived::run"),
            _usr_of(conn, ch, "Other::run"),
        }
    finally:
        conn.close()

    from fw_context_mcp.mcp.handlers import callgraph as cg

    rows = cg.find_callers(name="run", project_root=str(root))
    assert isinstance(rows, list) and rows, "no response for find_callers('run')"
    cand_usrs: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("resolution") == "ambiguous_name" or "same_name_candidates" in r:
            for c in r.get("same_name_candidates") or r.get("candidates") or []:
                if isinstance(c, dict) and c.get("usr"):
                    cand_usrs.add(c["usr"])
                elif isinstance(c, str):
                    cand_usrs.add(c)
    assert expected <= cand_usrs, (
        f"find_callers('run') silently picked one same-name symbol — the three "
        f"candidates {sorted(expected)} must be surfaced; got {rows}"
    )


# ── verifier repairs (V-1 / V-2 / V-3) ─────────────────────────────────────

REPAIR_V2_MULTILINE = (
    "struct Fwd;\n"                              # 1
    "struct Impl { void work(); };\n"            # 2
    "\n"                                         # 3
    "void mystery_caller(Fwd* f)\n"              # 4  (multi-line body)
    "{\n"                                        # 5
    "    f->work();\n"                           # 6
    "}\n"                                        # 7
    "int main() { return 0; }\n"                 # 8
)

REPAIR_V3_MIXED = (
    "struct Fwd;\n"                                        # 1
    "struct Svc { void ping(); void pong(); };\n"          # 2
    "void real_caller(Svc* s) { s->ping(); }\n"            # 3  exact AST edge
    "void ghost_caller(Fwd* f) { f->pong(); }\n"           # 4  possible, zero refs rows for pong
    "int main() { return 0; }\n"                           # 5
)


def test_v2_backfill_never_fabricates_refs_edge(tmp_path: Path):
    """V-2: the cross-TU postprocess backfill must not re-enter refs.

    A multi-line body whose undeclared-member call is text-resolvable by
    the bare-name resolver used to receive a fabricated ref_kind='call'
    edge from backfill_cross_tu_refs (exact-looking, handler-labelled
    certainty=exact).  Repair route (b): backfill discoveries are stored
    as call_sites rows with certainty='possible' and
    origin='cross_tu_backfill'; lines already covered by a call_sites row
    are skipped entirely.
    """
    root, db, ch = _indexed_project(
        tmp_path, "v2_backfill", "backfill.cpp", REPAIR_V2_MULTILINE
    )
    from fw_context_mcp.indexer.ops import backfill_cross_tu_refs

    conn = open_db(db)
    try:
        usr_caller = _usr_of(conn, ch, "mystery_caller")
        usr_impl_work = _usr_of(conn, ch, "Impl::work")
        # The postprocess step already ran inside run(); the fabricated
        # edge must not exist, and no twin call_sites row either.
        assert usr_impl_work not in _call_refs(conn, ch, usr_caller), (
            "V-2 fabrication: backfill re-entered a bare-name guess into "
            "refs as ref_kind='call' (handler labels it certainty=exact)"
        )
        sites = [
            r for r in _cs_rows(conn, ch)
            if r["from_usr"] == usr_caller and r["from_line"] == 6
        ]
        assert sites and sites[0]["certainty"] == "possible", (
            "the f->work() site must persist as a possible call_sites row"
        )
        n_sites_before = len(_cs_rows(conn, ch))

        # Force the backfill to see an uncovered line: drop the main-pass
        # row, then invoke the postprocess step directly.  The discovery
        # must be ROUTED into call_sites (origin=cross_tu_backfill), and
        # refs must stay clean.
        conn.execute(
            "DELETE FROM call_sites WHERE config_hash=? AND from_usr=? AND from_line=6",
            (ch, usr_caller),
        )
        conn.commit()
        added = backfill_cross_tu_refs(conn, ch, root)
        assert added >= 1, "backfill found nothing to route (fixture regression)"
        rows = conn.execute(
            """SELECT * FROM refs WHERE config_hash=? AND ref_kind='call'
               AND from_usr=? AND to_usr=?""",
            (ch, usr_caller, usr_impl_work),
        ).fetchall()
        assert not rows, (
            "V-2: backfill still inserts a refs 'call' edge for a text guess"
        )
        bf = [
            r for r in _cs_rows(conn, ch)
            if r["from_usr"] == usr_caller and r["origin"] == "cross_tu_backfill"
        ]
        assert bf, "route (b) violated: backfill discovery not stored in call_sites"
        assert bf[0]["certainty"] == "possible"
        assert bf[0]["resolution_kind"] == "name_fallback"
        assert json.loads(bf[0]["candidate_usrs"] or "[]") == [usr_impl_work]
        # No twins: the backfill row replaces (not duplicates) the dropped one.
        assert len(_cs_rows(conn, ch)) == n_sites_before
    finally:
        conn.close()


def test_v3_include_possible_reaches_zero_refs_symbols(tmp_path: Path):
    """V-3: include_possible must not be a no-op for a symbol with ZERO
    proven refs rows.  Svc::pong has no AST call edge anywhere; the
    undeclared ghost_caller site is its only caller evidence."""
    root, db, ch = _indexed_project(tmp_path, "v3_zero_refs", "mixed.cpp", REPAIR_V3_MIXED)
    conn = open_db(db)
    try:
        usr_pong = _usr_of(conn, ch, "Svc::pong")
        callers_of_pong = conn.execute(
            """SELECT COUNT(*) AS n FROM refs WHERE config_hash=? AND to_usr=?
               AND ref_kind IN ('call','indirect','implicit_construct')""",
            (ch, usr_pong),
        ).fetchone()
        assert callers_of_pong["n"] == 0, "fixture regression: pong has refs rows"
    finally:
        conn.close()

    from fw_context_mcp.mcp.handlers import callgraph as cg

    default_rows = cg.find_callers(name="Svc::pong", project_root=str(root))
    assert isinstance(default_rows, list) and default_rows
    assert not any(
        isinstance(r, dict) and r.get("caller") == "ghost_caller"
        for r in default_rows
    ), "default (exact-only) response must not show the uncertain caller"
    assert any(isinstance(r, dict) and "unresolved_count" in r for r in default_rows)

    poss_rows = cg.find_callers(
        name="Svc::pong", project_root=str(root), include_possible=True
    )
    merged = [
        r for r in poss_rows
        if isinstance(r, dict) and r.get("certainty") == "possible"
    ]
    assert merged, (
        f"V-3 tautology: include_possible=True returned no possible row for a "
        f"zero-refs symbol (early return pre-empted the merge): {poss_rows}"
    )
    assert merged[0]["caller"] == "ghost_caller"
    assert merged[0]["resolution_kind"] == "name_fallback"
    assert usr_pong in (merged[0]["candidate_usrs"] or [])


def test_v1_callgraph_source_arrows_intact():
    """V-1: db/_callgraph.py must carry proper UTF-8 arrows — the GBK
    misdecode of the GREEN pass garbled the module docstring, comments and
    nine chain-string code lines.  No U+FFFD, no mojibake, arrows >= base."""
    import fw_context_mcp.indexer.db._callgraph as cg_mod

    text = Path(cg_mod.__file__).read_text(encoding="utf-8")
    assert "\ufffd" not in text, "replacement char found: mojibake in _callgraph.py"
    assert text.count("\u2192") >= 27, (
        f"arrow count dropped: {text.count(chr(0x2192))} < 27 (base had 27)"
    )
    assert " \u2192 " in text, "chain-string joiner ' -> ' arrow missing from code"
    # The repaired file must decode identically from raw bytes (no BOM).
    raw = Path(cg_mod.__file__).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM reintroduced"
