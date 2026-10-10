"""M7.5 V2_RETIREMENT Hard Gate.

M7.5 retires v2 from the normal production path. All v2 semantic reads,
required writes, fallbacks, and finalization dependencies must be 0 in
normal (default) operation mode.

Clauses:
  [V1]  NORMAL_OLD_V2_SEMANTIC_READS == 0
  [V2]  NORMAL_OLD_V2_REQUIRED_WRITES == 0
  [V3]  NORMAL_OLD_V2_FALLBACKS == 0
  [V4]  NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES == 0
  [V5]  scan_production_authority: M6_NATIVE_READ == 32, OLD_V2 == 0
  [V6]  scan_fallbacks: unsafe_silent == 0
  [V7]  fresh Verifier review
  [V8]  fresh Guardian G6 review
  [V9]  M5 regression intact (M3B+M4)
  [V10] M7.1+M7.2+M7.3+M7.4 regression intact

Note: M7.5 is SEMANTIC/OPERATIONAL retirement, NOT physical deletion.
An explicit, isolated, default-OFF compatibility island may remain (§41).
Physical deletion of every legacy module is a FUTURE optional task.

Usage:
    python .dsh-control/m7/gates/run_m7_5_retirement_gate.py
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV = {**__import__("os").environ, "PYTHONPATH": "src;.dsh-control/corpora/harness;.dsh-control/m6_research", "PYTHONIOENCODING": "utf-8"}


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=ENV, cwd=str(REPO))
    out = (r.stdout or b"").decode("utf-8", errors="replace") + (r.stderr or b"").decode("utf-8", errors="replace")
    return r.returncode, out


def clause(name: str, passed: bool, detail: str = "") -> dict:
    return {"clause": name, "passed": passed, "detail": detail}


def main() -> int:
    results = []
    print("=" * 60)
    print("  M7.5 V2_RETIREMENT HARD GATE")
    print("=" * 60)

    # Run normal_path_dependencies scanner
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_normal_path_dependencies.py")], timeout=120)
    npd_p = REPO / ".dsh-control/m7/state/normal_path_dependencies_state.json"
    npd = {}
    if npd_p.exists():
        npd = json.loads(npd_p.read_text(encoding="utf-8"))

    # V1: NORMAL_OLD_V2_SEMANTIC_READS == 0
    v2_reads = npd.get("normal_old_v2_semantic_reads", -1)
    r1 = v2_reads == 0
    results.append(clause("V1_NORMAL_OLD_V2_SEMANTIC_READS_eq_0", r1, f"v2_reads={v2_reads}"))
    print(f"  [{'PASS' if r1 else 'FAIL'}] V1 NORMAL_OLD_V2_SEMANTIC_READS={v2_reads} (target=0)")

    # V2: NORMAL_OLD_V2_REQUIRED_WRITES == 0
    v2_writes = npd.get("normal_old_v2_required_writes", -1)
    r2 = v2_writes == 0
    results.append(clause("V2_NORMAL_OLD_V2_REQUIRED_WRITES_eq_0", r2, f"v2_writes={v2_writes}"))
    print(f"  [{'PASS' if r2 else 'FAIL'}] V2 NORMAL_OLD_V2_REQUIRED_WRITES={v2_writes} (target=0)")

    # V3: NORMAL_OLD_V2_FALLBACKS == 0
    v2_fallbacks = npd.get("normal_old_v2_fallbacks", -1)
    r3 = v2_fallbacks == 0
    results.append(clause("V3_NORMAL_OLD_V2_FALLBACKS_eq_0", r3, f"v2_fallbacks={v2_fallbacks}"))
    print(f"  [{'PASS' if r3 else 'FAIL'}] V3 NORMAL_OLD_V2_FALLBACKS={v2_fallbacks} (target=0)")

    # V4: NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES == 0
    v2_finalize = npd.get("normal_old_v2_finalization_dependencies", -1)
    r4 = v2_finalize == 0
    results.append(clause("V4_NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES_eq_0", r4, f"v2_finalize={v2_finalize}"))
    print(f"  [{'PASS' if r4 else 'FAIL'}] V4 NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES={v2_finalize} (target=0)")

    # V5: scan_production_authority
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_production_authority.py")], timeout=120)
    pa_p = REPO / ".dsh-control/m7/state/production_authority_state.json"
    pa = {}
    if pa_p.exists():
        pa = json.loads(pa_p.read_text(encoding="utf-8"))
    auth = pa.get("authority", {})
    m6_native = auth.get("m6_native_read_tools", -1)
    old_v2 = auth.get("old_v2_read_authority", -1)
    r5 = m6_native == 32 and old_v2 == 0
    results.append(clause("V5_production_authority", r5, f"m6_native={m6_native} old_v2={old_v2}"))
    print(f"  [{'PASS' if r5 else 'FAIL'}] V5 production_authority m6={m6_native} old_v2={old_v2}")

    # V6: scan_fallbacks
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_fallbacks.py")], timeout=120)
    fb_p = REPO / ".dsh-control/m7/state/fallbacks_state.json"
    fb = {}
    if fb_p.exists():
        fb = json.loads(fb_p.read_text(encoding="utf-8"))
    unsafe = fb.get("unsafe_or_silent_fallback", -1)
    r6 = unsafe == 0
    results.append(clause("V6_fallbacks_unsafe_silent_eq_0", r6, f"unsafe_silent={unsafe}"))
    print(f"  [{'PASS' if r6 else 'FAIL'}] V6 fallbacks unsafe_silent={unsafe} (target=0)")

    # V7: fresh Verifier review
    r7 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_5_verifier*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") == "PASS" and d.get("head", "").startswith("143642b"):
                r7 = True
                break
        except Exception:
            pass
    results.append(clause("V7_fresh_verifier", r7, "PASS artifact at 143642b" if r7 else "PENDING"))
    print(f"  [{'PASS' if r7 else 'FAIL'}] V7 fresh Verifier review")

    # V8: fresh Guardian G6 review
    r8 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_5_guardian*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") in ("ACCEPT", "ACCEPT_COMPLETION") and d.get("head", "").startswith("143642b"):
                r8 = True
                break
        except Exception:
            pass
    results.append(clause("V8_fresh_guardian_g6", r8, "ACCEPT artifact at 143642b" if r8 else "PENDING"))
    print(f"  [{'PASS' if r8 else 'FAIL'}] V8 fresh Guardian G6 review")

    # V9: M5 regression intact
    rc, out = run([sys.executable, str(REPO / ".dsh-control/gates/scan_authority_state.py")], timeout=120)
    r9 = rc == 0 and "M3B numeric gate: PASS" in out
    rc2, out2 = run([sys.executable, str(REPO / ".dsh-control/gates/scan_write_authority.py")], timeout=120)
    r9 = r9 and rc2 == 0 and "machine_gate=PASS" in out2
    results.append(clause("V9_M5_regression_intact", r9, f"M3B={rc==0} M4={rc2==0}"))
    print(f"  [{'PASS' if r9 else 'FAIL'}] V9 M5 regression intact (M3B+M4)")

    # V10: M7.1+M7.2+M7.3+M7.4 regression intact
    gates = {
        "m7_1": REPO / ".dsh-control/m7/gates/m7_1_dual_read_gate_result.json",
        "m7_2": REPO / ".dsh-control/m7/gates/m7_2_read_authority_gate_result.json",
        "m7_3": REPO / ".dsh-control/m7/gates/m7_3_write_authority_gate_result.json",
        "m7_4": REPO / ".dsh-control/m7/gates/m7_4_lifecycle_gate_result.json",
    }
    r10 = True
    for name, path in gates.items():
        if not path.exists():
            r10 = False
            break
        d = json.loads(path.read_text(encoding="utf-8"))
        if "PASS" not in d.get("verdict", ""):
            r10 = False
            break
    results.append(clause("V10_M7_1_to_M7_4_regression", r10, "M7.1-M7.4 all PASS" if r10 else "PENDING"))
    print(f"  [{'PASS' if r10 else 'FAIL'}] V10 M7.1+M7.2+M7.3+M7.4 regression intact")

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    all_pass = passed == total
    print("\n" + "=" * 60)
    print(f"  M7.5 V2_RETIREMENT: {passed}/{total} PASS")
    print("=" * 60)

    verdict = "M7_5_V2_RETIREMENT_PASS" if all_pass else "M7_5_V2_RETIREMENT_INCOMPLETE"
    out_path = REPO / ".dsh-control/m7/gates/m7_5_retirement_gate_result.json"
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
