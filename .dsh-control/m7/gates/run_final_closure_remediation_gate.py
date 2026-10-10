"""FINAL CLOSURE REMEDIATION HARD GATE (§31).

Consolidates all four issues (A, B, C, D) into a single executable gate.
Returns exit 0 ONLY if all hard requirements are met.

Required equalities (§31):
  - OLD_DB_ROWS_REQUIRED_AS_SEMANTIC_INPUT = 0  (Issue A)
  - M6_IS_MATERIALIZATION_AUTHORITY = true      (Issue A)
  - TOOL_PARITY_STUB_COUNT = 0                   (Issue B)
  - TOOL_PARITY_REAL_COUNT >= 9                  (Issue B)
  - WORLD_ID_HARDCODED = false                  (Issue C)
  - MULTI_BUILD_QUERY_ISOLATION = true           (Issue C)
  - SHARED_FACT_DEDUP = true                    (Issue C)
  - FREERTOS_CANARY_FACTS > 0                   (Canary)
  - FREERTOS_CANARY_LEGACY_DB_REQUIRED = false   (Canary)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EVIDENCE = REPO / ".dsh-control" / "evidence" / "final_remediation"


def _load(name: str) -> dict:
    path = EVIDENCE / name
    if not path.exists():
        return {"_missing": True, "_path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    print(f"{'='*70}")
    print(f"  FINAL CLOSURE REMEDIATION HARD GATE")
    print(f"{'='*70}")

    checks = []

    # ── Issue A: Direct Materialization ──────────────────────────────────
    green_a = _load("direct_materialization_green_test.json")
    kill_a = _load("old_materialization_kill_test.json")
    issue_a_pass = (
        green_a.get("verdict") == "PASS"
        and green_a.get("old_db_rows_required_as_semantic_input") == "0"
        and green_a.get("m6_is_materialization_authority") is True
    )
    checks.append({
        "name": "A_direct_materialization",
        "required": "OLD_DB_ROWS_REQUIRED_AS_SEMANTIC_INPUT=0 AND M6_IS_AUTHORITY=true",
        "actual": f"green={green_a.get('verdict')}, facts={green_a.get('facts_indexed_directly', '?')}, legacy_db={green_a.get('legacy_db_created', '?')}",
        "pass": issue_a_pass,
    })

    # ── Issue B: Tool Parity ─────────────────────────────────────────────
    green_b = _load("tool_parity_green_test.json")
    issue_b_pass = (
        green_b.get("verdict") == "PASS"
        and green_b.get("stub_count", 999) == 0
        and green_b.get("real_count", 0) >= 9
    )
    checks.append({
        "name": "B_tool_parity",
        "required": "STUB_COUNT=0 AND REAL_COUNT>=9",
        "actual": f"stub_count={green_b.get('stub_count', '?')}, real_count={green_b.get('real_count', '?')}",
        "pass": issue_b_pass,
    })

    # ── Issue C: Multi-Build World ───────────────────────────────────────
    green_c = _load("multi_build_world_test.json")
    issue_c_pass = (
        green_c.get("verdict") == "PASS"
        and green_c.get("world_id_hardcoded") is False
        and green_c.get("query_isolation") is True
        and green_c.get("shared_fact_dedup") is True
    )
    checks.append({
        "name": "C_multi_build_world",
        "required": "WORLD_ID_HARDCODED=false AND QUERY_ISOLATION=true AND SHARED_FACT_DEDUP=true",
        "actual": f"world_id_hardcoded={green_c.get('world_id_hardcoded', '?')}, isolation={green_c.get('query_isolation', '?')}, dedup={green_c.get('shared_fact_dedup', '?')}",
        "pass": issue_c_pass,
    })

    # ── FreeRTOS Production Canary ────────────────────────────────────────
    canary = _load("freertos_production_canary.json")
    canary_pass = (
        canary.get("verdict") == "PASS"
        and canary.get("total_m6_facts", 0) > 0
        and canary.get("old_db_rows_required_as_semantic_input") == "0"
        and canary.get("m6_is_materialization_authority") is True
    )
    checks.append({
        "name": "freertos_canary",
        "required": "FACTS>0 AND LEGACY_DB_REQUIRED=false AND M6_IS_AUTHORITY=true",
        "actual": f"facts={canary.get('total_m6_facts', '?')}, legacy_db_rows={canary.get('legacy_db_rows', '?')}",
        "pass": canary_pass,
    })

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    all_pass = True
    for c in checks:
        status = "PASS" if c["pass"] else "FAIL"
        if not c["pass"]:
            all_pass = False
        print(f"  [{status}] {c['name']}")
        print(f"         required: {c['required']}")
        print(f"         actual:   {c['actual']}")

    print(f"\n{'='*70}")
    if all_pass:
        print(f"  FINAL CLOSURE REMEDIATION GATE: PASS")
        print(f"{'='*70}")
        print(f"  All four issues remediated:")
        print(f"    A. M6 is production semantic-materialization authority (direct from libclang)")
        print(f"    B. All {green_b.get('real_count', 0)} M6 query adapters are real (0 stubs)")
        print(f"    C. Multi-build World correctness (distinct world_ids, query isolation, dedup)")
        print(f"    Canary. FreeRTOS BASE: {canary.get('total_m6_facts', 0)} facts, no legacy DB")
        print(f"\n  Required equalities satisfied:")
        print(f"    OLD_DB_ROWS_REQUIRED_AS_SEMANTIC_INPUT = 0")
        print(f"    M6_IS_MATERIALIZATION_AUTHORITY = true")
        print(f"    TOOL_PARITY_STUB_COUNT = 0")
        print(f"    WORLD_ID_HARDCODED = false")
        print(f"    MULTI_BUILD_QUERY_ISOLATION = true")
        print(f"    FREERTOS_CANARY_FACTS > 0")
        print(f"    FREERTOS_CANARY_LEGACY_DB_REQUIRED = false")
    else:
        print(f"  FINAL CLOSURE REMEDIATION GATE: FAIL")
        print(f"{'='*70}")
        failed = [c["name"] for c in checks if not c["pass"]]
        print(f"  Failed checks: {failed}")

    # Save gate result
    result = {
        "gate": "final_closure_remediation_gate",
        "verdict": "PASS" if all_pass else "FAIL",
        "checks": checks,
        "all_required_equalities_satisfied": all_pass,
    }
    out_path = EVIDENCE / "final_closure_remediation_gate.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  -> {out_path}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
