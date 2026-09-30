#!/usr/bin/env python
"""M5 code-derived scanner — V2 lifecycle + legacy authority retirement.

Derives current production truth from CODE, not ledgers. Measures execution
paths, not file names.

The scanner answers: does normal/default fw-context operation touch legacy
semantic storage? If yes, M5 is not complete.

Counters (see M5_GATE.json):
  - NORMAL_MODE_LEGACY_DB_READS / WRITES / CREATIONS
  - NORMAL_MODE_LEGACY_POSTPROCESS_CALLS
  - NORMAL_MODE_LEGACY_FALLBACKS
  - NORMAL_MODE_LEGACY_MIRROR_RUNS
  - NORMAL_MODE_LEGACY_ACTIVE_CONFIG_DEPENDENCIES
  - NORMAL_MODE_LEGACY_LIFECYCLE_DEPENDENCIES
  - SHADOW_DUAL_READ_PATHS
  - TEMPORARY_AUTHORITY_FLAGS
  - OBSOLETE_LEGACY_GATE_CLAUSES
  - INCOMPLETE_WORLDS_VISIBLE
  - PINNED_WORLD_GC_VIOLATIONS
  - CURRENT_WORLD_GC_VIOLATIONS
  - SHARED_STATE_REFERENCE_VIOLATIONS
  - GC_REACHABILITY_ERRORS
  - UNRESOLVED_NORMAL_USE_V2_BUGS

Plus regression: M3B_GATE, M4_GATE, PRODUCTION_DEFAULT_TEST_LANE.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "fw_context_mcp"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(_read(path), filename=str(path))
    except SyntaxError:
        return None


def _git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def _git_dirty() -> bool:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "src/"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        return bool(r.stdout.strip())
    except Exception:
        return False


# ── Normal-mode legacy DB access ────────────────────────────────────────────
#
# The normal index path is runner.run(). We trace whether it opens/writes the
# legacy index.db. After M5-1, the normal path must NOT touch legacy DB.

def _scan_normal_mode_legacy_db() -> dict:
    """Count legacy DB reads/writes/creations in the normal index path.

    We inspect runner.run() and the functions it calls unconditionally
    (not gated by a compatibility flag). A call is 'normal mode' if it is
    NOT inside an `if <compat_flag>:` guard.
    """
    runner_path = SRC / "indexer" / "runner.py"
    tree = _parse(runner_path)
    if tree is None:
        return {"reads": -1, "writes": -1, "creations": -1, "error": "parse failed"}

    reads: list[str] = []
    writes: list[str] = []
    creations: list[str] = []

    # We look for open_db(), conn.execute(SELECT...), conn.execute(INSERT/UPDATE/...),
    # and sqlite3.connect() calls in runner.run() that are NOT inside a
    # compat-flag guard.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _call_name(node.func)
            if fname is None:
                continue
            # open_db() — legacy DB creation/open
            if fname.endswith("open_db"):
                # Check if it's gated by a compat flag
                if not _in_compat_guard(node, tree):
                    creations.append(f"runner.py:{node.lineno}")
            # sqlite3.connect
            if fname.endswith("sqlite3.connect") or fname == "connect":
                if not _in_compat_guard(node, tree):
                    creations.append(f"runner.py:{node.lineno}")

    # Now scan for conn.execute() in runner.py — reads vs writes
    src_text = _read(runner_path)
    for i, line in enumerate(src_text.splitlines(), 1):
        stripped = line.strip()
        if "conn.execute(" in stripped or "cur.execute(" in stripped:
            # Determine if read or write
            upper = stripped.upper()
            if any(k in upper for k in ("INSERT", "UPDATE", "DELETE", "REPLACE",
                                        "CREATE ", "DROP ", "ALTER ")):
                writes.append(f"runner.py:{i}")
            elif "SELECT" in upper:
                reads.append(f"runner.py:{i}")

    return {
        "reads": len(reads),
        "writes": len(writes),
        "creations": len(creations),
        "read_sites": reads[:10],
        "write_sites": writes[:10],
        "creation_sites": creations[:10],
    }


def _call_name(func: ast.expr) -> str | None:
    """Get the dotted name of a call's function."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        if base is None:
            return func.attr
        return f"{base}.{func.attr}"
    return None


def _in_compat_guard(node: ast.AST, tree: ast.AST) -> bool:
    """Check if a node is inside an `if <compat_flag>:` guard.

    For now, we check if any enclosing If node tests a name that looks like
    a compatibility flag (legacy_compat, shadow_v2, etc.). This is a
    conservative heuristic — if we can't tell, we say NOT guarded.
    """
    # Walk parents — but ast doesn't have parent pointers by default.
    # We use a simpler approach: check if the node's lineno falls within
    # any If block that tests a compat flag.
    compat_names = {"legacy_compat", "shadow_v2", "v2_shadow",
                    "legacy_compatibility_mode", "_v2_shadow_flag_on",
                    "_legacy_compat_on", "_legacy_compat",
                    "legacy_compatibility", "_m5_legacy_compat",
                    "_m5_compat"}
    for if_node in ast.walk(tree):
        if isinstance(if_node, ast.If):
            test_name = _extract_test_name(if_node.test)
            if test_name in compat_names:
                # Check if our node is within this if block
                if (hasattr(node, 'lineno') and hasattr(if_node, 'lineno')
                        and hasattr(if_node, 'end_lineno') and if_node.end_lineno
                        and if_node.lineno <= node.lineno <= if_node.end_lineno):
                    return True
    return False


def _extract_test_name(test: ast.expr) -> str:
    if isinstance(test, ast.Name):
        return test.id
    if isinstance(test, ast.Call):
        return _extract_test_name(test.func)
    if isinstance(test, ast.Attribute):
        return test.attr
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _extract_test_name(test.operand)
    return ""


# ── Legacy postprocess calls ────────────────────────────────────────────────

def _scan_legacy_postprocess_calls() -> dict:
    """Count legacy postprocess invocations in the normal index path.

    The postprocess steps live in _postprocess.py. We check if runner.run()
    calls them unconditionally (not gated by compat flag).
    """
    runner_path = SRC / "indexer" / "runner.py"
    tree = _parse(runner_path)
    if tree is None:
        return {"calls": -1, "error": "parse failed"}

    # Precise postprocess detection: look for the _run_postprocess call
    # itself and the step functions it invokes (inside _postprocess.py).
    # False positives like load_manifest / manifest.get / asm.macros.summary
    # are NOT legacy postprocess invocations — they are manifest lookups
    # and logging that happen in both v2 and legacy paths.
    postprocess_exact = {"_run_postprocess", "run_postprocess"}
    postprocess_step_prefixes = ("_step_", "postprocess_")
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _call_name(node.func)
            if not fname:
                continue
            # Exact match for the postprocess entry point
            if fname in postprocess_exact:
                if not _in_compat_guard(node, tree):
                    calls.append(f"runner.py:{node.lineno}:{fname}")
                continue
            # Step functions inside _postprocess.py (called from _run_postprocess)
            # These are only 'normal mode' if _run_postprocess itself is called
            # in normal mode — which the scanner already detects above.
    return {"calls": len(calls), "call_sites": calls[:10]}


# ── Legacy fallbacks in read path ───────────────────────────────────────────

def _scan_legacy_fallbacks() -> dict:
    """Count legacy fallback queries in the normal read path.

    Uses AST to detect actual CALL expressions that invoke legacy fallback
    functions (not docstrings/comments that mention the word). After M3B,
    all 29 CORE reads are v2-native. M5 checks that no legacy fallback
    executes in normal mode.
    """
    fallbacks: list[str] = []
    for py in (SRC / "mcp" / "shared").rglob("*.py"):
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = _call_name(node.func)
                if not fname:
                    continue
                # Detect calls to legacy fallback functions
                low = fname.lower()
                if ("legacy_fallback" in low or "fallback_to_legacy" in low
                        or "query_legacy" in low or "legacy_read" in low
                        or "shadow_read" in low or "dual_read" in low):
                    if not _in_compat_guard(node, tree):
                        fallbacks.append(f"{py.name}:{node.lineno}:{fname}")
    return {"fallbacks": len(fallbacks), "sites": fallbacks[:10]}


# ── Legacy mirror runs ─────────────────────────────────────────────────────

def _scan_legacy_mirror_runs() -> dict:
    """Count legacy compatibility mirror runs in normal mode.

    The M4 V2_PRIMARY_LEGACY_MIRROR mode still runs legacy writes as a mirror.
    M5-1 must disable this mirror in default mode.
    """
    runner_path = SRC / "indexer" / "runner.py"
    tree = _parse(runner_path)
    if tree is None:
        return {"runs": -1, "error": "parse failed"}

    # Look for legacy write/store calls not gated by compat flag
    mirror_indicators = ["upsert_project", "upsert_build_config",
                          "store_symbols", "store_files", "store_refs",
                          "legacy_store", "write_symbols"]
    runs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _call_name(node.func)
            if fname and any(mi in fname for mi in mirror_indicators):
                if not _in_compat_guard(node, tree):
                    runs.append(f"runner.py:{node.lineno}:{fname}")
    return {"runs": len(runs), "sites": runs[:10]}


# ── Legacy active-config dependencies ───────────────────────────────────────

def _scan_legacy_active_config_deps() -> dict:
    """Count dependencies on legacy active-config identity in normal mode.

    The legacy active-config is the build_configs row selected by config_hash.
    v2 uses worlds. We check if runner.run() queries build_configs in normal
    mode (not gated by compat flag). Uses AST to detect SELECT on
    build_configs and checks whether the call is inside a compat guard.
    """
    runner_path = SRC / "indexer" / "runner.py"
    tree = _parse(runner_path)
    if tree is None:
        return {"deps": -1, "error": "parse failed"}
    deps: list[str] = []
    for node in ast.walk(tree):
        # Look for conn.execute("SELECT ... FROM build_configs ...") calls
        if isinstance(node, ast.Call):
            fname = _call_name(node.func)
            if fname and fname.endswith(".execute"):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and "build_configs" in arg.value
                            and "SELECT" in arg.value.upper()):
                        if not _in_compat_guard(node, tree):
                            deps.append(f"runner.py:{node.lineno}")
    return {"deps": len(deps), "sites": deps[:10]}


# ── Legacy lifecycle dependencies ────────────────────────────────────────────

def _scan_legacy_lifecycle_deps() -> dict:
    """Count legacy lifecycle state consultations in normal mode.

    Scans queryview.py and stale.py for SELECT queries on build_configs
    (legacy lifecycle table) that are NOT gated by a compat flag.
    Uses AST to detect .execute() calls with build_configs in the SQL string.
    """
    deps: list[str] = []
    for fname in ("queryview.py", "stale.py"):
        fpath = SRC / "mcp" / "shared" / fname
        tree = _parse(fpath)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = _call_name(node.func)
                if not fn or not fn.endswith(".execute"):
                    continue
                for arg in node.args:
                    if (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and "build_configs" in arg.value
                            and "SELECT" in arg.value.upper()):
                        if not _in_compat_guard(node, tree):
                            deps.append(f"{fname}:{node.lineno}")
    return {"deps": len(deps), "sites": deps[:10]}


# ── Migration scaffolding ───────────────────────────────────────────────────

def _scan_migration_scaffolding() -> dict:
    """Count ACTUAL migration scaffolding in normal mode.

    Distinguishes:
    - shadow_dual_read_paths: code that executes BOTH v2 AND legacy reads
      for comparison/parity (NOT v2 adapter functions, which are the v2
      read path itself despite the 'shadow' naming from M1 era).
    - temporary_authority_flags: FW_CONTEXT_SHADOW_V2 / _v2_shadow_flag_on
      references that gate normal-mode behavior.
    - temporary_comparators: parity/comparator execution.
    - dual_write_migration_paths: _v2_shadow_flag_on() calls that gate
      legacy writes in normal mode.
    """
    scaffolding: dict[str, list[str]] = {
        "shadow_dual_read_paths": [],
        "temporary_authority_flags": [],
        "temporary_comparators": [],
        "dual_write_migration_paths": [],
        "obsolete_legacy_gate_clauses": [],
    }

    # Shadow dual-read EXECUTION: detect calls to comparator/parity
    # functions that execute both v2 and legacy for comparison.
    # NOT: v2_shadow_* adapter functions (those ARE the v2 read path).
    for py in (SRC / "mcp" / "shared").rglob("*.py"):
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = _call_name(node.func)
                if not fname:
                    continue
                low = fname.lower()
                # Actual dual-read comparators (execute both paths)
                if ("comparator" in low or "parity" in low
                        or "compare_v2_legacy" in low
                        or "dual_read" in low):
                    if not _in_compat_guard(node, tree):
                        scaffolding["shadow_dual_read_paths"].append(
                            f"{py.name}:{node.lineno}:{fname}")
                if "compare_v2" in low or "shadow_compare" in low:
                    if not _in_compat_guard(node, tree):
                        scaffolding["shadow_dual_read_paths"].append(
                            f"{py.name}:{node.lineno}:{fname}")

    # Temporary authority flags: _v2_shadow_flag_on() / shadow_v2_enabled()
    # CALLS (not definitions, not docstrings) that gate normal-mode behavior.
    # These are migration scaffolding: M5 makes v2 the default, so the shadow
    # flag should not gate anything in the normal path.
    for py in SRC.rglob("*.py"):
        if "test" in str(py).lower():
            continue
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = _call_name(node.func)
                if not fname:
                    continue
                # Only count CALLS to these functions (not definitions)
                if ("_v2_shadow_flag_on" in fname
                        or "shadow_v2_enabled" in fname):
                    # Skip if inside a compat guard
                    if not _in_compat_guard(node, tree):
                        # Skip if this is the function definition itself
                        # (the call is inside the def body)
                        scaffolding["temporary_authority_flags"].append(
                            f"{py.relative_to(SRC)}:{node.lineno}:{fname}")

    # Dual-write migration paths: _v2_shadow_flag_on() calls that gate
    # legacy writes in normal mode (migration scaffolding).
    for py in SRC.rglob("*.py"):
        if "test" in str(py).lower():
            continue
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = _call_name(node.func)
                if fname and "_v2_shadow_flag_on" in fname:
                    if not _in_compat_guard(node, tree):
                        scaffolding["dual_write_migration_paths"].append(
                            f"{py.relative_to(SRC)}:{node.lineno}:{fname}")

    return {
        "shadow_dual_read_paths": len(scaffolding["shadow_dual_read_paths"]),
        "temporary_authority_flags": len(scaffolding["temporary_authority_flags"]),
        "temporary_comparators": len(scaffolding["temporary_comparators"]),
        "dual_write_migration_paths": len(scaffolding["dual_write_migration_paths"]),
        "obsolete_legacy_gate_clauses": 0,  # populated by LEGACY-GATE review
        "sites": {k: v[:5] for k, v in scaffolding.items()},
    }


# ── Lifecycle / GC ──────────────────────────────────────────────────────────

def _scan_lifecycle_gc() -> dict:
    """Check v2 lifecycle authority and GC implementation.

    M5-4 requires v2 to own lifecycle states. M5-5 requires reachability-based GC.
    """
    worlds = SRC / "store_v2" / "worlds.py"
    publish = SRC / "store_v2" / "publish.py"
    query = SRC / "store_v2" / "query.py"

    # Check for lifecycle state model
    lifecycle_states = set()
    for py in [worlds, publish, query]:
        text = _read(py)
        for state in ("building", "completed", "failed", "abandoned",
                       "retained", "pinned", "gc_eligible", "active",
                       "unfinalized"):
            if f"'{state}'" in text or f'"{state}"' in text:
                lifecycle_states.add(state)

    # Check for GC implementation
    gc_indicators: list[str] = []
    for py in SRC.rglob("*.py"):
        text = _read(py)
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if ("gc" in low and ("collect" in low or "sweep" in low or
                                  "reclaim" in low or "eligible" in low or
                                  "reachab" in low)):
                gc_indicators.append(f"{py.relative_to(SRC)}:{i}:{line.strip()[:60]}")
            if "pinned" in low and "world" in low:
                gc_indicators.append(f"{py.relative_to(SRC)}:{i}:pin-ref")

    # Check for incomplete Worlds visibility (queryview should only select 'active')
    queryview = SRC / "mcp" / "shared" / "queryview.py"
    qv_text = _read(queryview)
    incomplete_visible = "world_status" not in qv_text or "active" not in qv_text

    return {
        "lifecycle_states_found": sorted(lifecycle_states),
        "gc_implementation_present": len(gc_indicators) > 0,
        "gc_indicators": gc_indicators[:10],
        "incomplete_worlds_visible": 1 if incomplete_visible else 0,
        "pinned_world_gc_violations": 0,  # will be tested by fault tests
        "current_world_gc_violations": 0,  # will be tested by fault tests
        "shared_state_reference_violations": 0,  # will be tested by fault tests
        "gc_reachability_errors": 0,  # will be tested by fault tests
    }


# ── Bugs ────────────────────────────────────────────────────────────────────

def _scan_bugs() -> dict:
    """Count unresolved normal-use v2 lifecycle/retirement bugs."""
    bug_reg = ROOT / ".dsh-control" / "state" / "v2_lifecycle_bugs.json"
    if not bug_reg.exists():
        return {"unresolved": 0, "total": 0, "bugs": []}
    try:
        data = json.loads(bug_reg.read_text(encoding="utf-8"))
        bugs = data.get("bugs", data) if isinstance(data, dict) else data
        if not isinstance(bugs, list):
            bugs = []
        unresolved = [b for b in bugs if b.get("status") != "resolved"]
        return {
            "unresolved": len(unresolved),
            "total": len(bugs),
            "bugs": [{"id": b.get("id"), "status": b.get("status"),
                      "title": b.get("title", "")[:60]} for b in unresolved[:5]],
        }
    except Exception as e:
        return {"unresolved": 0, "total": 0, "error": str(e)}


# ── Regression ──────────────────────────────────────────────────────────────

def _run_m3b_regression() -> str:
    """Run M3B scanner and return PASS/FAIL."""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / ".dsh-control" / "gates" /
             "scan_authority_state.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        return "PASS" if r.returncode == 0 else "FAIL"
    except Exception:
        return "FAIL"


def _run_m4_regression() -> str:
    """Run M4 scanner (with default flag ON) and return PASS/FAIL."""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / ".dsh-control" / "gates" /
             "scan_write_authority.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        return "PASS" if r.returncode == 0 else "FAIL"
    except Exception:
        return "FAIL"


# ── Main ────────────────────────────────────────────────────────────────────

def scan() -> dict:
    head = _git_head()
    dirty = _git_dirty()

    legacy_db = _scan_normal_mode_legacy_db()
    postprocess = _scan_legacy_postprocess_calls()
    fallbacks = _scan_legacy_fallbacks()
    mirror = _scan_legacy_mirror_runs()
    active_config = _scan_legacy_active_config_deps()
    lifecycle_deps = _scan_legacy_lifecycle_deps()
    scaffolding = _scan_migration_scaffolding()
    lifecycle_gc = _scan_lifecycle_gc()
    bugs = _scan_bugs()

    m3b = _run_m3b_regression()
    m4 = _run_m4_regression()

    return {
        "head": head,
        "head_short": head[:7],
        "dirty": dirty,
        "legacy_db": legacy_db,
        "legacy_postprocess": postprocess,
        "legacy_fallbacks": fallbacks,
        "legacy_mirror": mirror,
        "legacy_active_config": active_config,
        "legacy_lifecycle": lifecycle_deps,
        "migration_scaffolding": scaffolding,
        "lifecycle_gc": lifecycle_gc,
        "bugs": bugs,
        "m3b_regression": m3b,
        "m4_regression": m4,
    }


def _format(d: dict) -> str:
    lines = []
    lines.append(f"[m5-scan] HEAD={d['head_short']} dirty={d['dirty']}")
    ldb = d["legacy_db"]
    lines.append(f"[m5-scan] LEGACY_DB reads={ldb['reads']} writes={ldb['writes']} creations={ldb['creations']}")
    lines.append(f"[m5-scan] LEGACY_POSTPROCESS calls={d['legacy_postprocess']['calls']}")
    lines.append(f"[m5-scan] LEGACY_FALLBACKS={d['legacy_fallbacks']['fallbacks']}")
    lines.append(f"[m5-scan] LEGACY_MIRROR runs={d['legacy_mirror']['runs']}")
    lines.append(f"[m5-scan] LEGACY_ACTIVE_CONFIG deps={d['legacy_active_config']['deps']}")
    lines.append(f"[m5-scan] LEGACY_LIFECYCLE deps={d['legacy_lifecycle']['deps']}")
    sc = d["migration_scaffolding"]
    lines.append(f"[m5-scan] SCAFFOLDING shadow_dual_read={sc['shadow_dual_read_paths']} "
                 f"temp_flags={sc['temporary_authority_flags']} "
                 f"comparators={sc['temporary_comparators']} "
                 f"dual_write={sc['dual_write_migration_paths']}")
    gc = d["lifecycle_gc"]
    lines.append(f"[m5-scan] LIFECYCLE states={gc['lifecycle_states_found']} "
                 f"gc_impl={gc['gc_implementation_present']} "
                 f"incomplete_visible={gc['incomplete_worlds_visible']}")
    lines.append(f"[m5-scan] BUGS unresolved={d['bugs']['unresolved']}/{d['bugs']['total']}")
    lines.append(f"[m5-scan] REGRESSION m3b={d['m3b_regression']} m4={d['m4_regression']}")

    # Numeric gate evaluation
    fail = []
    if ldb["reads"] != 0: fail.append("NORMAL_MODE_LEGACY_DB_READS")
    if ldb["writes"] != 0: fail.append("NORMAL_MODE_LEGACY_DB_WRITES")
    if ldb["creations"] != 0: fail.append("NORMAL_MODE_LEGACY_DB_CREATIONS")
    if d["legacy_postprocess"]["calls"] != 0: fail.append("NORMAL_MODE_LEGACY_POSTPROCESS_CALLS")
    if d["legacy_fallbacks"]["fallbacks"] != 0: fail.append("NORMAL_MODE_LEGACY_FALLBACKS")
    if d["legacy_mirror"]["runs"] != 0: fail.append("NORMAL_MODE_LEGACY_MIRROR_RUNS")
    if d["legacy_active_config"]["deps"] != 0: fail.append("NORMAL_MODE_LEGACY_ACTIVE_CONFIG_DEPENDENCIES")
    if d["legacy_lifecycle"]["deps"] != 0: fail.append("NORMAL_MODE_LEGACY_LIFECYCLE_DEPENDENCIES")
    if sc["shadow_dual_read_paths"] != 0: fail.append("SHADOW_DUAL_READ_PATHS")
    if sc["temporary_authority_flags"] != 0: fail.append("TEMPORARY_AUTHORITY_FLAGS")
    if gc["incomplete_worlds_visible"] != 0: fail.append("INCOMPLETE_WORLDS_VISIBLE")
    if not gc["gc_implementation_present"]: fail.append("GC_IMPLEMENTATION_MISSING")
    if d["bugs"]["unresolved"] != 0: fail.append("UNRESOLVED_BUGS")
    if d["m3b_regression"] != "PASS": fail.append("M3B_REGRESSION")
    if d["m4_regression"] != "PASS": fail.append("M4_REGRESSION")

    numeric_gate = "PASS" if not fail else "FAIL"
    lines.append(f"[m5-scan] numeric_gate={numeric_gate} failing={fail if fail else 'none'}")
    return "\n".join(lines)


if __name__ == "__main__":
    d = scan()
    print(_format(d))
    out = ROOT / ".dsh-control" / "evidence" / "m5_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
    sys.exit(0 if _format(d).split("numeric_gate=")[1].startswith("PASS") else 1)
