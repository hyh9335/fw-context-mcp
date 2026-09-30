#!/usr/bin/env python3
"""fw-context M4 write-authority stage hard gate.

Evaluates the immutable M4 formula against CURRENT code + acceptance
artifacts. No warning-pass. Exits non-zero on ANY failed clause.

Clauses (all must pass):
  C1  project root is a git repo under the canonical M4 root marker
  C2  write_authority_state.json exists (regenerated unless --no-regen)
  C3  NORMAL_INDEX_ENTRYPOINTS > 0
  C4  V2_PRIMARY_INDEX_ENTRYPOINTS == NORMAL_INDEX_ENTRYPOINTS
  C5  LEGACY_WRITE_REQUIRED_FOR_SUCCESS == 0
  C6  LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS == 0
  C7  LEGACY_DB_REQUIRED_FOR_V2_INDEX == 0
  C8  V2_FINALIZATION_IS_SUCCESS_AUTHORITY == true
  C9  INCOMPLETE_WORLD_EXPOSURE_PATHS == 0
  C10 UNSAFE_PARTIAL_FINALIZATION_PATHS == 0
  C11 EXPECTED_TU_ACCOUNTING_VIOLATIONS == 0
  C12 V2_FAILURE_MASKED_BY_LEGACY_PATHS == 0
  C13 LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS == 0
  C14 UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS == 0
  C15 CURRENT_WRITE_MODE == V2_PRIMARY_LEGACY_MIRROR
  C16 fault: V2_SUCCESS_LEGACY_FAILURE_TEST == PASS
  C17 fault: V2_FAILURE_LEGACY_SUCCESS_TEST == PASS
  C18 fault: CRASH_BEFORE_FINALIZE_TEST == PASS
  C19 fault: RESTART_IDEMPOTENCE_TEST == PASS
  C20 fault: RETRY_IDEMPOTENCE_TEST == PASS
  C21 fault: READ_AFTER_FINALIZE_TEST == PASS
  C22 fault: ROLLBACK_TEST == PASS
  C23 M3B_READ_AUTHORITY_REGRESSION == PASS
  C24 regression PASS (M4 + M3B focused tests green)
  C25 latest m4_verifier artifact: verdict==PASS AND head==current HEAD
  C26 latest m4_guardian artifact: verdict==ACCEPT_COMPLETION AND head==current HEAD
  C27 durable checkpoint: current HEAD resolves

Usage:
    python .dsh-control/gates/run_m4_stage_gate.py [--project-root PATH]
        [--evidence-dir PATH] [--no-regen] [--regression-file PATH]
        [--no-regression]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CANONICAL_ROOT_MARKER = ".dsh-control/contracts/IMMUTABLE_M4_GOAL_CONTRACT.md"
FOCUSED_TESTS_K = "v2 or authority or shadow or adapter or queryview or world or write_authority or finalize or m4"


def _git(args: list[str], root: Path) -> tuple[int, str]:
    out = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return out.returncode, out.stdout.strip()


def _current_head(root: Path) -> str:
    rc, out = _git(["rev-parse", "HEAD"], root)
    return out if rc == 0 else ""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def _latest_artifact(evidence_dir: Path, prefix: str) -> tuple[Path | None, dict | None]:
    candidates = sorted(
        evidence_dir.glob(f"{prefix}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None, None
    p = candidates[0]
    return p, _load_json(p)


def run_regression(root: Path) -> dict:
    cmd = [sys.executable, "-m", "pytest", "tests/", "-x", "-q",
           "-k", FOCUSED_TESTS_K, "--no-header", "--tb=short"]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=600)
        passed = proc.returncode == 0
        return {
            "pass": passed,
            "command": " ".join(cmd),
            "exit_code": proc.returncode,
            "tail": (proc.stdout + proc.stderr)[-2000:],
            "head": _current_head(root),
        }
    except Exception as e:
        return {"pass": False, "command": " ".join(cmd), "error": str(e),
                "head": _current_head(root)}


def evaluate_gate(
    project_root: Path,
    evidence_dir: Path,
    *,
    regen: bool = True,
    run_regression_flag: bool = True,
    regression_result: dict | None = None,
) -> dict:
    root = project_root.resolve()
    head = _current_head(root)
    clauses: list[dict] = []

    def clause(cid: str, name: str, passed: bool, detail):
        clauses.append({"id": cid, "name": name, "passed": bool(passed), "detail": detail})

    # C1 project root
    is_git = (root / ".git").exists()
    is_canonical = (root / CANONICAL_ROOT_MARKER).exists()
    clause("C1", "project_root_is_git_under_m4_canonical_root",
           is_git and is_canonical,
           {"root": str(root), "git": is_git, "canonical_marker": is_canonical})

    # C2 regenerate write_authority_state
    state_path = evidence_dir / "write_authority_state.json"
    if regen:
        import importlib.util
        scan_path = root / ".dsh-control" / "gates" / "scan_write_authority.py"
        spec = importlib.util.spec_from_file_location("m4_scan", scan_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        state = mod.scan(root)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        state = _load_json(state_path) if state_path.exists() else {"_error": "missing"}

    state_ok = isinstance(state, dict) and "_error" not in state and "counters" in state
    clause("C2", "write_authority_state_present", state_ok,
           {"path": str(state_path),
            "error": state.get("_error") if isinstance(state, dict) else None})

    c = state.get("counters", {}) if isinstance(state, dict) else {}

    # C3 NORMAL_INDEX_ENTRYPOINTS > 0
    clause("C3", "NORMAL_INDEX_ENTRYPOINTS_gt_0",
           c.get("NORMAL_INDEX_ENTRYPOINTS", -1) > 0,
           {"NORMAL_INDEX_ENTRYPOINTS": c.get("NORMAL_INDEX_ENTRYPOINTS")})

    # C4 V2_PRIMARY == NORMAL
    clause("C4", "V2_PRIMARY_INDEX_ENTRYPOINTS_eq_NORMAL",
           c.get("V2_PRIMARY_INDEX_ENTRYPOINTS", -1) == c.get("NORMAL_INDEX_ENTRYPOINTS", -2)
           and c.get("NORMAL_INDEX_ENTRYPOINTS", 0) > 0,
           {"V2_PRIMARY": c.get("V2_PRIMARY_INDEX_ENTRYPOINTS"),
            "NORMAL": c.get("NORMAL_INDEX_ENTRYPOINTS")})

    # C5 LEGACY_WRITE_REQUIRED_FOR_SUCCESS == 0
    clause("C5", "LEGACY_WRITE_REQUIRED_FOR_SUCCESS_eq_0",
           c.get("LEGACY_WRITE_REQUIRED_FOR_SUCCESS", -1) == 0,
           {"LEGACY_WRITE_REQUIRED_FOR_SUCCESS": c.get("LEGACY_WRITE_REQUIRED_FOR_SUCCESS")})

    # C6 LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS == 0
    clause("C6", "LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS_eq_0",
           c.get("LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS", -1) == 0,
           {"LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS": c.get("LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS")})

    # C7 LEGACY_DB_REQUIRED_FOR_V2_INDEX == 0
    clause("C7", "LEGACY_DB_REQUIRED_FOR_V2_INDEX_eq_0",
           c.get("LEGACY_DB_REQUIRED_FOR_V2_INDEX", -1) == 0,
           {"LEGACY_DB_REQUIRED_FOR_V2_INDEX": c.get("LEGACY_DB_REQUIRED_FOR_V2_INDEX")})

    # C8 V2_FINALIZATION_IS_SUCCESS_AUTHORITY == true
    clause("C8", "V2_FINALIZATION_IS_SUCCESS_AUTHORITY_true",
           c.get("V2_FINALIZATION_IS_SUCCESS_AUTHORITY") is True,
           {"V2_FINALIZATION_IS_SUCCESS_AUTHORITY": c.get("V2_FINALIZATION_IS_SUCCESS_AUTHORITY")})

    # C9 INCOMPLETE_WORLD_EXPOSURE_PATHS == 0
    clause("C9", "INCOMPLETE_WORLD_EXPOSURE_PATHS_eq_0",
           c.get("INCOMPLETE_WORLD_EXPOSURE_PATHS", -1) == 0,
           {"INCOMPLETE_WORLD_EXPOSURE_PATHS": c.get("INCOMPLETE_WORLD_EXPOSURE_PATHS")})

    # C10 UNSAFE_PARTIAL_FINALIZATION_PATHS == 0
    clause("C10", "UNSAFE_PARTIAL_FINALIZATION_PATHS_eq_0",
           c.get("UNSAFE_PARTIAL_FINALIZATION_PATHS", -1) == 0,
           {"UNSAFE_PARTIAL_FINALIZATION_PATHS": c.get("UNSAFE_PARTIAL_FINALIZATION_PATHS")})

    # C11 EXPECTED_TU_ACCOUNTING_VIOLATIONS == 0
    clause("C11", "EXPECTED_TU_ACCOUNTING_VIOLATIONS_eq_0",
           c.get("EXPECTED_TU_ACCOUNTING_VIOLATIONS", -1) == 0,
           {"EXPECTED_TU_ACCOUNTING_VIOLATIONS": c.get("EXPECTED_TU_ACCOUNTING_VIOLATIONS")})

    # C12 V2_FAILURE_MASKED_BY_LEGACY_PATHS == 0
    clause("C12", "V2_FAILURE_MASKED_BY_LEGACY_PATHS_eq_0",
           c.get("V2_FAILURE_MASKED_BY_LEGACY_PATHS", -1) == 0,
           {"V2_FAILURE_MASKED_BY_LEGACY_PATHS": c.get("V2_FAILURE_MASKED_BY_LEGACY_PATHS")})

    # C13 LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS == 0
    clause("C13", "LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS_eq_0",
           c.get("LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS", -1) == 0,
           {"LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS": c.get("LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS")})

    # C14 UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS == 0
    clause("C14", "UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS_eq_0",
           c.get("UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS", -1) == 0,
           {"UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS": c.get("UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS")})

    # C15 CURRENT_WRITE_MODE == V2_PRIMARY_LEGACY_MIRROR
    clause("C15", "CURRENT_WRITE_MODE_is_V2_PRIMARY_LEGACY_MIRROR",
           c.get("CURRENT_WRITE_MODE") == "V2_PRIMARY_LEGACY_MIRROR",
           {"CURRENT_WRITE_MODE": c.get("CURRENT_WRITE_MODE")})

    # Fault-injection clauses C16-C22
    fault_tests = state.get("fault_tests", {}) if isinstance(state, dict) else {}
    fault_map = [
        ("C16", "V2_SUCCESS_LEGACY_FAILURE_TEST"),
        ("C17", "V2_FAILURE_LEGACY_SUCCESS_TEST"),
        ("C18", "CRASH_BEFORE_FINALIZE_TEST"),
        ("C19", "RESTART_IDEMPOTENCE_TEST"),
        ("C20", "RETRY_IDEMPOTENCE_TEST"),
        ("C21", "READ_AFTER_FINALIZE_TEST"),
        ("C22", "ROLLBACK_TEST"),
    ]
    for cid, tname in fault_map:
        verdict = fault_tests.get(tname, {}).get("verdict")
        clause(cid, f"{tname}_PASS", verdict == "PASS",
               {"verdict": verdict})

    # C23 M3B read authority regression
    m3b = c.get("M3B_READ_AUTHORITY_REGRESSION")
    clause("C23", "M3B_READ_AUTHORITY_REGRESSION_PASS", m3b == "PASS",
           {"M3B_READ_AUTHORITY_REGRESSION": m3b})

    # C24 regression
    reg_path = evidence_dir / "m4_regression.json"
    if regression_result is not None:
        reg = regression_result
        reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    elif run_regression_flag:
        reg = run_regression(root)
        reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    else:
        reg = _load_json(reg_path) if reg_path.exists() else {"_error": "missing"}
    reg_pass = isinstance(reg, dict) and reg.get("pass") is True
    clause("C24", "regression_pass", reg_pass,
           {"pass": reg.get("pass") if isinstance(reg, dict) else None,
            "head": reg.get("head") if isinstance(reg, dict) else None,
            "error": reg.get("_error") if isinstance(reg, dict) else None})

    # C25 fresh verifier
    vpath, v = _latest_artifact(evidence_dir, "m4_verifier")
    v_ok = (isinstance(v, dict)
            and v.get("verdict") == "PASS"
            and v.get("head") == head)
    clause("C25", "fresh_m4_verifier_passes_on_current_head", v_ok,
           {"artifact": str(vpath) if vpath else None,
            "verdict": v.get("verdict") if isinstance(v, dict) else None,
            "artifact_head": v.get("head") if isinstance(v, dict) else None,
            "current_head": head})

    # C26 fresh guardian
    gpath, g = _latest_artifact(evidence_dir, "m4_guardian")
    g_ok = (isinstance(g, dict)
            and g.get("verdict") == "ACCEPT_COMPLETION"
            and g.get("head") == head)
    clause("C26", "fresh_m4_guardian_accepts_on_current_head", g_ok,
           {"artifact": str(gpath) if gpath else None,
            "verdict": g.get("verdict") if isinstance(g, dict) else None,
            "artifact_head": g.get("head") if isinstance(g, dict) else None,
            "current_head": head})

    # C27 durable checkpoint
    rc, _ = _git(["rev-parse", "--verify", "HEAD"], root)
    head_resolves = rc == 0 and bool(head)
    clause("C27", "durable_checkpoint_head_resolves", head_resolves,
           {"head": head})

    failed = [c for c in clauses if not c["passed"]]
    passed = len(failed) == 0
    return {
        "milestone": "M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE",
        "head": head,
        "passed": passed,
        "clauses": clauses,
        "counters": c,
        "failed_clauses": [c["id"] for c in failed],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--evidence-dir", default=".dsh-control/evidence")
    ap.add_argument("--no-regen", action="store_true",
                    help="do not regenerate write_authority_state.json")
    ap.add_argument("--regression-file", default=None,
                    help="read regression result from this JSON instead of running")
    ap.add_argument("--no-regression", action="store_true",
                    help="do not run focused tests; read m4_regression.json if present")
    args = ap.parse_args()
    root = Path(args.project_root).resolve()
    evidence_dir = Path(args.evidence_dir).resolve()

    reg_result = None
    if args.regression_file:
        reg_result = _load_json(Path(args.regression_file))
    result = evaluate_gate(
        root, evidence_dir,
        regen=not args.no_regen,
        run_regression_flag=not args.no_regression,
        regression_result=reg_result,
    )
    print(f"[m4-gate] HEAD: {result['head']}")
    print(f"[m4-gate] RESULT: {'PASS' if result['passed'] else 'FAIL'}")
    for c in result["clauses"]:
        mark = "OK" if c["passed"] else "XX"
        print(f"  {mark} {c['id']}  {c['name']}  {c['detail']}")
    if not result["passed"]:
        print(f"[m4-gate] FAILED clauses: {', '.join(result['failed_clauses'])}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
