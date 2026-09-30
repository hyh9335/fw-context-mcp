#!/usr/bin/env python3
"""fw-context M3B stage hard gate.

Evaluates the immutable M3B formula against CURRENT code + acceptance
artifacts. No warning-pass. Exits non-zero on ANY failed clause.

Clauses (all must pass):
  C1  project root is a git repo under the canonical root
  C2  authority_state.json exists (regenerated unless --no-regen)
  C3  CORE_NORMAL_USE_SEMANTIC_TOOLS > 0
  C4  CORE_V2_ROUTED == CORE
  C5  CORE_V2_NATIVE_READ == CORE
  C6  CORE_LEGACY_SEMANTIC_EXECUTION == 0
  C7  CORE_UNSAFE_FALLBACK == 0
  C8  UNRESOLVED_NORMAL_USE_V2_BUGS == 0
  C9  regression PASS (focused tests green)
  C10 latest verifier artifact: verdict==PASS AND head==current HEAD
  C11 latest guardian artifact: verdict==ACCEPT_COMPLETION AND head==current HEAD
  C12 durable checkpoint: current HEAD resolves (is a real commit) AND artifacts
      name exactly that HEAD (covered by C10/C11)

Usage:
    python .dsh-control/gates/run_stage_gate.py [--project-root PATH]
        [--evidence-dir PATH] [--no-regen] [--regression-file PATH]
        [--no-regression]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CANONICAL_ROOT_MARKER = ".dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md"
FOCUSED_TESTS_K = "v2 or authority or shadow or adapter or queryview or world"


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
    clause("C1", "project_root_is_git_under_canonical_root",
           is_git and is_canonical,
           {"root": str(root), "git": is_git, "canonical_marker": is_canonical})

    # C2 regenerate authority_state
    state_path = evidence_dir / "authority_state.json"
    if regen:
        import importlib.util
        scan_path = root / ".dsh-control" / "gates" / "scan_authority_state.py"
        spec = importlib.util.spec_from_file_location("m3b_scan", scan_path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        state = mod.scan(root)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        state = _load_json(state_path) if state_path.exists() else {"_error": "missing"}

    state_ok = isinstance(state, dict) and "_error" not in state and "counters" in state
    clause("C2", "authority_state_present", state_ok,
           {"path": str(state_path),
            "error": state.get("_error") if isinstance(state, dict) else None})

    counters = state.get("counters", {}) if state_ok else {}
    core = counters.get("CORE_NORMAL_USE_SEMANTIC_TOOLS", -1)
    clause("C3", "CORE_gt_0", core > 0, {"CORE": core})
    clause("C4", "CORE_V2_ROUTED_eq_CORE",
           counters.get("CORE_V2_ROUTED", -1) == core,
           {"CORE_V2_ROUTED": counters.get("CORE_V2_ROUTED", -1), "CORE": core})
    clause("C5", "CORE_V2_NATIVE_READ_eq_CORE",
           counters.get("CORE_V2_NATIVE_READ", -1) == core,
           {"CORE_V2_NATIVE_READ": counters.get("CORE_V2_NATIVE_READ", -1), "CORE": core})
    clause("C6", "CORE_LEGACY_SEMANTIC_EXECUTION_eq_0",
           counters.get("CORE_LEGACY_SEMANTIC_EXECUTION", -1) == 0,
           {"CORE_LEGACY_SEMANTIC_EXECUTION": counters.get("CORE_LEGACY_SEMANTIC_EXECUTION", -1)})
    clause("C7", "CORE_UNSAFE_FALLBACK_eq_0",
           counters.get("CORE_UNSAFE_FALLBACK", -1) == 0,
           {"CORE_UNSAFE_FALLBACK": counters.get("CORE_UNSAFE_FALLBACK", -1),
            "sites": state.get("unsafe_fallback_sites", []) if state_ok else []})
    clause("C8", "UNRESOLVED_NORMAL_USE_V2_BUGS_eq_0",
           counters.get("UNRESOLVED_NORMAL_USE_V2_BUGS", -1) == 0,
           {"UNRESOLVED_NORMAL_USE_V2_BUGS": counters.get("UNRESOLVED_NORMAL_USE_V2_BUGS", -1)})

    # C9 regression
    reg_path = evidence_dir / "regression.json"
    if regression_result is not None:
        reg = regression_result
        reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    elif run_regression_flag:
        reg = run_regression(root)
        reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    else:
        reg = _load_json(reg_path) if reg_path.exists() else {"_error": "missing"}
    reg_pass = isinstance(reg, dict) and reg.get("pass") is True
    clause("C9", "regression_pass", reg_pass,
           {"pass": reg.get("pass") if isinstance(reg, dict) else None,
            "head": reg.get("head") if isinstance(reg, dict) else None,
            "error": reg.get("_error") if isinstance(reg, dict) else None})

    # C10 verifier
    vpath, v = _latest_artifact(evidence_dir, "verifier")
    v_ok = (isinstance(v, dict)
            and v.get("verdict") == "PASS"
            and v.get("head") == head)
    clause("C10", "fresh_verifier_passes_on_current_head", v_ok,
           {"artifact": str(vpath) if vpath else None,
            "verdict": v.get("verdict") if isinstance(v, dict) else None,
            "artifact_head": v.get("head") if isinstance(v, dict) else None,
            "current_head": head})

    # C11 guardian
    gpath, g = _latest_artifact(evidence_dir, "guardian")
    g_ok = (isinstance(g, dict)
            and g.get("verdict") == "ACCEPT_COMPLETION"
            and g.get("head") == head)
    clause("C11", "fresh_guardian_accepts_on_current_head", g_ok,
           {"artifact": str(gpath) if gpath else None,
            "verdict": g.get("verdict") if isinstance(g, dict) else None,
            "artifact_head": g.get("head") if isinstance(g, dict) else None,
            "current_head": head})

    # C12 durable checkpoint: HEAD resolves to a commit (always true post-init)
    rc, _ = _git(["rev-parse", "--verify", "HEAD"], root)
    head_resolves = rc == 0 and bool(head)
    clause("C12", "durable_checkpoint_head_resolves", head_resolves,
           {"head": head})

    failed = [c for c in clauses if not c["passed"]]
    passed = len(failed) == 0
    return {
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "head": head,
        "clauses": clauses,
        "failed": failed,
        "counters": counters,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="fw-context M3B stage hard gate")
    ap.add_argument("--project-root", default=str(Path.cwd().resolve()))
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--no-regen", action="store_true",
                    help="do not regenerate authority_state.json (self-test only)")
    ap.add_argument("--no-regression", action="store_true",
                    help="do not run focused tests; read regression.json if present")
    ap.add_argument("--regression-file", default=None,
                    help="read regression result from this JSON file")
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else (
        root / ".dsh-control" / "evidence"
    )

    reg_result = None
    if args.regression_file:
        reg_result = _load_json(Path(args.regression_file))

    result = evaluate_gate(
        root, evidence_dir,
        regen=not args.no_regen,
        run_regression_flag=not args.no_regression,
        regression_result=reg_result,
    )

    print(f"[gate] HEAD: {result['head']}")
    print(f"[gate] RESULT: {'PASS' if result['passed'] else 'FAIL'}")
    for c in result["clauses"]:
        mark = "OK " if c["passed"] else "XX "
        print(f"  {mark}{c['id']}  {c['name']}  {c['detail']}")
    if not result["passed"]:
        print("[gate] FAILED clauses:",
              ", ".join(c["id"] for c in result["failed"]), file=sys.stderr)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
