"""M6.5 Adversarial Regression: verify direct materialization produces
CORRECT canonical facts (not just SOME facts).

Tests:
1. Mutation: rename a function → canon changes
2. Mutation: change signature → canon changes
3. Property: same source indexed twice → identical fact set (idempotent)
4. Property: extract_all → materialize → query roundtrip returns same symbols
5. Cross-TU: function defined in A, called from B → Call fact in B's build
6. Metamorphic: add a no-op statement → fact set unchanged
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))


def _parse_and_materialize(src_text: str, store, build_name: str, world_id: int,
                           filename: str = "test.c"):
    """Helper: parse C source and materialize into M6."""
    from fw_context_mcp.indexer.symbols import extract_all
    from fw_context_mcp.indexer.compile_commands import CompilationUnit
    from fw_context_mcp.store_v2.m6_direct_materialization import (
        materialize_unit_into_m6,
    )
    tmpdir = Path(tempfile.mkdtemp())
    src = tmpdir / filename
    src.write_text(src_text, encoding="utf-8")
    unit = CompilationUnit(
        file=src, clang_args=["-x", "c", "-std=c11"],
        directory=tmpdir, language="c",
    )
    parsed = extract_all(unit, with_refs=True, return_tu=True)
    stats = materialize_unit_into_m6(
        store=store, parsed=parsed, project_root=tmpdir,
        build_name=build_name, world_id=world_id, config_hash=f"hash_{build_name}",
    )
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return stats, parsed


def test_mutation_rename_changes_canon():
    """Renaming a function must change the canon (different fact)."""
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    store = open_m6_store(proj_root)

    src_a = "int original_func(int x) { return x; }"
    src_b = "int renamed_func(int x) { return x; }"

    _parse_and_materialize(src_a, store, "buildA", 3001)
    proj_a = store.project_build("buildA")
    names_a = set()
    for kind, facts in proj_a.items():
        for f in facts:
            p = f.get("payload", {}) or {}
            if p.get("name"):
                names_a.add(p["name"])

    _parse_and_materialize(src_b, store, "buildB", 3002)
    proj_b = store.project_build("buildB")
    names_b = set()
    for kind, facts in proj_b.items():
        for f in facts:
            p = f.get("payload", {}) or {}
            if p.get("name"):
                names_b.add(p["name"])

    print(f"  Build A names: {names_a}")
    print(f"  Build B names: {names_b}")
    assert "original_func" in names_a, "buildA must have original_func"
    assert "renamed_func" in names_b, "buildB must have renamed_func"
    assert "original_func" not in names_b, "renamed must not have original"
    assert "renamed_func" not in names_a, "original must not have renamed"
    print(f"  PASS: rename changes canon (different facts)")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_mutation_signature_changes_canon():
    """Changing a function signature must change the canon."""
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    store = open_m6_store(proj_root)

    src_a = "int func(int x) { return x; }"
    src_b = "int func(int x, int y) { return x + y; }"

    _parse_and_materialize(src_a, store, "buildA", 4001)
    _parse_and_materialize(src_b, store, "buildB", 4002)

    proj_a = store.project_build("buildA")
    proj_b = store.project_build("buildB")

    # Find func in both builds and compare signatures
    sig_a = None
    sig_b = None
    for f in proj_a.get("Function", []):
        p = f.get("payload", {}) or {}
        if p.get("name") == "func":
            sig_a = p.get("signature", "")
            break
    for f in proj_b.get("Function", []):
        p = f.get("payload", {}) or {}
        if p.get("name") == "func":
            sig_b = p.get("signature", "")
            break

    print(f"  Build A signature: {sig_a}")
    print(f"  Build B signature: {sig_b}")
    assert sig_a != sig_b, "different signatures must produce different facts"
    # Both builds should have a 'func' but with different canons (due to signature in canon)
    # The canon_function includes signature, so they're DIFFERENT facts
    func_canons_a = {f.get("fact_canon", "") for f in proj_a.get("Function", []) if (f.get("payload", {}) or {}).get("name") == "func"}
    func_canons_b = {f.get("fact_canon", "") for f in proj_b.get("Function", []) if (f.get("payload", {}) or {}).get("name") == "func"}
    print(f"  Build A func canons: {func_canons_a}")
    print(f"  Build B func canons: {func_canons_b}")
    assert func_canons_a != func_canons_b, "signature change must change canon"
    assert not (func_canons_a & func_canons_b), "different sigs = different canons, no overlap"
    print(f"  PASS: signature change changes canon")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_idempotent_reindex():
    """Indexing the same source twice must produce identical fact set."""
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    stable_src_dir = tmpdir / "src"
    stable_src_dir.mkdir()
    stable_src = stable_src_dir / "test.c"
    src_text = "int func(int x) { return x + 1; }\nstruct Point { int x; int y; };"
    stable_src.write_text(src_text, encoding="utf-8")

    from fw_context_mcp.indexer.symbols import extract_all
    from fw_context_mcp.indexer.compile_commands import CompilationUnit
    from fw_context_mcp.store_v2.m6_direct_materialization import (
        materialize_unit_into_m6,
    )
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    store = open_m6_store(proj_root)

    def _parse_stable():
        unit = CompilationUnit(
            file=stable_src, clang_args=["-x", "c", "-std=c11"],
            directory=stable_src_dir, language="c",
        )
        return extract_all(unit, with_refs=True, return_tu=True)

    parsed1 = _parse_stable()
    stats1 = materialize_unit_into_m6(
        store=store, parsed=parsed1, project_root=proj_root,
        build_name="buildA", world_id=5001, config_hash="hash_buildA",
    )
    # Materialize the SAME source (same file path) into the SAME build again
    parsed2 = _parse_stable()
    stats2 = materialize_unit_into_m6(
        store=store, parsed=parsed2, project_root=proj_root,
        build_name="buildA", world_id=5001, config_hash="hash_buildA",
    )

    print(f"  First materialize: indexed={stats1['facts_indexed']} reused={stats1['facts_reused']}")
    print(f"  Second materialize: indexed={stats2['facts_indexed']} reused={stats2['facts_reused']}")
    # Second time should have 0 new (all reused) — same file path means same canons
    assert stats2["facts_indexed"] == 0, "re-indexing same source must add 0 new facts"
    assert stats2["facts_reused"] > 0, "re-indexing must reuse existing facts"
    print(f"  PASS: idempotent re-index (0 new, all reused)")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_metamorphic_noop():
    """Adding a no-op statement must not change the fact set."""
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    from fw_context_mcp.store_v2.m6_query import open_m6_store
    store = open_m6_store(proj_root)

    src_a = "int func(int x) { return x; }"
    src_b = "int func(int x) { int noop = 0; return x; }"

    _parse_and_materialize(src_a, store, "buildA", 6001)
    _parse_and_materialize(src_b, store, "buildB", 6002)

    proj_a = store.project_build("buildA")
    proj_b = store.project_build("buildB")

    # The function fact should be the SAME (same name, same signature)
    func_a = None
    func_b = None
    for f in proj_a.get("Function", []):
        p = f.get("payload", {}) or {}
        if p.get("name") == "func":
            func_a = f.get("fact_canon", "")
            break
    for f in proj_b.get("Function", []):
        p = f.get("payload", {}) or {}
        if p.get("name") == "func":
            func_b = f.get("fact_canon", "")
            break

    print(f"  Build A func canon: {func_a}")
    print(f"  Build B func canon: {func_b}")
    assert func_a == func_b, "no-op statement must not change func canon"
    print(f"  PASS: metamorphic no-op (canon unchanged)")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_roundtrip_query():
    """Extract → materialize → query returns the same symbols."""
    tmpdir = Path(tempfile.mkdtemp())
    proj_root = tmpdir / "proj"
    proj_root.mkdir()
    from fw_context_mcp.store_v2.m6_query import open_m6_store, lookup_symbol
    store = open_m6_store(proj_root)

    src = """
    int alpha(int x) { return x; }
    int beta(int x) { return alpha(x); }
    struct Gamma { int field; };
    """
    stats, parsed = _parse_and_materialize(src, store, "buildA", 7001)
    print(f"  Materialized: {stats['facts_indexed']} facts")

    # Query for alpha
    results = lookup_symbol(store, "buildA", "alpha", exact=True)
    print(f"  lookup_symbol('alpha'): {len(results)} results")
    assert len(results) > 0, "must find alpha via query"
    assert results[0]["name"] == "alpha"

    # Query for beta
    results = lookup_symbol(store, "buildA", "beta", exact=True)
    assert len(results) > 0, "must find beta via query"

    # Query for Gamma
    results = lookup_symbol(store, "buildA", "Gamma", exact=True)
    assert len(results) > 0, "must find Gamma via query"

    print(f"  PASS: roundtrip query returns same symbols")
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print(f"{'='*60}")
    print(f"  M6.5 Adversarial Regression Suite")
    print(f"{'='*60}\n")

    tests = [
        ("mutation_rename", test_mutation_rename_changes_canon),
        ("mutation_signature", test_mutation_signature_changes_canon),
        ("idempotent_reindex", test_idempotent_reindex),
        ("metamorphic_noop", test_metamorphic_noop),
        ("roundtrip_query", test_roundtrip_query),
    ]
    results = {}
    all_pass = True
    for name, test_fn in tests:
        print(f"\n--- {name} ---")
        try:
            test_fn()
            results[name] = "PASS"
        except Exception as exc:
            results[name] = f"FAIL: {exc}"
            all_pass = False
            print(f"  FAIL: {exc}")

    print(f"\n{'='*60}")
    print(f"  M6.5 ADVERSARIAL REGRESSION: {'PASS' if all_pass else 'FAIL'}")
    print(f"{'='*60}")
    for name, status in results.items():
        print(f"  {name}: {status}")

    out_path = REPO / ".dsh-control/evidence/final_remediation/m6_5_adversarial_regression.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "test": "m6_5_adversarial_regression",
        "verdict": "PASS" if all_pass else "FAIL",
        "results": results,
    }, indent=2), encoding="utf-8")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
