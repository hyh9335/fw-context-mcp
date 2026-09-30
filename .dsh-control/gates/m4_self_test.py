#!/usr/bin/env python3
"""Hard-gate self-tests (TEST A-F) for the fw-context M4 controller.

Proves the controller cannot fool itself. Each test injects a SYNTHETIC
write-authority state into a temp evidence dir and asserts the gate's
response. Anti-gaming property holds regardless of real code state.

  TEST A  status forgery: PROJECT_STATUS says M4 complete, code is legacy-primary
          -> scanner derives code truth -> gate FAIL.
  TEST B  dual-write false completion: both legacy and v2 receive data, but
          legacy success still determines index success -> gate FAIL.
  TEST C  legacy mirror failure: legacy write fails after valid v2; if
          indexing would fail -> gate FAIL (synthetic state where legacy
          postprocess is still required).
  TEST D  v2 failure masked: v2 finalization fails, legacy succeeds, indexing
          reports success -> gate FAIL (synthetic state where v2 failure is
          swallowed).
  TEST E  stale verifier/guardian: acceptance artifacts from an OLDER HEAD
          -> gate FAIL.
  TEST F  incomplete World visibility: v2 finalize is swallowed so an
          incomplete World is queryable -> gate FAIL (synthetic state).

Run:  python .dsh-control/gates/m4_self_test.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".dsh-control" / "gates"))

# Import the M4 gate module by path.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("m4_gate", ROOT / ".dsh-control" / "gates" / "run_m4_stage_gate.py")
gate = _ilu.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(gate)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _fresh_evidence_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="m4_selftest_"))


def _head() -> str:
    rc, out = gate._git(["rev-parse", "HEAD"], ROOT)
    return out


def _expect(test_id: str, condition: bool, detail: str) -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {test_id}: {detail}")
    return condition


def _write_state(ev: Path, state: dict) -> None:
    (ev / "write_authority_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8")


def _write_artifact(ev: Path, role: str, verdict: str, head: str) -> None:
    name = f"m4_{role}_{head[:7]}.json"
    art = {
        role: "fresh_one_shot",
        "head": head,
        "timestamp": _now(),
        "verdict": verdict,
    }
    (ev / name).write_text(json.dumps(art, indent=2), encoding="utf-8")


def _green_state(head: str) -> dict:
    """A synthetic fully-GREEN M4 state (all counters pass)."""
    return {
        "schema_version": 1,
        "milestone": "M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE",
        "generated_at_utc": _now(),
        "project_root": str(ROOT),
        "git": {"head": head, "short": head[:7], "branch": "m3b-native-v2", "dirty": False},
        "counters": {
            "NORMAL_INDEX_ENTRYPOINTS": 4,
            "V2_PRIMARY_INDEX_ENTRYPOINTS": 4,
            "LEGACY_PRIMARY_INDEX_ENTRYPOINTS": 0,
            "LEGACY_WRITE_REQUIRED_FOR_SUCCESS": 0,
            "LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS": 0,
            "LEGACY_DB_REQUIRED_FOR_V2_INDEX": 0,
            "V2_FINALIZATION_IS_SUCCESS_AUTHORITY": True,
            "INCOMPLETE_WORLD_EXPOSURE_PATHS": 0,
            "UNSAFE_PARTIAL_FINALIZATION_PATHS": 0,
            "EXPECTED_TU_ACCOUNTING_VIOLATIONS": 0,
            "V2_FAILURE_MASKED_BY_LEGACY_PATHS": 0,
            "LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS": 0,
            "CURRENT_WRITE_MODE": "V2_PRIMARY_LEGACY_MIRROR",
            "DEFAULT_WRITE_MODE": "LEGACY_PRIMARY",
            "ROLLBACK_MODE_AVAILABLE": True,
            "M3B_READ_AUTHORITY_REGRESSION": "PASS",
            "UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS": 0,
        },
        "fault_tests": {t: {"verdict": "PASS"} for t in [
            "V2_SUCCESS_LEGACY_FAILURE_TEST", "V2_FAILURE_LEGACY_SUCCESS_TEST",
            "CRASH_BEFORE_FINALIZE_TEST", "RESTART_IDEMPOTENCE_TEST",
            "RETRY_IDEMPOTENCE_TEST", "READ_AFTER_FINALIZE_TEST", "ROLLBACK_TEST",
        ]},
        "_synthetic_selftest_fixture": True,
    }


def _red_state(head: str) -> dict:
    """A synthetic RED baseline (legacy-primary, v2 not authoritative)."""
    base = _green_state(head)
    base["counters"].update({
        "V2_PRIMARY_INDEX_ENTRYPOINTS": 0,
        "LEGACY_PRIMARY_INDEX_ENTRYPOINTS": 4,
        "LEGACY_WRITE_REQUIRED_FOR_SUCCESS": 3,
        "LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS": 1,
        "LEGACY_DB_REQUIRED_FOR_V2_INDEX": 3,
        "V2_FINALIZATION_IS_SUCCESS_AUTHORITY": False,
        "INCOMPLETE_WORLD_EXPOSURE_PATHS": 1,
        "V2_FAILURE_MASKED_BY_LEGACY_PATHS": 1,
        "LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS": 1,
        "CURRENT_WRITE_MODE": "LEGACY_PRIMARY",
    })
    base["fault_tests"] = {t: {"verdict": "NOT_RUN"} for t in [
        "V2_SUCCESS_LEGACY_FAILURE_TEST", "V2_FAILURE_LEGACY_SUCCESS_TEST",
        "CRASH_BEFORE_FINALIZE_TEST", "RESTART_IDEMPOTENCE_TEST",
        "RETRY_IDEMPOTENCE_TEST", "READ_AFTER_FINALIZE_TEST", "ROLLBACK_TEST",
    ]}
    return base


def test_a() -> bool:
    print("TEST A: status forgery — PROJECT_STATUS says M4 complete, code is legacy-primary")
    fake = ROOT / "PROJECT_STATUS_M4_FAKE_SELFTEST.md"
    fake.write_text("# FAKE\n\nM4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE\nAll done.\n",
                    encoding="utf-8")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # Inject RED state (legacy-primary). The fake status must NOT override it.
        _write_state(ev, _red_state(head))
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        mode = r["counters"].get("CURRENT_WRITE_MODE")
        ok = (not r["passed"]) and mode == "LEGACY_PRIMARY"
        return _expect("A", ok,
                       f"mode={mode} (ignored fake status) gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        fake.unlink(missing_ok=True)
        shutil.rmtree(ev, ignore_errors=True)


def test_b() -> bool:
    print("TEST B: dual-write false completion — both write, legacy determines success")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # v2 receives writes (finalize sites exist) BUT legacy success still
        # determines the run (legacy postprocess required, v2 failure masked).
        state = _red_state(head)
        # Pretend v2 finalize exists but is swallowed + legacy gates success.
        state["counters"]["V2_FAILURE_MASKED_BY_LEGACY_PATHS"] = 1
        state["counters"]["LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS"] = 1
        _write_state(ev, state)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        c6 = next(c for c in r["clauses"] if c["id"] == "C6")
        c12 = next(c for c in r["clauses"] if c["id"] == "C12")
        ok = (not r["passed"]) and (not c6["passed"]) and (not c12["passed"])
        return _expect("B", ok,
                       f"legacy_postprocess_required={c6['detail']} "
                       f"v2_failure_masked={c12['detail']} -> gate FAIL")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_c() -> bool:
    print("TEST C: legacy mirror failure blocks v2 success")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # Green except legacy mirror failure blocks v2 success path.
        state = _green_state(head)
        state["counters"]["LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS"] = 1
        _write_state(ev, state)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        c13 = next(c for c in r["clauses"] if c["id"] == "C13")
        ok = (not r["passed"]) and (not c13["passed"])
        return _expect("C", ok,
                       f"legacy_mirror_blocks={c13['detail']} -> gate FAIL")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_d() -> bool:
    print("TEST D: v2 failure masked by legacy")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # Green except v2 failure is masked by legacy.
        state = _green_state(head)
        state["counters"]["V2_FAILURE_MASKED_BY_LEGACY_PATHS"] = 1
        state["counters"]["V2_FINALIZATION_IS_SUCCESS_AUTHORITY"] = False
        _write_state(ev, state)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        c8 = next(c for c in r["clauses"] if c["id"] == "C8")
        c12 = next(c for c in r["clauses"] if c["id"] == "C12")
        ok = (not r["passed"]) and (not c8["passed"]) and (not c12["passed"])
        return _expect("D", ok,
                       f"v2_authority={c8['detail']} masked={c12['detail']} -> gate FAIL")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_e() -> bool:
    print("TEST E: stale verifier/guardian artifacts (older HEAD)")
    ev = _fresh_evidence_dir()
    head = _head()
    stale_head = "0" * 40  # a fake older HEAD
    try:
        # Green state but acceptance artifacts name a DIFFERENT (older) head.
        _write_state(ev, _green_state(head))
        _write_artifact(ev, "verifier", "PASS", stale_head)
        _write_artifact(ev, "guardian", "ACCEPT_COMPLETION", stale_head)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        c25 = next(c for c in r["clauses"] if c["id"] == "C25")
        c26 = next(c for c in r["clauses"] if c["id"] == "C26")
        ok = (not r["passed"]) and (not c25["passed"]) and (not c26["passed"])
        return _expect("E", ok,
                       f"verifier_ok={c25['passed']} guardian_ok={c26['passed']} (stale) -> FAIL")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_f() -> bool:
    print("TEST F: incomplete World visibility (v2 finalize swallowed)")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        # Green except an incomplete World can be exposed (v2 finalize swallowed).
        state = _green_state(head)
        state["counters"]["INCOMPLETE_WORLD_EXPOSURE_PATHS"] = 1
        _write_state(ev, state)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        c9 = next(c for c in r["clauses"] if c["id"] == "C9")
        ok = (not r["passed"]) and (not c9["passed"])
        return _expect("F", ok,
                       f"incomplete_exposure={c9['detail']} -> gate FAIL")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def test_pass() -> bool:
    print("TEST PASS: fully-green synthetic state -> gate PASS")
    ev = _fresh_evidence_dir()
    head = _head()
    try:
        _write_state(ev, _green_state(head))
        _write_artifact(ev, "verifier", "PASS", head)
        _write_artifact(ev, "guardian", "ACCEPT_COMPLETION", head)
        r = gate.evaluate_gate(ROOT, ev, regen=False, run_regression_flag=False,
                               regression_result={"pass": True, "head": head})
        failed = [c["id"] for c in r["clauses"] if not c["passed"]]
        ok = r["passed"] and not failed
        return _expect("PASS", ok,
                       f"failed={failed} -> gate={'PASS' if r['passed'] else 'FAIL'}")
    finally:
        shutil.rmtree(ev, ignore_errors=True)


def main() -> int:
    tests = [test_a, test_b, test_c, test_d, test_e, test_f, test_pass]
    results = [t() for t in tests]
    n_pass = sum(results)
    print(f"\n=== m4-self-tests: {n_pass}/{len(results)} passed ===")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
