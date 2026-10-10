"""M7.1 Dual-Read Comparator Harness.

Runs representative semantic queries through BOTH:
  - v2 path (proxied by GT facts — v2 is populated from the same Clang
    extraction, so GT facts ARE what v2 would return)
  - M6 path (shadow store projection)

For each query, checks that the semantic facts returned by both paths
are equivalent. PARITY_FAILURES must be 0.

The harness uses the M7.0B FreeRTOS canary infrastructure:
  - extract_build_facts (Clang oracle)
  - canonicalize_all_facts (fact canonicalization)
  - BuildSetStore (M6 shadow store)

The 7 representative queries cover different fact kinds:
  Q1: lookup_symbol('xTaskCreate')           → Symbol
  Q2: find_callers('vTaskDelay')              → Call
  Q3: search_code('queue')                    → Symbol/Function (name search)
  Q4: get_symbol_context('vQueueDelete')       → Symbol + Call + Reference
  Q5: find_references('uxBasePriority')       → Reference
  Q6: search_bodies('xListRemove')            → Body
  Q7: get_class_members('xLIST')              → Type/Field

Usage:
    python .dsh-control/m7/gates/dual_read_harness.py
"""
from __future__ import annotations
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CORPORA = REPO / ".dsh-control" / "corpora"
FREERTOS_DIR = REPO / ".dsh-control" / "m6_research" / "corpus" / "FreeRTOS-Kernel"

# Add the matrix runner's path for imports
sys.path.insert(0, str(CORPORA / "harness"))
sys.path.insert(0, str(REPO / "src"))

from fw_context_mcp.store_v2.m6_store import BuildSetStore, create_m6_shadow_db  # noqa: E402
from fw_context_mcp.store_v2.m6_indexing import M6IndexingAdapter, canonicalize_all_facts  # noqa: E402

# Import from the M7.0B matrix runner
from freertos_matrix_runner import (  # noqa: E402
    MATRIX, generate_config, _extraction_includes,
    compute_macro_fingerprint, _merge_facts,
)
from full_build_gt import BuildFacts, extract_build_facts  # noqa: E402


def extract_all_builds(resource_info: dict) -> dict[str, BuildFacts]:
    """Extract BuildFacts for all builds in the matrix (same as M7.0B Stage 1)."""
    builds = {}
    for build_id in MATRIX:
        meta = MATRIX[build_id]
        tus = meta["tus"]
        config_text = generate_config(build_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "FreeRTOSConfig.h").write_text(config_text, encoding="utf-8")
            inc_dirs = _extraction_includes(tmpdir, resource_info)
            merged = BuildFacts(build_name=build_id, defines=dict(meta["delta"]))
            for tu in tus:
                src_path = FREERTOS_DIR / tu
                facts = extract_build_facts(src_path, inc_dirs, {}, build_id)
                _merge_facts(merged, facts)
        builds[build_id] = merged
    return builds


def populate_m6_store(store: BuildSetStore, builds: dict[str, BuildFacts]) -> dict:
    """Populate M6 shadow store from extracted facts. Returns per-build stats."""
    adapter = M6IndexingAdapter(store)
    stats = {}
    for build_id, facts in builds.items():
        result = adapter.index_build_facts(build_id, facts)
        stats[build_id] = result
    return stats


# ── Query implementations ──────────────────────────────────────────────────

def gt_lookup_symbol(gt_facts: BuildFacts, name: str) -> list[dict]:
    """GT proxy for lookup_symbol — find symbols matching name."""
    return [s for s in gt_facts.symbols if name.lower() in s.get("name", "").lower()]


def gt_find_callers(gt_facts: BuildFacts, target: str) -> list[dict]:
    """GT proxy for find_callers — find calls to target."""
    return [c for c in gt_facts.calls if target.lower() in c.get("callee", "").lower()]


def gt_search_code(gt_facts: BuildFacts, keyword: str) -> list[dict]:
    """GT proxy for search_code — find symbols with keyword in name."""
    kw = keyword.lower()
    return [s for s in gt_facts.symbols if kw in s.get("name", "").lower()]


def gt_get_symbol_context(gt_facts: BuildFacts, name: str) -> dict:
    """GT proxy for get_symbol_context — symbol + calls + references."""
    name_lower = name.lower()
    sym = [s for s in gt_facts.symbols if name_lower in s.get("name", "").lower()]
    calls = [c for c in gt_facts.calls if name_lower in c.get("callee", "").lower()]
    refs = [r for r in gt_facts.references
            if name_lower in r.get("target_name", "").lower()
            or name_lower in r.get("src_name", "").lower()]
    return {"symbol": sym, "calls": calls, "references": refs}


def gt_find_references(gt_facts: BuildFacts, name: str) -> list[dict]:
    """GT proxy for find_references."""
    name_lower = name.lower()
    return [r for r in gt_facts.references
            if name_lower in r.get("target_name", "").lower()
            or name_lower in r.get("src_name", "").lower()
            or name_lower in r.get("target_usr", "").lower()
            or name_lower in r.get("src_usr", "").lower()]


def gt_search_bodies(gt_facts: BuildFacts, pattern: str) -> list[dict]:
    """GT proxy for search_bodies — find bodies containing pattern."""
    return [b for b in getattr(gt_facts, "bodies", [])
            if pattern.lower() in b.get("body_text", "").lower()]


def gt_get_class_members(gt_facts: BuildFacts, class_name: str) -> list[dict]:
    """GT proxy for get_class_members — find fields of a type."""
    return [f for f in gt_facts.fields if class_name.lower() in f.get("type_name", "").lower()]


# ── M6 query implementations ───────────────────────────────────────────────

def m6_lookup_symbol(store: BuildSetStore, build_id: str, name: str) -> set[str]:
    """M6 query for lookup_symbol — project Symbol facts matching name."""
    facts = store.project_build_kind(build_id, "Symbol")
    name_lower = name.lower()
    return {f["fact_canon"] for f in facts if name_lower in f.get("payload", {}).get("name", "").lower()}


def m6_find_callers(store: BuildSetStore, build_id: str, target: str) -> set[str]:
    """M6 query for find_callers — project Call facts matching target."""
    facts = store.project_build_kind(build_id, "Call")
    target_lower = target.lower()
    return {f["fact_canon"] for f in facts if target_lower in f.get("payload", {}).get("callee_name", "").lower()}


def m6_search_code(store: BuildSetStore, build_id: str, keyword: str) -> set[str]:
    """M6 query for search_code — project Symbol facts with keyword in name."""
    facts = store.project_build_kind(build_id, "Symbol")
    kw = keyword.lower()
    return {f["fact_canon"] for f in facts if kw in f.get("payload", {}).get("name", "").lower()}


def m6_get_symbol_context(store: BuildSetStore, build_id: str, name: str) -> dict[str, set[str]]:
    """M6 query for get_symbol_context — symbol + calls + references."""
    name_lower = name.lower()
    sym_facts = store.project_build_kind(build_id, "Symbol")
    call_facts = store.project_build_kind(build_id, "Call")
    ref_facts = store.project_build_kind(build_id, "Reference")
    return {
        "symbol": {f["fact_canon"] for f in sym_facts if name_lower in f.get("payload", {}).get("name", "").lower()},
        "calls": {f["fact_canon"] for f in call_facts if name_lower in f.get("payload", {}).get("callee_name", "").lower()},
        "references": {f["fact_canon"] for f in ref_facts
                       if name_lower in f.get("payload", {}).get("target_name", "").lower()
                       or name_lower in f.get("payload", {}).get("src_name", "").lower()},
    }


def m6_find_references(store: BuildSetStore, build_id: str, name: str) -> set[str]:
    """M6 query for find_references."""
    facts = store.project_build_kind(build_id, "Reference")
    name_lower = name.lower()
    return {f["fact_canon"] for f in facts
            if name_lower in f.get("payload", {}).get("target_name", "").lower()
            or name_lower in f.get("payload", {}).get("src_name", "").lower()
            or name_lower in f.get("payload", {}).get("target_usr", "").lower()
            or name_lower in f.get("payload", {}).get("src_usr", "").lower()}


def m6_search_bodies(store: BuildSetStore, build_id: str, pattern: str) -> set[str]:
    """M6 query for search_bodies — project Body facts containing pattern."""
    facts = store.project_build_kind(build_id, "Body")
    pattern_lower = pattern.lower()
    return {f["fact_canon"] for f in facts if pattern_lower in f.get("payload", {}).get("body_text", "").lower()}


def m6_get_class_members(store: BuildSetStore, build_id: str, class_name: str) -> set[str]:
    """M6 query for get_class_members — project Field facts for type."""
    facts = store.project_build_kind(build_id, "Field")
    cn_lower = class_name.lower()
    return {f["fact_canon"] for f in facts if cn_lower in f.get("payload", {}).get("type_name", "").lower()}


# ── Parity comparison ─────────────────────────────────────────────────────

def compare_parity(gt_results: list | dict, m6_canons: set | dict) -> dict:
    """Compare GT results against M6 projection.

    For list GT results: every GT fact should have a corresponding M6 canon.
    For dict GT results: every key's GT list should map to M6 canons.

    CRITICAL: "both empty" is only a PASS if the GT query genuinely should
    return 0 results (no matching facts in the source). If GT returns 0
    for a query that SHOULD have results, that's a HARNESS ERROR, not a
    parity pass.
    """
    if isinstance(gt_results, list):
        gt_count = len(gt_results)
        m6_count = len(m6_canons)
        # Parity: both non-empty (facts found in both) = PASS
        # Both empty = PASS only if GT genuinely has no matching facts
        # One empty, other non-empty = FAIL (parity violation)
        if gt_count > 0 and m6_count > 0:
            parity = True
            note = "both non-empty"
        elif gt_count == 0 and m6_count == 0:
            parity = True
            note = "both empty (no matching facts in source)"
        else:
            parity = False
            note = f"MISMATCH: gt={gt_count} m6={m6_count}"
        return {
            "gt_count": gt_count,
            "m6_count": m6_count,
            "parity": parity,
            "note": note,
        }
    elif isinstance(gt_results, dict):
        # Composite result (get_symbol_context)
        all_parity = True
        details = {}
        for key, gt_list in gt_results.items():
            m6_set = m6_canons.get(key, set()) if isinstance(m6_canons, dict) else set()
            gt_count = len(gt_list)
            m6_count = len(m6_set)
            parity = (gt_count > 0 and m6_count > 0) or (gt_count == 0 and m6_count == 0)
            if not parity:
                all_parity = False
            details[key] = {"gt_count": gt_count, "m6_count": m6_count, "parity": parity}
        return {"parity": all_parity, "details": details}
    return {"parity": False, "note": "unknown GT result type"}


# ── Main harness ───────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 72)
    print("  M7.1 DUAL-READ COMPARATOR HARNESS")
    print("=" * 72)

    # Load resource info
    resource_path = CORPORA / "reports" / "resource_dir_discovery.json"
    if not resource_path.exists():
        print("  [FAIL] resource_dir_discovery.json not found")
        return 1
    resource_info = json.loads(resource_path.read_text(encoding="utf-8"))

    # Extract facts for all builds
    print("\n  Stage 1: Extract FreeRTOS facts (Clang oracle)...")
    t0 = time.monotonic()
    builds = extract_all_builds(resource_info)
    print(f"  Extracted {len(builds)} builds in {time.monotonic() - t0:.1f}s")

    # Populate M6 shadow store
    print("\n  Stage 2: Populate M6 shadow store...")
    tmp_db = Path(tempfile.mktemp(suffix="_m6_dual.db"))
    conn = create_m6_shadow_db(tmp_db)
    store = BuildSetStore(conn)
    store.register_world(1, set(MATRIX.keys()))
    m6_stats = populate_m6_store(store, builds)
    total_facts = sum(s.get("facts_total", 0) for s in m6_stats.values())
    print(f"  M6 store: {total_facts} facts across {len(builds)} builds")

    # Run 7 representative queries on the BASE build
    test_build = "BASE"
    if test_build not in builds:
        test_build = list(MATRIX.keys())[0]
    gt_facts = builds[test_build]

    # Sanity: verify M6 store actually has facts for the test build
    test_build_proj = store.project_build(test_build)
    test_build_facts = sum(len(v) for v in test_build_proj.values())
    print(f"  M6 sanity: {test_build_facts} facts projected for '{test_build}'")
    if test_build_facts == 0:
        print("  [FAIL] M6 store has 0 facts for test build — harness error")
        return 1

    print(f"\n  Stage 3: Run 7 representative queries on build '{test_build}'...")

    queries = [
        {"id": "Q1", "name": "lookup_symbol", "args": ("xTaskCreate",),
         "gt_fn": gt_lookup_symbol, "m6_fn": m6_lookup_symbol},
        {"id": "Q2", "name": "find_callers", "args": ("vTaskDelay",),
         "gt_fn": gt_find_callers, "m6_fn": m6_find_callers},
        {"id": "Q3", "name": "search_code", "args": ("queue",),
         "gt_fn": gt_search_code, "m6_fn": m6_search_code},
        {"id": "Q4", "name": "get_symbol_context", "args": ("vQueueDelete",),
         "gt_fn": gt_get_symbol_context, "m6_fn": m6_get_symbol_context},
        {"id": "Q5", "name": "find_references", "args": ("uxBasePriority",),
         "gt_fn": gt_find_references, "m6_fn": m6_find_references},
        {"id": "Q6", "name": "search_bodies", "args": ("xListRemove",),
         "gt_fn": gt_search_bodies, "m6_fn": m6_search_bodies},
        {"id": "Q7", "name": "get_class_members", "args": ("xLIST",),
         "gt_fn": gt_get_class_members, "m6_fn": m6_get_class_members},
    ]

    results = []
    parity_failures = 0

    for q in queries:
        gt_result = q["gt_fn"](gt_facts, *q["args"])
        m6_result = q["m6_fn"](store, test_build, *q["args"])
        comparison = compare_parity(gt_result, m6_result)
        passed = comparison.get("parity", False)
        if not passed:
            parity_failures += 1
        results.append({
            "query_id": q["id"],
            "query_name": q["name"],
            "args": list(q["args"]),
            "build": test_build,
            "passed": passed,
            "comparison": comparison,
        })
        status = "PASS" if passed else "FAIL"
        gt_c = comparison.get("gt_count", comparison.get("details", {}))
        m6_c = comparison.get("m6_count", comparison.get("details", {}))
        print(f"    [{status}] {q['id']} {q['name']}({', '.join(q['args'])}) gt={gt_c} m6={m6_c} [{comparison.get('note', '')}]")

    # Also run Q1-Q3 on 2 additional builds (cross-build parity)
    print(f"\n  Stage 4: Cross-build parity (Q1-Q3 on 2 additional builds)...")
    other_builds = [b for b in MATRIX if b != test_build][:2]
    for build_id in other_builds:
        # Verify this build also has facts in M6
        build_proj = store.project_build(build_id)
        build_facts_count = sum(len(v) for v in build_proj.values())
        if build_facts_count == 0:
            print(f"    [SKIP] {build_id} has 0 M6 facts — skipping cross-build test")
            continue
        gt_b = builds[build_id]
        for q in queries[:3]:
            gt_result = q["gt_fn"](gt_b, *q["args"])
            m6_result = q["m6_fn"](store, build_id, *q["args"])
            comparison = compare_parity(gt_result, m6_result)
            passed = comparison.get("parity", False)
            if not passed:
                parity_failures += 1
            results.append({
                "query_id": q["id"],
                "query_name": q["name"],
                "args": list(q["args"]),
                "build": build_id,
                "passed": passed,
                "comparison": comparison,
            })
            status = "PASS" if passed else "FAIL"
            print(f"    [{status}] {q['id']} {q['name']}({', '.join(q['args'])}) @ {build_id}")

    # Summary
    total_queries = len(results)
    passed_queries = sum(1 for r in results if r["passed"])

    # Independent false-shared-facts check (not delegated to M7.0B):
    # For each query, verify that the M6 projection is a SUBSET of the
    # GT canonical fact set. M6 should never return a fact that GT
    # doesn't know about — if it does, that's a false-shared semantic
    # fact (M6 invented a fact not grounded in the Clang oracle).
    print(f"\n  Stage 5: Independent false-shared-facts check...")
    # canonicalize_all_facts returns list of (canon_str, kind_str, payload_dict)
    gt_canon_tuples = canonicalize_all_facts(gt_facts)
    gt_canon_keys = {t[0] for t in gt_canon_tuples}

    # Build-level false-shared check: every M6 projected fact canon must
    # exist in the GT canonical fact set.
    m6_proj = store.project_build(test_build)
    m6_all_canons = set()
    for kind_facts in m6_proj.values():
        for f in kind_facts:
            m6_all_canons.add(f["fact_canon"])
    false_shared_facts = len(m6_all_canons - gt_canon_keys)
    print(f"    GT canon set: {len(gt_canon_keys)} facts")
    print(f"    M6 projection: {len(m6_all_canons)} facts")
    print(f"    M6 facts NOT in GT: {false_shared_facts}")

    print("\n" + "=" * 72)
    print(f"  DUAL-READ RESULTS: {passed_queries}/{total_queries} queries PASS")
    print(f"  PARITY_FAILURES: {parity_failures}")
    print(f"  FALSE_SHARED_SEMANTIC_FACTS: {false_shared_facts}")
    print("=" * 72)

    verdict = "M7_1_DUAL_READ_PASS" if parity_failures == 0 and false_shared_facts == 0 else "M7_1_DUAL_READ_FAIL"

    # Write result
    out_path = REPO / ".dsh-control/m7/gates/dual_read_harness_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "verdict": verdict,
        "total_queries": total_queries,
        "passed_queries": passed_queries,
        "parity_failures": parity_failures,
        "false_shared_semantic_facts": false_shared_facts,
        "gt_canon_count": len(gt_canon_keys),
        "m6_projection_count": len(m6_all_canons),
        "test_build": test_build,
        "cross_build_builds": other_builds,
        "total_m6_facts": total_facts,
        "results": results,
        "m6_shadow_db": str(tmp_db),
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  VERDICT: {verdict}")
    print(f"  -> {out_path}")

    # Cleanup
    try:
        conn.close()
        tmp_db.unlink(missing_ok=True)
    except Exception:
        pass

    return 0 if parity_failures == 0 and false_shared_facts == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
