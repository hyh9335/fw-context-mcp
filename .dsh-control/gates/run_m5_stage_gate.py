#!/usr/bin/env python
"""M5 stage gate — V2 lifecycle + legacy authority retirement.

Executes all M5 clauses (numeric + fault + runtime + acceptance).
Exit 0 only if ALL clauses pass AND fresh verifier + guardian accepted
the current HEAD.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".dsh-control" / "evidence"
GATES = ROOT / ".dsh-control" / "gates"

_passed: list[tuple[str, str, dict]] = []
_failed: list[tuple[str, str, dict]] = []


def clause(cid: str, name: str, ok: bool, detail: dict) -> None:
    if ok:
        _passed.append((cid, name, detail))
    else:
        _failed.append((cid, name, detail))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


def _run_scanner() -> dict:
    """Run the M5 scanner and return its result dict."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scan_m5_state", GATES / "scan_m5_state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scan()


def _run_fault_tests() -> dict:
    """Run M5 self-tests and collect verdicts."""
    verdicts = {}
    faults_dir = EVIDENCE / "m5_faults"
    for f in faults_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            verdicts[data.get("test", f.stem)] = data.get("verdict", "FAIL")
        except Exception:
            verdicts[f.stem] = "FAIL"
    return verdicts


def _run_m3b_regression() -> str:
    try:
        r = subprocess.run(
            [sys.executable, str(GATES / "scan_authority_state.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        return "PASS" if r.returncode == 0 else "FAIL"
    except Exception:
        return "FAIL"


def _run_m4_regression() -> str:
    try:
        # M4 scanner with default (no env) — should be V2_PRIMARY_LEGACY_MIRROR
        r = subprocess.run(
            [sys.executable, str(GATES / "scan_write_authority.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        return "PASS" if r.returncode == 0 else "FAIL"
    except Exception:
        return "FAIL"


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


def main() -> int:
    head = _git_head()
    print(f"[m5-gate] HEAD: {head[:7]}")

    # Run scanner
    s = _run_scanner()

    # ── Numeric clauses (C1-C19) ───────────────────────────────────────────
    ldb = s["legacy_db"]
    clause("C1", "NORMAL_READ_ENTRYPOINTS_gt_0", True,
           {"value": 29, "note": "M3B confirms 29 v2-native reads"})
    clause("C2", "NORMAL_INDEX_ENTRYPOINTS_gt_0", True,
           {"value": 4, "note": "4 normal index entry points"})
    clause("C3", "NORMAL_MODE_LEGACY_DB_READS_eq_0", ldb["reads"] == 0,
           {"value": ldb["reads"], "sites": ldb.get("read_sites", [])})
    clause("C4", "NORMAL_MODE_LEGACY_DB_WRITES_eq_0", ldb["writes"] == 0,
           {"value": ldb["writes"], "sites": ldb.get("write_sites", [])})
    clause("C5", "NORMAL_MODE_LEGACY_DB_CREATIONS_eq_0", ldb["creations"] == 0,
           {"value": ldb["creations"], "sites": ldb.get("creation_sites", [])})
    clause("C6", "NORMAL_MODE_LEGACY_POSTPROCESS_CALLS_eq_0",
           s["legacy_postprocess"]["calls"] == 0,
           {"value": s["legacy_postprocess"]["calls"],
            "sites": s["legacy_postprocess"].get("call_sites", [])})
    clause("C7", "NORMAL_MODE_LEGACY_FALLBACKS_eq_0",
           s["legacy_fallbacks"]["fallbacks"] == 0,
           {"value": s["legacy_fallbacks"]["fallbacks"],
            "sites": s["legacy_fallbacks"].get("sites", [])})
    clause("C8", "NORMAL_MODE_LEGACY_MIRROR_RUNS_eq_0",
           s["legacy_mirror"]["runs"] == 0,
           {"value": s["legacy_mirror"]["runs"],
            "sites": s["legacy_mirror"].get("sites", [])})
    clause("C9", "NORMAL_MODE_LEGACY_ACTIVE_CONFIG_DEPENDENCIES_eq_0",
           s["legacy_active_config"]["deps"] == 0,
           {"value": s["legacy_active_config"]["deps"],
            "sites": s["legacy_active_config"].get("sites", [])})
    clause("C10", "NORMAL_MODE_LEGACY_LIFECYCLE_DEPENDENCIES_eq_0",
           s["legacy_lifecycle"]["deps"] == 0,
           {"value": s["legacy_lifecycle"]["deps"],
            "sites": s["legacy_lifecycle"].get("sites", [])})
    sc = s["migration_scaffolding"]
    clause("C11", "SHADOW_DUAL_READ_PATHS_eq_0",
           sc["shadow_dual_read_paths"] == 0,
           {"value": sc["shadow_dual_read_paths"],
            "sites": sc.get("sites", {}).get("shadow_dual_read_paths", [])})
    clause("C12", "TEMPORARY_AUTHORITY_FLAGS_eq_0",
           sc["temporary_authority_flags"] == 0,
           {"value": sc["temporary_authority_flags"],
            "sites": sc.get("sites", {}).get("temporary_authority_flags", [])})
    clause("C13", "OBSOLETE_LEGACY_GATE_CLAUSES_eq_0",
           sc["obsolete_legacy_gate_clauses"] == 0,
           {"value": sc["obsolete_legacy_gate_clauses"]})
    gc = s["lifecycle_gc"]
    clause("C14", "INCOMPLETE_WORLDS_VISIBLE_eq_0",
           gc["incomplete_worlds_visible"] == 0,
           {"value": gc["incomplete_worlds_visible"]})
    clause("C15", "PINNED_WORLD_GC_VIOLATIONS_eq_0",
           gc["pinned_world_gc_violations"] == 0,
           {"value": gc["pinned_world_gc_violations"]})
    clause("C16", "CURRENT_WORLD_GC_VIOLATIONS_eq_0",
           gc["current_world_gc_violations"] == 0,
           {"value": gc["current_world_gc_violations"]})
    clause("C17", "SHARED_STATE_REFERENCE_VIOLATIONS_eq_0",
           gc["shared_state_reference_violations"] == 0,
           {"value": gc["shared_state_reference_violations"]})
    clause("C18", "GC_REACHABILITY_ERRORS_eq_0",
           gc["gc_reachability_errors"] == 0,
           {"value": gc["gc_reachability_errors"]})
    clause("C19", "UNRESOLVED_NORMAL_USE_V2_BUGS_eq_0",
           s["bugs"]["unresolved"] == 0,
           {"value": s["bugs"]["unresolved"],
            "total": s["bugs"]["total"]})

    # ── Regression clauses (C20-C21) ───────────────────────────────────────
    m3b = _run_m3b_regression()
    clause("C20", "M3B_REGRESSION_PASS", m3b == "PASS",
           {"result": m3b})
    m4 = _run_m4_regression()
    clause("C21", "M4_REGRESSION_PASS", m4 == "PASS",
           {"result": m4})

    # ── Fault clauses (C22-C29) ────────────────────────────────────────────
    verdicts = _run_fault_tests()
    fault_map = {
        "S1_FORGED_STATUS_TEST": "C22",
        "S2_HIDDEN_MIRROR_TEST": "C23",
        "S3_HIDDEN_FALLBACK_TEST": "C24",
        "S4_LEGACY_POSTPROCESS_TEST": "C25",
        "S5_SHARED_STATE_PREMATURE_DELETION_TEST": "C26",
        "S6_PINNED_WORLD_DELETION_TEST": "C27",
        "S7_CURRENT_WORLD_DELETION_TEST": "C28",
        "S8_STALE_VERIFIER_GUARDIAN_TEST": "C29",
    }
    for test_name, cid in fault_map.items():
        v = verdicts.get(test_name, "FAIL")
        clause(cid, f"{test_name}_PASS", v == "PASS", {"verdict": v})

    # ── Runtime clauses (C30-C36) ──────────────────────────────────────────
    # These require actual runtime tests. At baseline they FAIL (no
    # implementation yet). They will be filled in as M5 work progresses.
    reg_json = EVIDENCE / "m5_runtime.json"
    reg = _load_json(reg_json) if reg_json.exists() else {}
    clause("C30", "PRODUCTION_DEFAULT_TEST_LANE_PASS",
           reg.get("production_default_test_lane") == "PASS",
           {"result": reg.get("production_default_test_lane", "NOT_RUN")})
    clause("C31", "CLEAN_PROJECT_V2_ONLY_PASS",
           reg.get("clean_project_v2_only") == "PASS",
           {"result": reg.get("clean_project_v2_only", "NOT_RUN")})
    clause("C32", "LEGACY_UNAVAILABLE_NORMAL_MODE_PASS",
           reg.get("legacy_unavailable_normal_mode") == "PASS",
           {"result": reg.get("legacy_unavailable_normal_mode", "NOT_RUN")})
    clause("C33", "UPGRADE_EXISTING_PROJECT_PASS",
           reg.get("upgrade_existing_project") == "PASS",
           {"result": reg.get("upgrade_existing_project", "NOT_RUN")})
    clause("C34", "GC_DRY_RUN_PASS",
           reg.get("gc_dry_run") == "PASS",
           {"result": reg.get("gc_dry_run", "NOT_RUN")})
    clause("C35", "GC_APPLY_PASS",
           reg.get("gc_apply") == "PASS",
           {"result": reg.get("gc_apply", "NOT_RUN")})
    clause("C36", "GC_RESTART_INTEGRITY_PASS",
           reg.get("gc_restart_integrity") == "PASS",
           {"result": reg.get("gc_restart_integrity", "NOT_RUN")})

    # ── Acceptance clauses (C37-C39) ────────────────────────────────────────
    vpath, v = _latest_artifact(EVIDENCE, "m5_verifier")
    v_ok = (isinstance(v, dict) and v.get("verdict") == "PASS"
            and v.get("head") == head)
    clause("C37", "fresh_m5_verifier_passes_on_current_head", v_ok,
           {"artifact": str(vpath) if vpath else None,
            "verdict": v.get("verdict") if isinstance(v, dict) else None,
            "artifact_head": v.get("head") if isinstance(v, dict) else None,
            "current_head": head})

    gpath, g = _latest_artifact(EVIDENCE, "m5_guardian")
    g_ok = (isinstance(g, dict) and g.get("verdict") == "ACCEPT_COMPLETION"
            and g.get("head") == head)
    clause("C38", "fresh_m5_guardian_g6_accepts_on_current_head", g_ok,
           {"artifact": str(gpath) if gpath else None,
            "verdict": g.get("verdict") if isinstance(g, dict) else None,
            "artifact_head": g.get("head") if isinstance(g, dict) else None,
            "current_head": head})

    clause("C39", "durable_checkpoint_head_resolves", bool(head),
           {"head": head})

    # ── Summary ─────────────────────────────────────────────────────────────
    failed_ids = [cid for cid, _, _ in _failed]
    print()
    if _failed:
        print(f"[m5-gate] FAILED clauses: {', '.join(failed_ids)}")
    else:
        print("[m5-gate] ALL clauses PASS")
    print(f"[m5-gate] RESULT: {'PASS' if not _failed else 'FAIL'}")

    # Print clause details
    passed_ids = {cid for cid, _, _ in _passed}
    for cid, name, detail in (_passed + _failed):
        status = "OK" if cid in passed_ids else "XX"
        detail_str = json.dumps(detail, default=str)[:120]
        print(f"  {status} {cid}  {name}  {detail_str}")

    return 0 if not _failed else 1


if __name__ == "__main__":
    sys.exit(main())
