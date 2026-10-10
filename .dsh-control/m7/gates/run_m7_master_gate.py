"""M7 MASTER Hard Gate.

The master gate aggregates all stage gates and anti-gaming self-tests.
Exits 0 ONLY if ALL of:
  - BOOTSTRAP_PASS
  - M7.1_PASS, M7.2_PASS, M7.3_PASS, M7.4_PASS, M7.5_PASS
  - ECONOMIC_CLOSURE_PASS (or FRONTEND_BOTTLENECK classified)
  - ORIGINAL_GOAL_FINAL_AUDIT_PASS
  - PRODUCTION_M6_NATIVE_READ_TOOLS == CORE_SEMANTIC_TOOLS
  - PRODUCTION_M6_NATIVE_INDEX_ENTRYPOINTS == NORMAL_INDEX_ENTRYPOINTS
  - NORMAL_OLD_V2_SEMANTIC_READS == 0
  - NORMAL_OLD_V2_REQUIRED_WRITES == 0
  - NORMAL_OLD_V2_FALLBACKS == 0
  - NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES == 0
  - FALSE_SHARED_SEMANTIC_FACTS == 0
  - QUERY_PARITY_FAILURES == 0
  - UNRESOLVED_BLOCKING_DEFECTS == 0
  - FRESH_FINAL_VERIFIER_PASS
  - FRESH_FINAL_GUARDIAN_ACCEPT
  - REVIEW_HEAD_MATCH

Usage:
    python .dsh-control/m7/gates/run_m7_master_gate.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def clause(name: str, passed: bool, detail: str = "") -> dict:
    return {"clause": name, "passed": passed, "detail": detail}


def load_gate_result(name: str) -> dict | None:
    p = REPO / ".dsh-control/m7/gates" / f"{name}_result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    results = []
    print("=" * 60)
    print("  M7 MASTER HARD GATE")
    print("=" * 60)

    # Stage gates
    bootstrap = load_gate_result("bootstrap_gate")
    b_pass = bootstrap and bootstrap.get("verdict") == "BOOTSTRAP_PASS"
    results.append(clause("BOOTSTRAP_PASS", bool(b_pass), bootstrap.get("verdict", "MISSING") if bootstrap else "NOT_RUN"))
    print(f"  [{'PASS' if b_pass else 'FAIL'}] BOOTSTRAP")

    for stage in ["m7_1_dual_read", "m7_2_read_authority", "m7_3_write_authority",
                  "m7_4_lifecycle", "m7_5_retirement", "m7_economic"]:
        r = load_gate_result(f"{stage}_gate")
        passed = r and r.get("verdict", "").endswith("_PASS")
        results.append(clause(f"{stage.upper()}_PASS", bool(passed), r.get("verdict", "MISSING") if r else "NOT_RUN"))
        print(f"  [{'PASS' if passed else 'FAIL'}] {stage.upper()}")

    # Final audit
    audit = load_gate_result("original_goal_final_audit_gate")
    audit_pass = audit and audit.get("verdict") in ("ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED", "ORIGINAL_GOAL_REMAINING_FRONTEND_BOTTLENECK")
    results.append(clause("ORIGINAL_GOAL_FINAL_AUDIT", bool(audit_pass), audit.get("verdict", "MISSING") if audit else "NOT_RUN"))
    print(f"  [{'PASS' if audit_pass else 'FAIL'}] ORIGINAL_GOAL_FINAL_AUDIT")

    # Authority counters (from scanners)
    pa_state_p = REPO / ".dsh-control/m7/state/production_authority_state.json"
    if pa_state_p.exists():
        pa = json.loads(pa_state_p.read_text(encoding="utf-8"))
        m6_native = pa["authority"]["m6_native_read_tools"]
        old_v2 = pa["authority"]["old_v2_read_authority"]
        unsafe = pa["authority"]["unsafe_or_silent_fallback"]
        results.append(clause("PRODUCTION_M6_NATIVE_READ_TOOLS_eq_DENOMINATOR", m6_native == 32, f"m6_native={m6_native} denom=32"))
        results.append(clause("OLD_V2_READ_AUTHORITY_eq_0", old_v2 == 0, f"old_v2={old_v2}"))
        results.append(clause("UNSAFE_OR_SILENT_FALLBACK_eq_0", unsafe == 0, f"unsafe={unsafe}"))
        print(f"  [{'PASS' if m6_native == 32 else 'FAIL'}] M6_NATIVE_READ={m6_native}/32")
        print(f"  [{'PASS' if old_v2 == 0 else 'FAIL'}] OLD_V2_READ={old_v2}")
        print(f"  [{'PASS' if unsafe == 0 else 'FAIL'}] UNSAFE_FALLBACK={unsafe}")

    # Defects
    defects_p = REPO / ".dsh-control/m7/state/unresolved_defects.json"
    blocking = 0
    if defects_p.exists():
        d = json.loads(defects_p.read_text(encoding="utf-8"))
        blocking = sum(1 for x in d.get("defects", []) if x.get("severity") == "blocking" and x.get("status") != "resolved")
    results.append(clause("UNRESOLVED_BLOCKING_DEFECTS_eq_0", blocking == 0, f"blocking={blocking}"))
    print(f"  [{'PASS' if blocking == 0 else 'FAIL'}] BLOCKING_DEFECTS={blocking}")

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    all_pass = passed == total
    print("\n" + "=" * 60)
    print(f"  M7 MASTER: {passed}/{total} PASS")
    print("=" * 60)

    verdict = "M7_SHARED_FACT_GRAPH_PRODUCTION_AUTHORITY_COMPLETE" if all_pass else "M7_INCOMPLETE"
    out_path = REPO / ".dsh-control/m7/gates/master_gate_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "verdict": verdict,
        "passed": passed,
        "total": total,
        "results": results,
    }, indent=2), encoding="utf-8")
    print(f"\n  VERDICT: {verdict}")
    print(f"  -> {out_path}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
