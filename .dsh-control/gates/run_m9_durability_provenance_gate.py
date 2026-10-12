#!/usr/bin/env python3
"""M9 Final Durability & Provenance Closure Gate.

Verifies that the M9 implementation is durably committed, reviews are bound
to the exact committed HEAD, and all hard requirements are met. Exits nonzero
if reviewer provenance is stale.

Per the durable completion contract (§14):
    M9_EXECUTABLE_CODE_COMMITTED = true
    UNCOMMITTED_M9_EXECUTABLE_DIFF = 0
    WORKLOAD_PROBE_CONTRACT = PASS
    M9_FINAL_GATE = PASS
    FRESH_SAFETY_VERIFIER = PASS
    FRESH_ECONOMICS_VERIFIER = PASS
    FRESH_GUARDIAN = ACCEPT_COMPLETION
    REVIEW_HEAD_MATCH = true
    FFR = 0
    CFA = 0
    GTP = 0
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
M9_DIR = REPO / ".dsh-control" / "m9_frontend_reuse"
EVIDENCE = M9_DIR / "evidence"
REVIEWS = M9_DIR / "reviews"
STATE = M9_DIR / "state"
GATE_RESULT = EVIDENCE / "final_gate_result.json"

M9_FINAL_CODE_HEAD = "a965c871f3e3e2b8a15cce894a34f6cf2e0bbef1"


def _git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                       capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


def _git_diff_src() -> str:
    """Uncommitted executable diff in src/."""
    r = subprocess.run(["git", "diff", "HEAD", "--", "src/"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def _git_cat_file_exists(path: str) -> bool:
    r = subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"],
                       cwd=str(REPO), capture_output=True, timeout=10)
    return r.returncode == 0


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
    """True if `ancestor` is reachable from `descendant` (or they are equal)."""
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(REPO), capture_output=True, timeout=10,
    )
    return r.returncode == 0


def _git_diff_src_between(a: str, b: str) -> str:
    """Executable diff in src/ between two commits (empty = no src/ change)."""
    r = subprocess.run(["git", "diff", a, b, "--", "src/"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    head = _git_head()
    print(f"[m9-durability] HEAD: {head[:7]}")
    print(f"[m9-durability] Expected M9_FINAL_CODE_HEAD: {M9_FINAL_CODE_HEAD[:7]}")

    checks = []

    # 1. M9_EXECUTABLE_CODE_COMMITTED
    cert_committed = _git_cat_file_exists("src/fw_context_mcp/indexer/certificate.py")
    unit_committed = _git_cat_file_exists("src/fw_context_mcp/indexer/_unit_processor.py")
    checks.append(("M9_EXECUTABLE_CODE_COMMITTED",
                   cert_committed and unit_committed,
                   f"certificate.py committed={cert_committed}, _unit_processor.py committed={unit_committed}"))

    # 2. UNCOMMITTED_M9_EXECUTABLE_DIFF = 0
    src_diff = _git_diff_src()
    diff_lines = len(src_diff.splitlines()) if src_diff else 0
    checks.append(("UNCOMMITTED_M9_EXECUTABLE_DIFF",
                   diff_lines == 0,
                   f"uncommitted src/ diff lines: {diff_lines}"))

    # 3. M9_FINAL_CODE_HEAD is reachable from HEAD (ancestor-or-equal),
    #    AND no executable src/ changes between them (closure commits are
    #    evidence-only — they must not alter M9 production code).
    is_ancestor = _git_is_ancestor(M9_FINAL_CODE_HEAD, head)
    src_between = _git_diff_src_between(M9_FINAL_CODE_HEAD, head)
    src_between_lines = len(src_between.splitlines()) if src_between else 0
    code_reachable = is_ancestor and src_between_lines == 0
    checks.append(("M9_CODE_REACHABLE_FROM_HEAD",
                   code_reachable,
                   f"a965c87 is ancestor of HEAD={is_ancestor}, "
                   f"src/ diff a965c87..HEAD lines={src_between_lines}"))

    # 4. WORKLOAD_PROBE_CONTRACT = PASS
    audit = EVIDENCE / "workload_probe_contract_audit.md"
    audit_exists = audit.exists()
    probe_state = _load_json(STATE / "workload_probe.json")
    probe_evaluated = (probe_state.get("status") == "EVALUATED" and
                       probe_state.get("policy") in ("ENABLE", "DISABLE", "SAMPLE/LEARN"))
    checks.append(("WORKLOAD_PROBE_CONTRACT",
                   audit_exists and probe_evaluated,
                   f"audit exists={audit_exists}, status={probe_state.get('status')}, policy={probe_state.get('policy')}"))

    # 5. M9_FINAL_GATE = PASS
    gate_result = _load_json(GATE_RESULT)
    gate_pass = (gate_result.get("all_passed") is True and
                 gate_result.get("verdict") == "M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE")
    checks.append(("M9_FINAL_GATE",
                   gate_pass,
                   f"verdict={gate_result.get('verdict')}, all_passed={gate_result.get('all_passed')}"))

    # 6-8. Fresh reviews bound to M9_FINAL_CODE_HEAD (the commit that
    #      introduced the M9 executable code). The provenance rule is
    #      review_head == final_m9_code_head, NOT review_head == current HEAD.
    #      Closure commits that add evidence artifacts do not change the
    #      code under review.
    for role, expected_verdicts in [
        ("safety_verifier", ("PASS",)),
        ("economics_verifier", ("PASS",)),
        ("guardian", ("ACCEPT", "ACCEPT_COMPLETION")),
    ]:
        rev = _load_json(REVIEWS / f"{role}.json")
        rev_head = rev.get("head", "")
        rev_verdict = rev.get("verdict", "MISSING")
        head_match_role = (rev_head == M9_FINAL_CODE_HEAD)
        verdict_ok = rev_verdict in expected_verdicts
        label = f"FRESH_{role.upper()}"
        checks.append((label,
                       head_match_role and verdict_ok,
                       f"review_head={rev_head[:7]}, final_m9_code_head={M9_FINAL_CODE_HEAD[:7]}, "
                       f"verdict={rev_verdict}, head_match={head_match_role}"))

    # 9. REVIEW_HEAD_MATCH (all reviews bound to M9_FINAL_CODE_HEAD)
    all_heads = set()
    for role in ("safety_verifier", "economics_verifier", "guardian"):
        rev = _load_json(REVIEWS / f"{role}.json")
        all_heads.add(rev.get("head", ""))
    review_head_match = (len(all_heads) == 1 and all_heads.pop() == M9_FINAL_CODE_HEAD)
    checks.append(("REVIEW_HEAD_MATCH",
                   review_head_match,
                   f"all review heads == final_m9_code_head {M9_FINAL_CODE_HEAD[:7]}: {review_head_match}"))

    # 10-12. FFR=0, CFA=0, GTP=0
    perf = _load_json(EVIDENCE / "performance_validation.json")
    ffr = perf.get("frontend_false_reuse", -1)
    cfa = perf.get("certificate_false_accept", -1)
    gtp = perf.get("gt_parity_failures", -1)
    checks.append(("FFR", ffr == 0, f"frontend_false_reuse={ffr}"))
    checks.append(("CFA", cfa == 0, f"certificate_false_accept={cfa}"))
    checks.append(("GTP", gtp == 0, f"gt_parity_failures={gtp}"))

    # Print results
    print("\n" + "=" * 60)
    print("  M9 DURABILITY & PROVENANCE CLOSURE GATE")
    print("=" * 60)
    all_pass = True
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            all_pass = False

    verdict = ("M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE" if all_pass
               else "M9_DURABLE_COMPLETION_BLOCKED")
    print(f"\n  VERDICT: {verdict}")
    print("=" * 60)

    # Write result
    result = {
        "head": head,
        "m9_final_code_head": M9_FINAL_CODE_HEAD,
        "verdict": verdict,
        "all_passed": all_pass,
        "checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
    }
    out = EVIDENCE / "durability_provenance_gate_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  Result written to {out}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
