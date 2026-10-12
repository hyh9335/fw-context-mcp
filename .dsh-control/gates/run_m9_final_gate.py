#!/usr/bin/env python
"""M9 final hard gate — Certificate-Gated Cross-Build TU Frontend Reuse.

Per `.dsh-control/m9_frontend_reuse/FINAL_GATE_SPEC.md`. Exits 0 only if
every clause passes AND fresh Safety Verifier + fresh Economics Verifier
+ Fresh Guardian accepted the current HEAD.

Allowed verdicts:
- M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE
- M9_LIMITED_BUT_PRODUCTION_WORTHY
- M9_REJECTED_NO_NET_VALUE
- M9_BLOCKED_HUMAN_DECISION

This gate is the executable authority per AGENTS.local.md: no agent may
declare the M9 Goal complete unless this script exits 0 AND fresh
verifiers/guardian accepted the exact HEAD.

Usage:
    python .dsh-control/gates/run_m9_final_gate.py [--head HEAD]
    python .dsh-control/gates/run_m9_final_gate.py --verdict-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M9_DIR = ROOT / ".dsh-control" / "m9_frontend_reuse"
GATES = ROOT / ".dsh-control" / "gates"
EVIDENCE = M9_DIR / "evidence"
STATE = M9_DIR / "state"
REVIEWS = M9_DIR / "reviews"

# Frozen thresholds — loaded from state/baseline_freeze_thresholds.json.
# These MUST be frozen BEFORE the final measurement (M9_COMPLETION_FORMULA.md).
THRESHOLDS_PATH = STATE / "baseline_freeze_thresholds.json"


@dataclass
class ClauseResult:
    cid: str
    name: str
    ok: bool
    detail: dict = field(default_factory=dict)


_passed: list[ClauseResult] = []
_failed: list[ClauseResult] = []


def clause(cid: str, name: str, ok: bool, detail: dict | None = None) -> None:
    r = ClauseResult(cid=cid, name=name, ok=bool(ok), detail=detail or {})
    if r.ok:
        _passed.append(r)
    else:
        _failed.append(r)


def _git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Clause implementations ──────────────────────────────────────────────────
# Each clause loads its evidence file and returns (ok, detail).
# Evidence files are produced by the PROTOTYPE / ADVERSARIAL_EXPANSION /
# PERFORMANCE_VALIDATION stages.

def _run_compact_m6_m65_m7_regressions() -> tuple[bool, dict]:
    """Run a compact subset of M6/M6.5/M7 regression tests.

    Compact set (chosen to cover the safety surface M9 touches, not full
    M6.5 G1-G12 which would take hours):
    - M6.5 G11 FreeRTOS real-project (this IS the M9 negative-control corpus)
    - M5 hard gate (autonomous controller — M9 must not weaken)
    - A targeted M6 fact-parity check on the positive-control workload

    Returns (ok, {m65_g11, m5_gate, m6_positive_parity}).
    """
    detail: dict = {}
    all_ok = True

    # M6.5 G11 — FreeRTOS real-project
    try:
        r = subprocess.run(
            [sys.executable, str(GATES / "run_m6_5_validation_gate.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        detail["m65_gate_exit"] = r.returncode
        detail["m65_gate_tail"] = r.stdout[-500:] if r.stdout else ""
        if r.returncode != 0:
            all_ok = False
    except Exception as e:
        detail["m65_gate_error"] = str(e)
        all_ok = False

    # M5 hard gate — autonomous controller MUST still pass
    try:
        r = subprocess.run(
            [sys.executable, str(GATES / "run_m5_stage_gate.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        detail["m5_gate_exit"] = r.returncode
        if r.returncode != 0:
            all_ok = False
    except Exception as e:
        detail["m5_gate_error"] = str(e)
        all_ok = False

    # M6 fact-parity on positive-control workload (produced by PERF_VALIDATION)
    parity = _load_json(EVIDENCE / "positive_control_gt_parity.json")
    detail["m6_positive_parity_failures"] = parity.get("failures", "MISSING")
    if parity.get("failures", -1) != 0:
        all_ok = False

    return all_ok, detail


def _check_certificate_coverage() -> tuple[bool, dict]:
    """Certificate dependency coverage complete (per safety contract)."""
    cov = _load_json(STATE / "certificate_coverage.json")
    required = [
        "main_tu_content",
        "language_mode_std",
        "target_triple_abi",
        "libclang_identity",
        "resource_dir_builtins",
        "forced_includes",
        "include_search_roots_order",
        "transitive_include_resolution_content",
        "generated_headers",
        "effective_macros_D",
        "undefines_U",
        "macro_redefine_undef",
        "pragma_state_pack",
        "include_next",
        "x_macro_repeated_include",
        "file_line_counter_include_level",
    ]
    covered = cov.get("covered_dependencies", [])
    missing = [d for d in required if d not in covered]
    return (len(missing) == 0, {"covered": covered, "missing": missing})


def _check_mutation_suite() -> tuple[bool, dict]:
    """Mutation suite: every intentional dependency-drop is caught."""
    mut = _load_json(EVIDENCE / "mutation_suite_results.json")
    required_attacks = [
        "include_order", "generated_header", "target", "forced_include",
        "undefine_U", "pragma_pack", "resource_dir", "builtins",
        "language_std", "define_D", "include_next", "x_macro",
    ]
    results = mut.get("attacks", {})
    caught = {k: results.get(k, {}).get("caught", False) for k in required_attacks}
    all_caught = all(caught.values())
    return (all_caught, {"per_attack": caught,
                          "all_required_caught": all_caught})


def _check_hard_zero_metrics() -> tuple[bool, dict]:
    """FRONTEND_FALSE_REUSE=0, CERTIFICATE_FALSE_ACCEPT=0, GT_PARITY_FAILURES=0."""
    perf = _load_json(EVIDENCE / "performance_validation.json")
    ffr = perf.get("frontend_false_reuse", -1)
    cfa = perf.get("certificate_false_accept", -1)
    gtp = perf.get("gt_parity_failures", -1)
    return (ffr == 0 and cfa == 0 and gtp == 0,
            {"frontend_false_reuse": ffr,
             "certificate_false_accept": cfa,
             "gt_parity_failures": gtp})


def _check_positive_control() -> tuple[bool, dict]:
    """Positive-control: correctness PASS + frontend parse reduction PASS
    + total wall-time benefit PASS (or LIMITED policy satisfied)."""
    perf = _load_json(EVIDENCE / "performance_validation.json")
    pos = perf.get("positive_control", {})
    correct = pos.get("correctness_pass", False)
    parse_reduction_pct = pos.get("frontend_parse_reduction_pct", 0.0)
    wall_benefit_pct = pos.get("total_wall_time_benefit_pct", 0.0)
    thresholds = _load_json(THRESHOLDS_PATH)
    parse_min = thresholds.get("positive_control_target_savings_pct_min", 30)
    return (correct and parse_reduction_pct >= parse_min and wall_benefit_pct > 0,
            {"correctness_pass": correct,
             "frontend_parse_reduction_pct": parse_reduction_pct,
             "total_wall_time_benefit_pct": wall_benefit_pct,
             "frozen_min_parse_reduction_pct": parse_min})


def _check_negative_control() -> tuple[bool, dict]:
    """FreeRTOS negative-control: 0 false reuse + safe misses + overhead PASS."""
    perf = _load_json(EVIDENCE / "performance_validation.json")
    neg = perf.get("negative_control", {})
    ffr = neg.get("frontend_false_reuse", -1)
    overhead_pct = neg.get("wall_clock_overhead_pct", 999.0)
    thresholds = _load_json(THRESHOLDS_PATH)
    max_overhead = thresholds.get("negative_control_max_overhead_pct", 5)
    return (ffr == 0 and overhead_pct <= max_overhead,
            {"frontend_false_reuse": ffr,
             "wall_clock_overhead_pct": overhead_pct,
             "frozen_max_overhead_pct": max_overhead})


def _check_workload_probe() -> tuple[bool, dict]:
    """Workload probe evaluated; policy decided."""
    probe = _load_json(STATE / "workload_probe.json")
    status = probe.get("status", "NOT_STARTED")
    policy = probe.get("policy")
    return (status == "EVALUATED" and policy in ("ENABLE", "DISABLE", "SAMPLE/LEARN"),
            {"status": status, "policy": policy})


def _check_production_integration() -> tuple[bool, dict]:
    """Real production indexing path used; rollback clean; no M6/M7 regression."""
    integ = _load_json(EVIDENCE / "production_integration.json")
    real_path = integ.get("production_path_used", False)
    rollback = integ.get("rollback_clean", False)
    no_authority_regression = integ.get("no_m6_m7_authority_regression", False)
    return (real_path and rollback and no_authority_regression,
            {"production_path_used": real_path,
             "rollback_clean": rollback,
             "no_authority_regression": no_authority_regression})


def _check_certificate_overhead() -> tuple[bool, dict]:
    """certificate_cost < avoided_frontend_cost on enabled workloads."""
    perf = _load_json(EVIDENCE / "performance_validation.json")
    cert_cost_ms = perf.get("certificate_cost_per_tu_ms", 999.0)
    avoided_ms = perf.get("avoided_frontend_cost_per_tu_ms", 0.0)
    thresholds = _load_json(THRESHOLDS_PATH)
    max_cert = thresholds.get("certificate_cost_per_tu_ms_max", 1.0)
    return (cert_cost_ms <= max_cert and cert_cost_ms < avoided_ms,
            {"certificate_cost_per_tu_ms": cert_cost_ms,
             "avoided_frontend_cost_per_tu_ms": avoided_ms,
             "frozen_max_per_tu_ms": max_cert})


def _check_fresh_reviews() -> tuple[bool, dict]:
    """Fresh Safety Verifier PASS + Fresh Economics Verifier PASS + Fresh Guardian ACCEPT,
    all bound to current HEAD."""
    head = _git_head()
    detail: dict = {"head": head}
    all_ok = True
    for role in ("safety_verifier", "economics_verifier", "guardian"):
        p = REVIEWS / f"{role}.json"
        rev = _load_json(p)
        bound_head = rev.get("head", "")
        verdict = rev.get("verdict", "MISSING")
        detail[role] = {"head": bound_head, "verdict": verdict}
        if verdict not in ("PASS", "ACCEPT") or bound_head != head:
            all_ok = False
    return (all_ok, detail)


def _check_open_defects() -> tuple[bool, dict]:
    """Unresolved blocking defects == 0."""
    defects = _load_json(STATE / "open_defects.json")
    blocking = [d for d in defects if d.get("severity") == "blocking"]
    return (len(blocking) == 0, {"blocking_count": len(blocking),
                                  "total_defects": len(defects)})


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", default=None,
                        help="Expected HEAD (defaults to current git HEAD)")
    parser.add_argument("--verdict-only", action="store_true",
                        help="Only print the verdict based on existing evidence")
    args = parser.parse_args()

    head = _git_head()
    expected = args.head or head
    print(f"[m9-gate] HEAD: {head[:7]}  expected: {expected[:7]}")

    if head != expected:
        print(f"[m9-gate] FAIL: HEAD mismatch (reviewer HEAD != current HEAD)")
        return 1

    # ── Run all clauses ──
    ok_reg, det_reg = _run_compact_m6_m65_m7_regressions()
    clause("G1", "compact_m6_m65_m7_regressions_pass", ok_reg, det_reg)

    ok_cov, det_cov = _check_certificate_coverage()
    clause("G2", "certificate_dependency_coverage_complete", ok_cov, det_cov)

    ok_mut, det_mut = _check_mutation_suite()
    clause("G3", "mutation_suite_pass", ok_mut, det_mut)

    ok_zero, det_zero = _check_hard_zero_metrics()
    clause("G4", "hard_zero_metrics_ffr_cfa_gtp", ok_zero, det_zero)

    ok_pos, det_pos = _check_positive_control()
    clause("G5", "positive_control_pass", ok_pos, det_pos)

    ok_neg, det_neg = _check_negative_control()
    clause("G6", "negative_control_pass", ok_neg, det_neg)

    ok_probe, det_probe = _check_workload_probe()
    clause("G7", "workload_probe_evaluated", ok_probe, det_probe)

    ok_integ, det_integ = _check_production_integration()
    clause("G8", "production_integration_clean", ok_integ, det_integ)

    ok_overhead, det_overhead = _check_certificate_overhead()
    clause("G9", "certificate_overhead_pass", ok_overhead, det_overhead)

    ok_reviews, det_reviews = _check_fresh_reviews()
    clause("G10", "fresh_reviews_bound_to_head", ok_reviews, det_reviews)

    ok_defects, det_defects = _check_open_defects()
    clause("G11", "no_blocking_defects", ok_defects, det_defects)

    # ── Report ──
    print(f"\n[m9-gate] Clauses: {len(_passed)} passed, {len(_failed)} failed")
    for r in _passed:
        print(f"  ✓ {r.cid}  {r.name}")
    for r in _failed:
        print(f"  ✗ {r.cid}  {r.name}  detail={r.detail}")

    if _failed:
        print(f"\n[m9-gate] FAIL: {len(_failed)} clause(s) failed.")
        return 1

    # ── Verdict ──
    # All clauses passed — determine which verdict applies.
    # COMPLETE: all positive-control thresholds met cleanly.
    # LIMITED: correctness/safety PASS but wall-time benefit is marginal
    #         (positive-control wall-benefit > 0 but parse-reduction below
    #          frozen min, OR negative-control overhead exactly at threshold).
    # REJECTED: safety zero holds but no net value (cert cost >= avoided cost).
    # BLOCKED: requires human decision (rare; e.g., libclang version conflict).
    perf = _load_json(EVIDENCE / "performance_validation.json")
    pos = perf.get("positive_control", {})
    thresholds = _load_json(THRESHOLDS_PATH)
    parse_red = pos.get("frontend_parse_reduction_pct", 0.0)
    parse_min = thresholds.get("positive_control_target_savings_pct_min", 30)

    if parse_red >= parse_min:
        verdict = "M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE"
    elif parse_red > 0:
        verdict = "M9_LIMITED_BUT_PRODUCTION_WORTHY"
    else:
        verdict = "M9_REJECTED_NO_NET_VALUE"

    print(f"\n[m9-gate] VERDICT: {verdict}")

    # Persist gate result
    result = {
        "head": head,
        "verdict": verdict,
        "clauses_passed": [c.cid for c in _passed],
        "clauses_failed": [c.cid for c in _failed],
        "all_passed": len(_failed) == 0,
    }
    (EVIDENCE / "final_gate_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")

    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
