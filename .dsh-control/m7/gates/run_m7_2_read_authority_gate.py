"""M7.2 READ_AUTHORITY Hard Gate.

M7.2 makes M6 the production read authority. All core semantic tools
must route their normal read path through M6 query implementations.

Clauses:
  [R1]  CORE_NATIVE_M6_READ == 32 (all 32 semantic tools M6-native)
  [R2]  OLD_V2_READ_AUTHORITY == 0 (no tool uses old v2 as normal read path)
  [R3]  UNSAFE_OR_SILENT_FALLBACK == 0 (no silent fallbacks in normal mode)
  [R4]  scan_production_authority confirms authority switch
  [R5]  scan_normal_path_dependencies confirms v2 reads == 0
  [R6]  scan_fallbacks confirms 0 unsafe_silent
  [R7]  M6 shadow store query interface intact (BuildSetStore methods)
  [R8]  fresh Verifier review
  [R9]  fresh Guardian G6 review
  [R10] M5 regression intact (M3B+M4 scanners PASS)

Anti-false-migration rule (§8): a tool counts as M6-native ONLY if
production request -> M6 query implementation -> M6 result. Does NOT
count: router->unsupported->old v2; M6 validation->old v2;
M6 label->legacy impl.

Usage:
    python .dsh-control/m7/gates/run_m7_2_read_authority_gate.py
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
    print("  M7.2 READ AUTHORITY HARD GATE")
    print("=" * 60)

    # Run production authority scanner
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_production_authority.py")], timeout=120)
    pa_state_p = REPO / ".dsh-control/m7/state/production_authority_state.json"
    pa = {}
    if pa_state_p.exists():
        pa = json.loads(pa_state_p.read_text(encoding="utf-8"))
    auth = pa.get("authority", {})

    # R1: CORE_NATIVE_M6_READ == 32
    m6_native = auth.get("m6_native_read_tools", -1)
    r1 = m6_native == 32
    results.append(clause("R1_CORE_NATIVE_M6_READ_eq_32", r1, f"m6_native_read={m6_native}"))
    print(f"  [{'PASS' if r1 else 'FAIL'}] R1 CORE_NATIVE_M6_READ={m6_native} (target=32)")

    # R2: OLD_V2_READ_AUTHORITY == 0
    old_v2 = auth.get("old_v2_read_authority", -1)
    r2 = old_v2 == 0
    results.append(clause("R2_OLD_V2_READ_AUTHORITY_eq_0", r2, f"old_v2_read={old_v2}"))
    print(f"  [{'PASS' if r2 else 'FAIL'}] R2 OLD_V2_READ_AUTHORITY={old_v2} (target=0)")

    # R3: UNSAFE_OR_SILENT_FALLBACK == 0
    unsafe = auth.get("unsafe_or_silent_fallback", -1)
    r3 = unsafe == 0
    results.append(clause("R3_UNSAFE_OR_SILENT_FALLBACK_eq_0", r3, f"unsafe_silent={unsafe}"))
    print(f"  [{'PASS' if r3 else 'FAIL'}] R3 UNSAFE_OR_SILENT_FALLBACK={unsafe} (target=0)")

    # R4: scanner confirms authority switch
    r4 = rc == 0 and auth.get("m6_native_read_tools", 0) == 32
    results.append(clause("R4_scanner_confirms_authority_switch", r4, f"rc={rc} m6_native={auth.get('m6_native_read_tools', 'MISSING')}"))
    print(f"  [{'PASS' if r4 else 'FAIL'}] R4 scan_production_authority confirms authority switch")

    # R5: scan_normal_path_dependencies: v2 reads == 0
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_normal_path_dependencies.py")], timeout=120)
    npd_p = REPO / ".dsh-control/m7/state/normal_path_dependencies_state.json"
    npd = {}
    if npd_p.exists():
        npd = json.loads(npd_p.read_text(encoding="utf-8"))
    v2_reads = npd.get("normal_old_v2_semantic_reads", -1)
    r5 = v2_reads == 0
    results.append(clause("R5_normal_v2_reads_eq_0", r5, f"normal_old_v2_semantic_reads={v2_reads}"))
    print(f"  [{'PASS' if r5 else 'FAIL'}] R5 normal_path v2_reads={v2_reads} (target=0)")

    # R6: scan_fallbacks: unsafe_silent == 0
    rc, out = run([sys.executable, str(REPO / ".dsh-control/m7/scanners/scan_fallbacks.py")], timeout=120)
    fb_p = REPO / ".dsh-control/m7/state/fallbacks_state.json"
    fb = {}
    if fb_p.exists():
        fb = json.loads(fb_p.read_text(encoding="utf-8"))
    unsafe_silent = fb.get("unsafe_or_silent_fallback", -1)
    r6 = unsafe_silent == 0
    results.append(clause("R6_fallbacks_unsafe_silent_eq_0", r6, f"unsafe_or_silent_fallback={unsafe_silent}"))
    print(f"  [{'PASS' if r6 else 'FAIL'}] R6 fallbacks unsafe_silent={unsafe_silent} (target=0)")

    # R7: M6 store query interface intact
    r7 = False
    try:
        from fw_context_mcp.store_v2.m6_store import BuildSetStore
        methods = [m for m in dir(BuildSetStore) if not m.startswith("_")]
        required = ["project_build", "project_build_kind", "register_world", "add_fact", "stats"]
        r7 = all(m in methods for m in required)
    except Exception:
        pass
    results.append(clause("R7_m6_store_interface_intact", r7, "BuildSetStore methods present"))
    print(f"  [{'PASS' if r7 else 'FAIL'}] R7 M6 store query interface intact")

    # R8: fresh Verifier review
    r8 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_2_verifier*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") == "PASS" and d.get("head", "").startswith("143642b"):
                r8 = True
                break
        except Exception:
            pass
    results.append(clause("R8_fresh_verifier", r8, "PASS artifact at 143642b" if r8 else "PENDING"))
    print(f"  [{'PASS' if r8 else 'FAIL'}] R8 fresh Verifier review")

    # R9: fresh Guardian G6 review
    r9 = False
    for p in (REPO / ".dsh-control/evidence").glob("m7_2_guardian*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("verdict") in ("ACCEPT", "ACCEPT_COMPLETION") and d.get("head", "").startswith("143642b"):
                r9 = True
                break
        except Exception:
            pass
    results.append(clause("R9_fresh_guardian_g6", r9, "ACCEPT artifact at 143642b" if r9 else "PENDING"))
    print(f"  [{'PASS' if r9 else 'FAIL'}] R9 fresh Guardian G6 review")

    # R10: M5 regression intact
    rc, out = run([sys.executable, str(REPO / ".dsh-control/gates/scan_authority_state.py")], timeout=120)
    r10 = rc == 0 and "M3B numeric gate: PASS" in out
    rc2, out2 = run([sys.executable, str(REPO / ".dsh-control/gates/scan_write_authority.py")], timeout=120)
    r10 = r10 and rc2 == 0 and "machine_gate=PASS" in out2
    results.append(clause("R10_M5_regression_intact", r10, f"M3B={rc==0} M4={rc2==0}"))
    print(f"  [{'PASS' if r10 else 'FAIL'}] R10 M5 regression intact (M3B+M4)")

    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    all_pass = passed == total
    print("\n" + "=" * 60)
    print(f"  M7.2 READ AUTHORITY: {passed}/{total} PASS")
    print("=" * 60)

    verdict = "M7_2_READ_AUTHORITY_PASS" if all_pass else "M7_2_READ_AUTHORITY_INCOMPLETE"
    out_path = REPO / ".dsh-control/m7/gates/m7_2_read_authority_gate_result.json"
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
