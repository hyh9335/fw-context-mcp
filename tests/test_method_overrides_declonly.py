"""W3 — declaration-only method overrides (AUD-R3 / T05 F5).

Regression module for the overrides graph built by ``_build_overrides``
(``indexer/_postprocess.py``).  A declaration-only (header-declared) virtual
method must still produce overrides rows: the libclang ``is_virtual`` /
``is_pure_virtual`` flag is AST relationship evidence, not name inference.
The pre-fix ``is_definition = 1`` gate on the derived-side Phase 1 query
dropped every decl-only override from the graph (T05 F5: header-only
``void run() override;`` yielded zero overrides rows).

Cases (accepted_design_W3.md #3, all pre-registered):
(a) W0 sharp case: decl-only VIRTUAL base + DEFINED derived  -> row present
(b) audit-spec literal: non-virtual decl-only base hiding     -> overrides=[]
    (correct C++: hiding a non-virtual method is not an override)
(c) T05 oracle #5 VERBATIM fixture (decl-only derived override) -> row present
plus W3 seed folds: pure-virtual base, decl-only derived override of a
pure-virtual base, out-of-line definition, unrelated same-name isolation,
overload mismatch, multiple inheritance, and the force-rebuild idempotency
trap (a force rebuild must re-create the graph, not leave it empty).

Fixture bytes for (c) live verbatim in
``tests/fixtures/method_overrides_declonly/t05_f05_calls.cpp`` (SHA256-locked,
``-text`` in the scoped .gitattributes so Windows/WSL bytes stay identical).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "method_overrides_declonly"
T05_CPP = FIXTURES / "t05_f05_calls.cpp"
# SHA256 of the verbatim T05 artifact
# (.fw-orchestrator/artifacts/T05/reproduction/f05_cpp_calls/calls.cpp).
T05_SHA256 = "6c90e7cb4932ac3dc9d3142c1de1fc6ba0dd284ceac15d6cc8016272c9015711"

# ── inline fixtures (W0 sharp case, W3 seed folds) ──────────────────────────

# (a) W0 sharp case — decl-only VIRTUAL base + DEFINED derived override.
W0_SHARP_CPP = """\
struct VBase {
    virtual void vrun(); // declaration ONLY - virtual, no body in this TU
};

struct VDerived : VBase {
    void vrun() override {}  // defined here
};

int main() { VDerived v; v.vrun(); return 0; }
"""

# (b) Audit-spec literal — non-virtual decl-only base method hidden by a
# defined derived method.  NO override relationship exists in C++.
NONVIRTUAL_HIDING_CPP = """\
struct Base {
    void run();          // declaration ONLY - non-virtual
};

struct Derived : Base {
    void run() {}        // defined here - HIDES, does not override
};

int main() { Derived d; d.run(); return 0; }
"""

# Pure-virtual base (decl-only, is_pure_virtual) + defined derived override.
PURE_VIRTUAL_BASE_CPP = """\
struct B {
    virtual void pv() = 0;
};

struct D : B {
    void pv() override {}
};

void use(D& d) { d.pv(); }
"""

# Both sides declaration-only: derived override of a pure-virtual base.
DECLONLY_PURE_VIRTUAL_CPP = """\
struct B {
    virtual void pv() = 0;
};

struct D : B {
    void pv() override;  // declaration only
};

void use(D& d) { d.pv(); }
"""

# Out-of-line definition: declaration in class, body at file scope.
OUT_OF_LINE_CPP = """\
struct B {
    virtual void run();
};

struct D : B {
    void run() override;
};

void D::run() {}
void use(D& d) { d.run(); }
"""

# Overload mismatch: only the matching signature is an override.
OVERLOAD_MISMATCH_CPP = """\
struct B {
    virtual void send(int);
    virtual void send(double);
};

struct D : B {
    void send(int) override;  // matches send(int) only
};

void use(D& d) { d.send(1); }
"""

# Multiple inheritance: two virtual bases, both overridden (decl-only).
MULTIPLE_INHERITANCE_CPP = """\
struct A {
    virtual void fa();
};

struct Bx {
    virtual void fb();
};

struct D : A, Bx {
    void fa() override;
    void fb() override;
};

void use(D& d) { d.fa(); d.fb(); }
"""


# ── harness (SLICE-G pattern: isolated index dir + project id) ──────────────


def _write_cc(root: Path, src_rel: str) -> Path:
    """compile_commands.json in arguments-array form with forward slashes
    (Windows backslash paths in the command-string form get escape-mangled —
    W0 side finding)."""
    fwd = str(root).replace("\\", "/")
    payload = [{
        "directory": fwd,
        "arguments": ["clang++", "-std=c++17", "-c", src_rel, "-o", "build/tu.o"],
        "file": src_rel,
    }]
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


def _write_project_id(root: Path, pid: str) -> None:
    from fw_context_mcp.config.settings import _write_project_id

    _write_project_id(root, pid)


def _indexed_project(
    tmp_path: Path, name: str, source: str | None = None, src_file: Path | None = None
) -> tuple[Path, Path, str]:
    """Fixture project + isolated index dir + full index -> (root, db, hash).

    Exactly one of *source* (inline text, written to src/tu.cpp) or *src_file*
    (checked-in fixture, referenced where it lies) must be given.
    """
    root = tmp_path / name
    root.mkdir()
    if src_file is not None:
        assert source is None
        src_rel = str(src_file).replace("\\", "/")
    else:
        assert source is not None
        src = root / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "tu.cpp").write_text(source, encoding="utf-8")
        src_rel = "src/tu.cpp"
    cc = _write_cc(root, src_rel)
    index_dir = _isolate_index_db(root, tmp_path / f"{name}_state")

    from fw_context_mcp.config.settings import generate_project_id

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
    db_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        cc,
        db_path,
        project_root=root,
        project_id=pid,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
    )
    conn = open_db(db_path)
    ch = conn.execute(
        "SELECT config_hash FROM build_configs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()["config_hash"]
    conn.close()
    return root, db_path, ch


def _overrides_rows(conn: sqlite3.Connection, ch: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM overrides WHERE config_hash=?", (ch,)
    ).fetchall()


def _usr_of(conn: sqlite3.Connection, ch: str, qualified_name: str) -> str:
    row = conn.execute(
        "SELECT usr FROM symbols WHERE config_hash=? AND qualified_name=? LIMIT 1",
        (ch, qualified_name),
    ).fetchone()
    assert row is not None, f"symbol {qualified_name} not indexed"
    return row["usr"]


def _handler_overrides(root: Path, method: str) -> dict:
    from fw_context_mcp.mcp.handlers.inheritance import get_method_overrides

    return get_method_overrides(method_name=method, project_root=str(root))


def _pair(rows: list[sqlite3.Row], derived_usr: str, base_usr: str) -> bool:
    return any(
        r["derived_usr"] == derived_usr and r["base_usr"] == base_usr for r in rows
    )


# ── (c) T05 oracle #5 verbatim fixture ──────────────────────────────────────


def test_t05_fixture_bytes_are_verbatim():
    """The checked-in T05 fixture is byte-identical to the research artifact."""
    assert T05_CPP.is_file()
    assert (
        hashlib.sha256(T05_CPP.read_bytes()).hexdigest() == T05_SHA256
    ), "t05_f05_calls.cpp drifted from the T05 oracle #5 artifact bytes"


def test_t05_f05_declonly_derived_override_row_present(tmp_path: Path):
    """AUD-R3 / T05 F5 — the RED case, now pinned GREEN.

    Fixture (verbatim): Base declares ``virtual void run();`` (decl only),
    ``Derived final : Base`` declares ``void run() override;`` (decl only,
    line 8), plus unrelated same-name ``Other::run``.  The overrides table
    must hold exactly Derived::run -> Base::run; the unrelated non-virtual
    Other::run must appear in no row; the handler surfaces the same graph.
    """
    root, db, ch = _indexed_project(tmp_path, "t05_f05", src_file=T05_CPP)
    conn = open_db(db)
    try:
        rows = _overrides_rows(conn, ch)
        assert len(rows) == 1, f"expected exactly 1 overrides row, got {len(rows)}"
        usr_derived = _usr_of(conn, ch, "Derived::run")
        usr_base = _usr_of(conn, ch, "Base::run")
        usr_other = _usr_of(conn, ch, "Other::run")
        assert _pair(rows, usr_derived, usr_base), (
            "decl-only Derived::run must override decl-only Base::run"
        )
        assert not _pair(rows, usr_other, usr_base), "unrelated Other::run leaked in"
        assert not _pair(rows, usr_derived, usr_other), "Other::run leaked as base"
    finally:
        conn.close()

    mo = _handler_overrides(root, "Derived::run")
    assert mo.get("overrides") and mo["overrides"][0]["qualified_name"] == "Base::run"
    assert mo.get("overridden_by") == []
    other = _handler_overrides(root, "Other::run")
    assert other.get("overrides") == [] and other.get("overridden_by") == []


def test_t05_f05_reverse_query_overridden_by(tmp_path: Path):
    """Base::run (decl-only virtual) reports Derived::run under overridden_by."""
    root, _db, _ch = _indexed_project(tmp_path, "t05_f05_rev", src_file=T05_CPP)
    mo = _handler_overrides(root, "Base::run")
    assert mo.get("overridden_by") and mo["overridden_by"][0]["qualified_name"] == "Derived::run"
    assert mo.get("overrides") == []


# ── (a) W0 sharp case ───────────────────────────────────────────────────────


def test_w0_sharp_case_declonly_virtual_base_defined_derived(tmp_path: Path):
    """(a) W0 sharp case: decl-only virtual BASE + defined derived override.

    The guarantee W0 observed ad hoc but nothing enforced: the row
    VDerived::vrun -> VBase::vrun must exist even though the base method has
    no body in any TU.
    """
    root, db, ch = _indexed_project(tmp_path, "w0_sharp", source=W0_SHARP_CPP)
    conn = open_db(db)
    try:
        rows = _overrides_rows(conn, ch)
        assert _pair(rows, _usr_of(conn, ch, "VDerived::vrun"),
                     _usr_of(conn, ch, "VBase::vrun")), (
            "decl-only virtual base must keep its overrides row"
        )
    finally:
        conn.close()
    mo = _handler_overrides(root, "VDerived::vrun")
    assert mo.get("overrides") and mo["overrides"][0]["qualified_name"] == "VBase::vrun"


# ── (b) spec-literal non-virtual case stays empty ───────────────────────────


def test_spec_literal_nonvirtual_hiding_has_no_row(tmp_path: Path):
    """(b) Non-virtual decl-only base hiding yields NO row — correct C++.

    The fix must not fabricate overrides from name equality: Derived::run
    merely HIDES the non-virtual Base::run.  The audit-spec-literal case from
    W0 stays green (overrides=[]).
    """
    root, db, ch = _indexed_project(tmp_path, "nonvirtual_hiding",
                                    source=NONVIRTUAL_HIDING_CPP)
    conn = open_db(db)
    try:
        assert _overrides_rows(conn, ch) == [], (
            "non-virtual hiding must not produce an overrides row"
        )
    finally:
        conn.close()
    mo = _handler_overrides(root, "Derived::run")
    assert mo.get("overrides") == [] and mo.get("overridden_by") == []


# ── W3 seed folds ───────────────────────────────────────────────────────────


def test_pure_virtual_base_defined_derived_row(tmp_path: Path):
    """Pure-virtual (necessarily decl-only) base + defined derived override."""
    root, db, ch = _indexed_project(tmp_path, "pv_base", source=PURE_VIRTUAL_BASE_CPP)
    conn = open_db(db)
    try:
        assert _pair(_overrides_rows(conn, ch), _usr_of(conn, ch, "D::pv"),
                     _usr_of(conn, ch, "B::pv"))
    finally:
        conn.close()


def test_declonly_derived_overrides_pure_virtual_base(tmp_path: Path):
    """Both sides declaration-only: ``void pv() override;`` against a pure
    virtual base.  The derived method has no body in any TU yet must keep
    the relationship."""
    root, db, ch = _indexed_project(tmp_path, "pv_declonly",
                                    source=DECLONLY_PURE_VIRTUAL_CPP)
    conn = open_db(db)
    try:
        assert _pair(_overrides_rows(conn, ch), _usr_of(conn, ch, "D::pv"),
                     _usr_of(conn, ch, "B::pv"))
    finally:
        conn.close()
    mo = _handler_overrides(root, "D::pv")
    assert mo.get("overrides") and mo["overrides"][0]["qualified_name"] == "B::pv"


def test_out_of_line_definition_row(tmp_path: Path):
    """Out-of-line defined override (body at file scope) keeps its row."""
    root, db, ch = _indexed_project(tmp_path, "out_of_line", source=OUT_OF_LINE_CPP)
    conn = open_db(db)
    try:
        assert _pair(_overrides_rows(conn, ch), _usr_of(conn, ch, "D::run"),
                     _usr_of(conn, ch, "B::run"))
    finally:
        conn.close()


def test_overload_mismatch_only_matching_signature(tmp_path: Path):
    """Overload guard: send(int) matches, send(double) must not."""
    root, db, ch = _indexed_project(tmp_path, "overload", source=OVERLOAD_MISMATCH_CPP)
    conn = open_db(db)
    try:
        rows = _overrides_rows(conn, ch)
        assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}"
        base_int = conn.execute(
            "SELECT usr FROM symbols WHERE config_hash=? AND qualified_name='B::send' "
            "AND signature LIKE '%int%'", (ch,),
        ).fetchone()
        assert base_int is not None and rows[0]["base_usr"] == base_int["usr"], (
            "the single row must target send(int), never send(double)"
        )
    finally:
        conn.close()


def test_multiple_inheritance_two_rows(tmp_path: Path):
    """Two virtual bases, both overridden (decl-only): two rows, right ends."""
    root, db, ch = _indexed_project(tmp_path, "multi_inherit",
                                    source=MULTIPLE_INHERITANCE_CPP)
    conn = open_db(db)
    try:
        rows = _overrides_rows(conn, ch)
        assert _pair(rows, _usr_of(conn, ch, "D::fa"), _usr_of(conn, ch, "A::fa"))
        assert _pair(rows, _usr_of(conn, ch, "D::fb"), _usr_of(conn, ch, "Bx::fb"))
        assert len(rows) == 2
    finally:
        conn.close()


def test_force_rebuild_keeps_overrides_graph(tmp_path: Path):
    """Idempotency trap (design Risks): after the fix, a no-force call on an
    existing graph is a no-op, and a FORCE rebuild must re-create the rows —
    not wipe them and leave a stale-empty graph."""
    from fw_context_mcp.indexer._postprocess import _build_overrides

    _root, db, ch = _indexed_project(tmp_path, "force_rebuild", src_file=T05_CPP)
    db_dir = db.parent
    conn = open_db(db)
    try:
        assert len(_overrides_rows(conn, ch)) == 1

        # No-force call on an existing graph: idempotent no-op, rows intact.
        _build_overrides(conn, ch, db_dir)
        assert len(_overrides_rows(conn, ch)) == 1

        # Force rebuild: rows are deleted then rebuilt.
        _build_overrides(conn, ch, db_dir, force=True)
        assert len(_overrides_rows(conn, ch)) == 1, (
            "force rebuild must re-create the overrides graph"
        )
    finally:
        conn.close()
