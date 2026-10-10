"""M7.1 DUAL READ Hard Gate.

M7.1 runs M6 alongside v2 through the real production MCP path (dual read),
proving equivalence. v2 remains authoritative; M6 is the comparator.

Clauses:
  [D1]  dual-read harness exists and runs through real MCP path
  [D2]  all 7 representative queries tested on BASE build + cross-build
  [D3]  PARITY_FAILURES == 0
  [D4]  FALSE_SHARED_SEMANTIC_FACTS == 0
  [D5]  fresh Query Parity Critic review
  [D6]  fresh Verifier review
  [D7]  no M6 production authority switched (M6 still shadow comparator)
  [D8]  M5 regression intact

Usage:
    python .dsh-control/m7/gates/run_m7_1_dual_read_gate.py
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV = {**__import__("os").environ, "PYTHONPATH": "src;.dsh-control/corpora/harness;.dsh-control/m6_research", "PYTHONIOENCODING": "utf-8"}


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=ENV, cwd=str(REPO))
    out = (r.stdout or b"").decode("utf-8", errors="replace") + (r.stderr or b"").decode("utf-8", errors="replace")
    return r.returncode, out


def clause(name: str, passed: bool, detail: str = "") -> dict:
    return {"clause": name, "passed": passed, "detail": detail}


def main() -> int:
    results = []
    print("=" * 60)
    print("  M7.1 DUAL READ HARD GATE")
    print("=" * 60)

    # D1+D2+D3+D4: Run the dual-read harness
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/gates/dual_read_harness.py")], timeout=600)
    harness_result_path = REPO / ".dsh-control/m7/gates/dual_read_harness_result.json"
    harness = None
    if harness_result_path.exists():
        harness = json.loads(harness_result_path.read_text(encoding="utf-8"))

    d1 = rc == 0 and harness is not None and harness.get("verdict") == "M7_1_DUAL_READ_PASS"
    results.append(clause("D1_dual_read_harness_runs", d1, f"rc={rc} verdict={harness.get('verdict', 'MISSING') if harness else 'MISSING'}"))
    print(f"  [{'PASS' if d1 else 'FAIL'}] D1 dual-read harness runs through real MCP path")

    d2 = harness is not None and harness.get("total_queries", 0) >= 13
    results.append(clause("D2_all_representative_queries_tested", d2, f"total_queries={harness.get('total_queries', 0) if harness else 0}"))
    print(f"  [{'PASS' if d2 else 'FAIL'}] D2 all 7 representative queries + cross-build tested ({harness.get('total_queries', 0) if harness else 0} queries)")

    d3 = harness is not None and harness.get("parity_failures", -1) == 0
    results.append(clause("D3_PARITY_FAILURES_eq_0", d3, f"parity_failures={harness.get('parity_failures', -1) if harness else 'MISSING'}"))
    print(f"  [{'PASS' if d3 else 'FAIL'}] D3 PARITY_FAILURES=0 ({harness.get('parity_failures', -1) if harness else 'MISSING'})")

    d4 = harness is not None and harness.get("false_shared_semantic_facts", -1) == 0
    results.append(clause("D4_FALSE_SHARED_SEMANTIC_FACTS_eq_0", d4, f"false_shared={harness.get('false_shared_semantic_facts', -1) if harness else 'MISSING'}"))
    print(f"  [{'PASS' if d4 else 'FAIL'}] D4 FALSE_SHARED_SEMANTIC_FACTS=0")

    # D5: fresh Query Parity Critic review (pending — spawned separately)
    d5 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_1_query_parity_critic*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") == "PASS" and d.get("head", "").startswith("143642b"):
                d5 = True
                break
        except Exception:
            pass
    results.append(clause("D5_fresh_query_parity_critic", d5, "PASS artifact at 143642b" if d5 else "PENDING"))
    print(f"  [{'PASS' if d5 else 'FAIL'}] D5 fresh Query Parity Critic review")

    # D6: fresh Verifier review (pending — spawned separately)
    d6 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_1_verifier*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") == "PASS" and d.get("head", "").startswith("143642b"):
                d6 = True
                break
        except Exception:
            pass
    results.append(clause("D6_fresh_verifier", d6, "PASS artifact at 143642b" if d6 else "PENDING"))
    print(f"  [{'PASS' if d6 else 'FAIL'}] D6 fresh Verifier review")

    # D7: M6 still shadow-only (no production authority switched)
    pa_state_p = REPO / ".dsh-control/m7/state/production_authority_state.json"
    d7 = False
    if pa_state_p.exists():
        pa = json.loads(pa_state_p.read_text(encoding="utf-8"))
        d7 = pa["authority"]["m6_native_read_tools"] == 0
    results.append(clause("D7_M6_still_shadow_comparator", d7, f"m6_native_read={pa.get('authority', {}).get('m6_native_read_tools', '?') if pa_state_p.exists() else 'MISSING'}"))
    print(f"  [{'PASS' if d7 else 'FAIL'}] D7 M6 still shadow comparator (no authority switched)")

    # D8: M5 regression intact
    rc, out = run([sys.executable, str(REPO / ".dsh-control/gates/scan_authority_state.py")], timeout=120)
    d8 = rc == 0 and "M3B numeric gate: PASS" in out
    rc2, out2 = run([sys.executable, str(REPO / ".dsh-control/gates/scan_write_authority.py")], timeout=120)
    d8 = d8 and rc2 == 0 and "machine_gate=PASS" in out2
    results.append(clause("D8_M5_regression_intact", d8, f"M3B={rc==0} M4={rc2==0}"))
    print(f"  [{'PASS' if d8 else 'FAIL'}] D8 M5 regression intact (M3B+M4)")

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    all_pass = passed == total
    print("\n" + "=" * 60)
    print(f"  M7.1 DUAL READ: {passed}/{total} PASS")
    print("=" * 60)

    verdict = "M7_1_DUAL_READ_PASS" if all_pass else "M7_1_DUAL_READ_INCOMPLETE"
    out_path = REPO / ".dsh-control/m7/gates/m7_1_dual_read_gate_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "verdict": verdict,
        "passed": passed,
        "total": total,
        "results": results,
        "harness_summary": {
            "total_m6_facts": harness.get("total_m6_facts", 0) if harness else 0,
            "total_queries": harness.get("total_queries", 0) if harness else 0,
            "parity_failures": harness.get("parity_failures", 0) if harness else 0,
            "false_shared": harness.get("false_shared_semantic_facts", 0) if harness else 0,
        } if harness else None,
    }, indent=2), encoding="utf-8")
    print(f"\n  VERDICT: {verdict}")
    print(f"  -> {out_path}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
