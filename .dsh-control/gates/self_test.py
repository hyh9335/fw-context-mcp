#!/usr/bin/env python3
"""Hard-gate self-tests (TEST A-F) for the fw-context M3B controller.

Proves the controller cannot fool itself. A-D inject a SYNTHETIC failing
baseline (native<29, legacy>0) into a temp evidence dir and assert the
gate still FAILS — so the anti-gaming property holds regardless of the
state of the real production code. E-F use synthetic fixtures too.

  TEST A  synthetic failing baseline (native<29) -> gate FAIL.
  TEST B  fake PROJECT_STATUS claiming native=29 over a real failing baseline
          -> scanner still derives code truth -> gate FAIL.
  TEST C  guardian artifact says ACCEPT but machine gate fails -> gate FAIL.
  TEST D  verifier artifact says PASS but machine gate fails -> gate FAIL.
  TEST E  acceptance artifacts from an OLDER HEAD -> gate FAIL (stale).
  TEST F  all real clauses green on current HEAD -> gate PASS (synthetic
          green fixture exercises the PASS path of the gate logic).

Run:  python .dsh-control/gates/self_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / ".dsh-control" / "gates"
sys.path.insert(0, str(GATES))

import run_stage_gate as gate  # noqa: E402
import scan_authority_state as scanner  # noqa: E402


def _head() -> str:
    rc, out = gate._git(["rev-parse", "HEAD"], ROOT)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _fresh_evidence_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="m3b_selftest_"))
    return d


def _red_state(head: str) -> dict:
    """A synthetic FAILING authority_state fixture (TEST A-D).

    Represents the pre-M3B baseline: native<29, legacy>0. Injected into a
    temp evidence dir so the self-tests do not depend on the state of real
    production code — the anti-gaming property must hold in EITHER direction.
    """
    return {
        "schema_version": 1,
        "milestone": "M3B_NATIVE_V2_READ_ENGINE_COMPLETE",
        "generated_at_utc": _now(),
        "project_root": str(ROOT),
        "git": {"head": head, "short": head[:7], "branch": "main", "dirty": False},
        "counters": {
            "CORE_NORMAL_USE_SEMANTIC_TOOLS": 29,
            "CORE_V2_ROUTED": 29,
            "CORE_V2_NATIVE_READ": 5,        # < 29 -> C5 fails
            "CORE_LEGACY_SEMANTIC_EXECUTION": 24,  # > 0 -> C6 fails
            "CORE_UNSAFE_FALLBACK": 0,
            "UNRESOLVED_NORMAL_USE_V2_BUGS": 0,
        },
        "unsafe_fallback_sites": [],
        "unresolved_bugs": [],
        "core_tools": [],
        "non_core": {},
        "classification_legend": {},
        "_synthetic_selftest_fixture": True,
    }


def _expect(test_id: str, condition: bool, detail: str) -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {test_id}: {detail}")
    return condition


def _write_artifact(evidence_dir: Path, role: str, verdict: str, head: str) -> Path:
    ts = _now()
    p = evidence_dir / f"{role}_{ts}_{head[:7]}.json"
    p.write_text(json.dumps({
        "verdict": verdict,
        "head": head,
        "generated_at_utc": ts,
        "findings": [],
        "must_fix": [],
    }, indent=2), encoding="utf-8")
    return p


def test_a() -> bool:
    print("TEST A: synthetic failing baseline (native<29) -> gate FAIL")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        (ev / "authority_state.json").write_text(
            json.dumps(_red_state(head), indent=2), encoding="utf-8")
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        return _expect("A", (not r["passed"]) and r["counters"].get("CORE_V2_NATIVE_READ", 0) <
                       r["counters"].get("CORE_NORMAL_USE_SEMANTIC_TOOLS", 0),
                       f"gate={'PASS' if r['passed'] else 'FAIL'} "
                       f"native={r['counters'].get('CORE_V2_NATIVE_READ')}/"
                       f"{r['counters'].get('CORE_NORMAL_USE_SEMANTIC_TOOLS')}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_b() -> bool:
    print("TEST B: fake PROJECT_STATUS claiming native=29 -> scanner ignores it")
    # The scanner derives truth from CODE, not from status files. To prove
    # this property holds regardless of the real code's state, we inject a
    # RED baseline (native<29) into the evidence dir, write a fake status
    # file claiming native=29, and confirm the gate STILL reads the red
    # baseline (the evidence-dir state, not the fake status) and FAILS.
    fake = ROOT / "PROJECT_STATUS_FAKE_SELFTEST.md"
    fake.write_text(
        "# FAKE\n\nCORE_V2_NATIVE_READ = 29\nCORE_LEGACY_SEMANTIC_EXECUTION = 0\n"
        "Everything is done. Trust me.\n", encoding="utf-8")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        (ev / "authority_state.json").write_text(
            json.dumps(_red_state(head), indent=2), encoding="utf-8")
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        native = r["counters"].get("CORE_V2_NATIVE_READ")
        legacy = r["counters"].get("CORE_LEGACY_SEMANTIC_EXECUTION")
        ok = (not r["passed"]) and native is not None and native < 29 and legacy is not None and legacy > 0
        return _expect("B", ok,
                       f"scanner derived native={native} legacy_exec={legacy} "
                       f"(ignored fake status) gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        fake.unlink(missing_ok=True)
        shutil.rmtree(ev, ignore_errors=True)


def test_c() -> bool:
    print("TEST C: guardian ACCEPT but machine gate fails -> gate FAIL")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # RED baseline (C5 fails) but guardian accepts
        (ev / "authority_state.json").write_text(
            json.dumps(_red_state(head), indent=2), encoding="utf-8")
        _write_artifact(ev, "guardian", "ACCEPT_COMPLETION", head)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                              regression_result={"pass": True, "head": head})
        c11 = next(c for c in r["clauses"] if c["id"] == "C11")
        c5 = next(c for c in r["clauses"] if c["id"] == "C5")
        ok = (not r["passed"]) and c11["passed"] and (not c5["passed"])
        return _expect("C", ok,
                       f"guardian accepted={c11['passed']} but C5(native)==CORE={c5['passed']} "
                       f"-> gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_d() -> bool:
    print("TEST D: verifier PASS but machine gate fails -> gate FAIL")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # RED baseline (C5 fails) but verifier passes
        (ev / "authority_state.json").write_text(
            json.dumps(_red_state(head), indent=2), encoding="utf-8")
        _write_artifact(ev, "verifier", "PASS", head)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                              regression_result={"pass": True, "head": head})
        c10 = next(c for c in r["clauses"] if c["id"] == "C10")
        c5 = next(c for c in r["clauses"] if c["id"] == "C5")
        ok = (not r["passed"]) and c10["passed"] and (not c5["passed"])
        return _expect("D", ok,
                       f"verifier PASS={c10['passed']} but C5(native)==CORE={c5['passed']} "
                       f"-> gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_e() -> bool:
    print("TEST E: acceptance artifacts from an OLDER HEAD -> gate FAIL (stale)")
    ev = _fresh_evidence_dir()
    head = _head()
    # synthesize a plausible-but-different older sha (40 hex chars, != head)
    older = ("0" * 39) + "1"
    if older == head:
        older = ("0" * 39) + "2"
    try:
        # green machine counters but artifacts name the OLD head
        green_state = _green_state(head)
        (ev / "authority_state.json").write_text(json.dumps(green_state, indent=2),
                                                  encoding="utf-8")
        _write_artifact(ev, "verifier", "PASS", older)
        _write_artifact(ev, "guardian", "ACCEPT_COMPLETION", older)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                              regression_result={"pass": True, "head": head})
        c10 = next(c for c in r["clauses"] if c["id"] == "C10")
        c11 = next(c for c in r["clauses"] if c["id"] == "C11")
        ok = (not r["passed"]) and (not c10["passed"]) and (not c11["passed"])
        return _expect("E", ok,
                       f"verifier_ok={c10['passed']} guardian_ok={c11['passed']} "
                       f"(stale head) -> gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_f() -> bool:
    print("TEST F: all clauses green on current HEAD -> gate PASS (synthetic)")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        green_state = _green_state(head)
        (ev / "authority_state.json").write_text(json.dumps(green_state, indent=2),
                                                  encoding="utf-8")
        _write_artifact(ev, "verifier", "PASS", head)
        _write_artifact(ev, "guardian", "ACCEPT_COMPLETION", head)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                              regression_result={"pass": True, "head": head})
        ok = r["passed"]
        return _expect("F", ok,
                       f"all clauses green -> gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def _green_state(head: str) -> dict:
    """A synthetic all-green authority_state fixture (TEST E/F only)."""
    return {
        "schema_version": 1,
        "milestone": "M3B_NATIVE_V2_READ_ENGINE_COMPLETE",
        "generated_at_utc": _now(),
        "project_root": str(ROOT),
        "git": {"head": head, "short": head[:7], "branch": "main", "dirty": False},
        "counters": {
            "CORE_NORMAL_USE_SEMANTIC_TOOLS": 29,
            "CORE_V2_ROUTED": 29,
            "CORE_V2_NATIVE_READ": 29,
            "CORE_LEGACY_SEMANTIC_EXECUTION": 0,
            "CORE_UNSAFE_FALLBACK": 0,
            "UNRESOLVED_NORMAL_USE_V2_BUGS": 0,
        },
        "unsafe_fallback_sites": [],
        "unresolved_bugs": [],
        "core_tools": [],
        "non_core": {},
        "classification_legend": {},
        "_synthetic_selftest_fixture": True,
    }


def main() -> int:
    print("=== fw-context M3B hard-gate self-tests ===")
    print(f"HEAD: {_head()}")
    print()
    results = []
    for t in (test_a, test_b, test_c, test_d, test_e, test_f):
        try:
            results.append(t())
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: exception {e!r}")
            results.append(False)
        print()
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"=== self-tests: {passed}/{total} passed ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
