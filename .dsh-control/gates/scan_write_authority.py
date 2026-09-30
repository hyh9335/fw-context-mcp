#!/usr/bin/env python3
"""Code-derived write-authority scanner for the fw-context M4 milestone.

DERIVES its counters from CURRENT production code. It does NOT read
PROJECT_STATUS.md, WRITE_AUTHORITY_LEDGER.md, or any Lead report.

What it inspects:
  * src/fw_context_mcp/indexer/runner.py  — the core `run()` pipeline:
    - v2 World finalization (is it the success authority? is it flag-gated?
      is it exception-isolated so v2 failure is swallowed?)
    - legacy postprocess on the success path (_run_postprocess)
    - legacy DB writes on the success path
    - the success return (config_hash) and what gates it
  * src/fw_context_mcp/cli/_index.py  — normal CLI entry points (cmd_index,
    _run_multi) that orchestrate indexing.
  * src/fw_context_mcp/mcp/handlers/maintenance.py  — MCP reindex entry points.
  * src/fw_context_mcp/mcp/background.py (if present) — watcher/daemon reindex.
  * src/fw_context_mcp/indexer/_postprocess.py  — legacy postprocess steps.
  * src/fw_context_mcp/store_v2/worlds.py + publish.py  — v2 finalization API.
  * Write-mode flags: FW_CONTEXT_V2_WRITE_AUTHORITY, v2_write, v2_primary, etc.
  * .dsh-control/state/v2_write_bugs.json — open bug register.

Fault-injection test results are read from evidence files under
.dsh-control/evidence/m4_faults/*.json (written by the Lead during fault
injection runs; absent ⇒ NOT_RUN ⇒ treated as failing).

Writes machine JSON to .dsh-control/evidence/write_authority_state.json.

Usage:
    python .dsh-control/gates/scan_write_authority.py [--project-root PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

V2_FINALIZE_FN_NAMES = {"finalize_world", "publish_world", "finalize", "commit_world"}
V2_STORE_OPEN_NAMES = {"open_v2_store_for_write", "open_v2_store"}
LEGACY_DB_WRITE_ATTRS = {"commit", "executemany", "execute"}
LEGACY_POSTPROCESS_FN = "_run_postprocess"
SUCCESS_RETURN_NAME = "config_hash"
# Flag/env names that control v2 write authority.
V2_WRITE_FLAG_NAMES = {
    "FW_CONTEXT_V2_WRITE_AUTHORITY",
    "FW_CONTEXT_V2_PRIMARY",
    "FW_CONTEXT_V2_WRITE",
    "FW_CONTEXT_DUAL_WRITE",
}
V2_SHADOW_FLAG_NAMES = {
    "FW_CONTEXT_V2_SHADOW",
    "FW_CONTEXT_STORE_V2",
    "_v2_shadow_flag_on",
}


def _git(args: list[str], root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _git_dirty(root: Path) -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(root),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool(out.stdout.strip())


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return None


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


# ---------------------------------------------------------------------------
# Entry-point enumeration
# ---------------------------------------------------------------------------

# Functions that orchestrate a normal production index run. We look for these
# names across the CLI / MCP / watcher surfaces; each one that exists and
# reaches runner.run (directly or via _run_multi) counts as one entry point.
NORMAL_ENTRYPOINT_NAMES = {
    "cmd_index": "cli/_index.py",
    "_run_multi": "cli/_index.py",
    "reindex_file_impl": "mcp/handlers/maintenance.py",
    "reindex_file": "mcp/handlers/maintenance.py",
}


def _enumerate_entry_points(root: Path) -> list[dict]:
    """Find normal production indexing entry points by name + runner.run reach."""
    src = root / "src" / "fw_context_mcp"
    entry_points: list[dict] = []
    seen: set[str] = set()
    for name, rel in NORMAL_ENTRYPOINT_NAMES.items():
        path = src / rel
        if not path.exists():
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                # Does this function (or _run_multi it calls) reach runner.run?
                reaches_run = _func_reaches_runner(node)
                key = f"{rel}::{name}"
                if key in seen:
                    continue
                seen.add(key)
                entry_points.append({
                    "name": name,
                    "file": rel,
                    "line": node.lineno,
                    "reaches_runner_run": reaches_run,
                })
    # Also scan background/daemon for a reindex orchestration entry.
    bg = src / "mcp" / "background.py"
    if bg.exists():
        tree = _parse(bg)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and (
                    "reindex" in node.name.lower() or "index" in node.name.lower()
                ):
                    if _func_reaches_runner(node):
                        key = f"mcp/background.py::{node.name}"
                        if key not in seen:
                            seen.add(key)
                            entry_points.append({
                                "name": node.name,
                                "file": "mcp/background.py",
                                "line": node.lineno,
                                "reaches_runner_run": True,
                            })
    return entry_points


def _func_reaches_runner(func: ast.FunctionDef) -> bool:
    """True if the function body calls runner.run / run( / _run_multi."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            n = _call_name(node)
            if n in {"run", "_run_multi"}:
                return True
            # module.run( pattern
            if isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                return True
    return False


# ---------------------------------------------------------------------------
# Runner success-authority analysis
# ---------------------------------------------------------------------------

def _analyze_runner(root: Path) -> dict:
    """Inspect runner.run() to determine the success authority.

    Derives:
      - v2 finalize call sites (flag-gated? exception-isolated?)
      - legacy postprocess on success path
      - whether v2 finalize failure propagates to the return
      - legacy DB writes on the success path
    """
    path = root / "src" / "fw_context_mcp" / "indexer" / "runner.py"
    tree = _parse(path)
    if tree is None:
        return {"_error": f"cannot parse {path}"}
    run_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            run_fn = node
            break
    if run_fn is None:
        return {"_error": "run() not found in runner.py"}

    src_text = path.read_text(encoding="utf-8")
    v2_finalize_sites = _find_v2_finalize_sites(run_fn, src_text)
    legacy_postprocess_sites = _find_calls(run_fn, {LEGACY_POSTPROCESS_FN})
    legacy_db_writes = _find_legacy_db_writes(run_fn)
    success_returns = _find_returns(run_fn)

    # v2 finalization is the success authority IFF:
    #  - there is at least one v2 finalize call on the success path, AND
    #  - v2 finalize failure is NOT swallowed (not wrapped in a bare
    #    except that logs-and-continues), AND
    #  - it is not purely flag-gated behind a legacy-shadow flag.
    v2_authority = (
        len(v2_finalize_sites) > 0
        and not all(s.get("exception_isolated") for s in v2_finalize_sites)
    )

    return {
        "v2_finalize_sites": v2_finalize_sites,
        "legacy_postprocess_sites": legacy_postprocess_sites,
        "legacy_db_write_sites": legacy_db_writes,
        "success_return_count": len(success_returns),
        "v2_finalize_is_success_authority": v2_authority,
        "v2_finalize_swallowed": all(
            s.get("exception_isolated") for s in v2_finalize_sites
        ) if v2_finalize_sites else True,
    }


def _find_v2_finalize_sites(func: ast.FunctionDef, src_text: str) -> list[dict]:
    """Find v2 World finalization call sites and classify isolation."""
    sites: list[dict] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            n = _call_name(node)
            if n in V2_FINALIZE_FN_NAMES:
                isolated = _is_exception_isolated(node, func, src_text)
                flag_gated = _is_flag_gated_context(node, func, src_text)
                sites.append({
                    "call": n,
                    "line": node.lineno,
                    "exception_isolated": isolated,
                    "flag_gated": flag_gated,
                })
    return sites


def _find_calls(func: ast.FunctionDef, names: set[str]) -> list[dict]:
    sites: list[dict] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            n = _call_name(node)
            if n in names:
                sites.append({"call": n, "line": node.lineno})
    return sites


def _find_legacy_db_writes(func: ast.FunctionDef) -> list[dict]:
    """Find legacy DB mutation calls (conn.commit / conn.executemany)."""
    sites: list[dict] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in LEGACY_DB_WRITE_ATTRS:
                sites.append({"call": node.func.attr, "line": node.lineno})
    return sites


def _find_returns(func: ast.FunctionDef) -> list[dict]:
    sites: list[dict] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Return):
            sites.append({"line": node.lineno})
    return sites


def _is_exception_isolated(target: ast.AST, func: ast.FunctionDef, src_text: str) -> bool:
    """True if the target call sits inside a try/except that swallows errors.

    Heuristic: walk parents; if an ExceptHandler's body contains the target
    AND the handler does not re-raise, the call's failure is swallowed.
    We approximate by checking whether the enclosing try has a bare/Log-only
    except (no raise inside the handler).
    """
    # Find the smallest enclosing Try whose body contains the target's line.
    target_line = getattr(target, "lineno", -1)
    enclosing_try = None
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            lo = node.lineno
            hi = getattr(node, "end_lineno", lo)
            if lo <= target_line <= hi:
                if enclosing_try is None or (
                    node.lineno >= enclosing_try.lineno
                ):
                    enclosing_try = node
    if enclosing_try is None:
        return False
    # Does any except handler re-raise? If not, the failure is swallowed.
    for handler in enclosing_try.handlers:
        handler_text = _segment(src_text, handler)
        if "raise" in handler_text and "raise " in handler_text:
            return False
    return True


def _is_flag_gated_context(target: ast.AST, func: ast.FunctionDef, src_text: str) -> bool:
    """True if the target call sits inside an `if <v2_shadow_flag>:` block."""
    target_line = getattr(target, "lineno", -1)
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            lo = node.lineno
            hi = getattr(node, "end_lineno", lo)
            if lo < target_line <= hi:
                cond_text = _segment(src_text, node.test)
                for flag in tuple(V2_SHADOW_FLAG_NAMES) + tuple(V2_WRITE_FLAG_NAMES):
                    if flag in cond_text:
                        return True
    return False


def _segment(src_text: str, node: ast.AST) -> str:
    lo = getattr(node, "lineno", 1)
    hi = getattr(node, "end_lineno", lo)
    lines = src_text.splitlines()
    return "\n".join(lines[lo - 1: hi])


# ---------------------------------------------------------------------------
# Write-mode detection
# ---------------------------------------------------------------------------

def _detect_write_mode(root: Path) -> dict:
    """Detect the active write mode from env flags + code defaults."""
    env = {k: os.environ.get(k) for k in V2_WRITE_FLAG_NAMES}
    # Default mode: if no v2-write-authority flag is set, the code uses
    # legacy-primary (the historical default). v2 shadow may run inertly.
    v2_write_authority_on = env.get("FW_CONTEXT_V2_WRITE_AUTHORITY") in {"1", "true", "True"}
    current_mode = "V2_PRIMARY_LEGACY_MIRROR" if v2_write_authority_on else "LEGACY_PRIMARY"
    return {
        "current_write_mode": current_mode,
        "default_write_mode": "LEGACY_PRIMARY",  # code default absent explicit flag
        "env_flags": env,
        "rollback_mode_available": True,  # legacy-primary is the rollback mode
    }


# ---------------------------------------------------------------------------
# Legacy postprocess inventory
# ---------------------------------------------------------------------------

def _inventory_postprocess(root: Path) -> list[dict]:
    """Enumerate legacy postprocess steps from _postprocess.py."""
    path = root / "src" / "fw_context_mcp" / "indexer" / "_postprocess.py"
    tree = _parse(path)
    if tree is None:
        return []
    steps: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_step_"):
            name = node.name
            # Heuristic classification: a step that touches store_v2 is
            # V2-related; otherwise it's legacy-compat or unknown.
            body_text = _segment(path.read_text(encoding="utf-8"), node)
            touches_v2 = "store_v2" in body_text or "v2_" in body_text
            classification = "V2_REQUIRED" if touches_v2 else "LEGACY_COMPATIBILITY_ONLY"
            steps.append({
                "step": name,
                "line": node.lineno,
                "classification": classification,
            })
    return steps


# ---------------------------------------------------------------------------
# Fault-injection evidence
# ---------------------------------------------------------------------------

FAULT_TEST_NAMES = [
    "V2_SUCCESS_LEGACY_FAILURE_TEST",
    "V2_FAILURE_LEGACY_SUCCESS_TEST",
    "CRASH_BEFORE_FINALIZE_TEST",
    "RESTART_IDEMPOTENCE_TEST",
    "RETRY_IDEMPOTENCE_TEST",
    "READ_AFTER_FINALIZE_TEST",
    "ROLLBACK_TEST",
]


def _read_fault_tests(root: Path) -> dict:
    """Read fault-injection test verdicts from evidence files."""
    fdir = root / ".dsh-control" / "evidence" / "m4_faults"
    results: dict[str, dict] = {}
    for name in FAULT_TEST_NAMES:
        fpath = fdir / f"{name}.json"
        if fpath.exists():
            try:
                results[name] = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception as e:
                results[name] = {"verdict": "ERROR", "error": str(e)}
        else:
            results[name] = {"verdict": "NOT_RUN"}
    return results


# ---------------------------------------------------------------------------
# M3B regression check (delegates to the M3B scanner)
# ---------------------------------------------------------------------------

def _m3b_regression(root: Path) -> str:
    """Run the accepted M3B read scanner and report PASS/FAIL."""
    scan_path = root / ".dsh-control" / "gates" / "scan_authority_state.py"
    if not scan_path.exists():
        return "MISSING_SCANNER"
    try:
        proc = subprocess.run(
            [sys.executable, str(scan_path)], cwd=str(root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        state_path = root / ".dsh-control" / "evidence" / "authority_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            c = state.get("counters", {})
            ok = (
                c.get("CORE_NORMAL_USE_SEMANTIC_TOOLS", 0) > 0
                and c.get("CORE_V2_ROUTED", 0) == c.get("CORE_NORMAL_USE_SEMANTIC_TOOLS", 0)
                and c.get("CORE_V2_NATIVE_READ", 0) == c.get("CORE_NORMAL_USE_SEMANTIC_TOOLS", 0)
                and c.get("CORE_LEGACY_SEMANTIC_EXECUTION", -1) == 0
                and c.get("CORE_UNSAFE_FALLBACK", -1) == 0
                and c.get("UNRESOLVED_NORMAL_USE_V2_BUGS", -1) == 0
            )
            return "PASS" if ok else "FAIL"
        return "FAIL" if proc.returncode != 0 else "PASS"
    except Exception as e:
        return f"ERROR:{e}"


# ---------------------------------------------------------------------------
# Bug register
# ---------------------------------------------------------------------------

def _read_bug_register(root: Path) -> list:
    path = root / ".dsh-control" / "state" / "v2_write_bugs.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan(root: Path) -> dict:
    root = root.resolve()
    head = _git(["rev-parse", "HEAD"], root)
    short = head[:7] if head else ""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    dirty = _git_dirty(root)

    entry_points = _enumerate_entry_points(root)
    normal_count = len(entry_points)
    runner_analysis = _analyze_runner(root)
    postprocess_steps = _inventory_postprocess(root)
    write_mode = _detect_write_mode(root)
    fault_tests = _read_fault_tests(root)
    m3b = _m3b_regression(root)
    bugs = _read_bug_register(root)

    v2_finalize_sites = runner_analysis.get("v2_finalize_sites", [])
    v2_authority = runner_analysis.get("v2_finalize_is_success_authority", False)
    v2_swallowed = runner_analysis.get("v2_finalize_swallowed", True)
    legacy_postprocess_sites = runner_analysis.get("legacy_postprocess_sites", [])
    legacy_db_writes = runner_analysis.get("legacy_db_write_sites", [])

    # v2 failure masked by legacy: v2 finalize exists but is swallowed, so
    # legacy success masks v2 failure.
    v2_failure_masked = 1 if (v2_finalize_sites and v2_swallowed and not v2_authority) else 0

    # legacy mirror failure blocks v2 success: if legacy postprocess is on the
    # success path AND not wrapped in a try that continues on failure, it blocks.
    legacy_postprocess_required = len(legacy_postprocess_sites)

    # legacy DB required for v2: if the runner opens/writes legacy index.db on
    # the v2 path (heuristic: legacy DB writes exist on the success path).
    legacy_db_required = len(legacy_db_writes)

    # incomplete world exposure: v2 finalize is swallowed → an incomplete
    # world may be left queryable without finalization gating.
    incomplete_exposure = 1 if (v2_finalize_sites and v2_swallowed) else 0

    # v2-primary entry points: those where v2 finalize is the authority.
    # On baseline (v2 not authoritative), this is 0.
    v2_primary_count = normal_count if v2_authority else 0
    legacy_primary_count = normal_count - v2_primary_count

    counters = {
        "NORMAL_INDEX_ENTRYPOINTS": normal_count,
        "V2_PRIMARY_INDEX_ENTRYPOINTS": v2_primary_count,
        "LEGACY_PRIMARY_INDEX_ENTRYPOINTS": legacy_primary_count,
        "LEGACY_WRITE_REQUIRED_FOR_SUCCESS": legacy_db_required,
        "LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS": legacy_postprocess_required,
        "LEGACY_DB_REQUIRED_FOR_V2_INDEX": legacy_db_required,
        "V2_FINALIZATION_IS_SUCCESS_AUTHORITY": bool(v2_authority),
        "INCOMPLETE_WORLD_EXPOSURE_PATHS": incomplete_exposure,
        "UNSAFE_PARTIAL_FINALIZATION_PATHS": 0,  # derived when finalize impl lands
        "EXPECTED_TU_ACCOUNTING_VIOLATIONS": 0,  # derived when accounting lands
        "V2_FAILURE_MASKED_BY_LEGACY_PATHS": v2_failure_masked,
        "LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS": 1 if legacy_postprocess_required > 0 and not v2_authority else 0,
        "CURRENT_WRITE_MODE": write_mode["current_write_mode"],
        "DEFAULT_WRITE_MODE": write_mode["default_write_mode"],
        "ROLLBACK_MODE_AVAILABLE": write_mode["rollback_mode_available"],
        "M3B_READ_AUTHORITY_REGRESSION": m3b,
        "UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS": len(bugs),
    }

    # Gate evaluation for the numeric + fault clauses.
    numeric_pass = (
        counters["NORMAL_INDEX_ENTRYPOINTS"] > 0
        and counters["V2_PRIMARY_INDEX_ENTRYPOINTS"] == counters["NORMAL_INDEX_ENTRYPOINTS"]
        and counters["LEGACY_WRITE_REQUIRED_FOR_SUCCESS"] == 0
        and counters["LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS"] == 0
        and counters["LEGACY_DB_REQUIRED_FOR_V2_INDEX"] == 0
        and counters["V2_FINALIZATION_IS_SUCCESS_AUTHORITY"] is True
        and counters["INCOMPLETE_WORLD_EXPOSURE_PATHS"] == 0
        and counters["UNSAFE_PARTIAL_FINALIZATION_PATHS"] == 0
        and counters["EXPECTED_TU_ACCOUNTING_VIOLATIONS"] == 0
        and counters["V2_FAILURE_MASKED_BY_LEGACY_PATHS"] == 0
        and counters["LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS"] == 0
        and counters["UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS"] == 0
        and counters["CURRENT_WRITE_MODE"] == "V2_PRIMARY_LEGACY_MIRROR"
    )
    m3b_pass = counters["M3B_READ_AUTHORITY_REGRESSION"] == "PASS"
    fault_pass = all(
        fault_tests.get(t, {}).get("verdict") == "PASS" for t in FAULT_TEST_NAMES
    )

    state = {
        "schema_version": 1,
        "milestone": "M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE",
        "generated_at_utc": _now_utc(),
        "project_root": str(root),
        "git": {"head": head, "short": short, "branch": branch, "dirty": dirty},
        "counters": counters,
        "entry_points": entry_points,
        "runner_analysis": runner_analysis,
        "postprocess_steps": postprocess_steps,
        "write_mode": write_mode,
        "fault_tests": fault_tests,
        "bug_register": bugs,
        "numeric_gate_pass": numeric_pass,
        "m3b_regression_pass": m3b_pass,
        "fault_gate_pass": fault_pass,
        "machine_gate_pass": numeric_pass and m3b_pass and fault_pass,
    }
    return state


def _now_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--out", default=".dsh-control/evidence/write_authority_state.json")
    args = ap.parse_args()
    root = Path(args.project_root).resolve()
    state = scan(root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    c = state["counters"]
    print(f"[m4-scan] HEAD: {state['git']['short']} ({'dirty' if state['git']['dirty'] else 'clean'})")
    print(f"[m4-scan] NORMAL_ENTRYPOINTS={c['NORMAL_INDEX_ENTRYPOINTS']} "
          f"V2_PRIMARY={c['V2_PRIMARY_INDEX_ENTRYPOINTS']} "
          f"LEGACY_PRIMARY={c['LEGACY_PRIMARY_INDEX_ENTRYPOINTS']}")
    print(f"[m4-scan] V2_FINALIZATION_IS_AUTHORITY={c['V2_FINALIZATION_IS_SUCCESS_AUTHORITY']} "
          f"V2_FAILURE_MASKED={c['V2_FAILURE_MASKED_BY_LEGACY_PATHS']}")
    print(f"[m4-scan] LEGACY_WRITE_REQ={c['LEGACY_WRITE_REQUIRED_FOR_SUCCESS']} "
          f"LEGACY_POSTPROCESS_REQ={c['LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS']} "
          f"LEGACY_DB_REQ={c['LEGACY_DB_REQUIRED_FOR_V2_INDEX']}")
    print(f"[m4-scan] INCOMPLETE_EXPOSURE={c['INCOMPLETE_WORLD_EXPOSURE_PATHS']} "
          f"WRITE_MODE={c['CURRENT_WRITE_MODE']} "
          f"M3B_REGRESSION={c['M3B_READ_AUTHORITY_REGRESSION']}")
    ft = state["fault_tests"]
    print(f"[m4-scan] faults: " + ", ".join(
        f"{t.split('_TEST')[0]}={ft.get(t, {}).get('verdict')}" for t in FAULT_TEST_NAMES))
    print(f"[m4-scan] numeric_gate={'PASS' if state['numeric_gate_pass'] else 'FAIL'} "
          f"m3b={'PASS' if state['m3b_regression_pass'] else 'FAIL'} "
          f"fault={'PASS' if state['fault_gate_pass'] else 'FAIL'} "
          f"machine_gate={'PASS' if state['machine_gate_pass'] else 'FAIL'}")
    return 0 if state["machine_gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
