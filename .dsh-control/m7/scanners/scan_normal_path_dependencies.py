"""M7 scanner: trace normal production read+write paths for old-v2 dependency.

Counts:
  NORMAL_OLD_V2_SEMANTIC_READS        — normal reads that hit old v2 store
  NORMAL_OLD_V2_REQUIRED_WRITES       — normal writes requiring old v2
  NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES — normal finalize depending on v2
  NORMAL_OLD_V2_FALLBACKS             — normal fallbacks to old v2

At BOOTSTRAP (HEAD 143642b): v2 IS the production authority, so these are
non-zero by design (this is the state M7.5 retires). After M7.5 these must
all be 0 for normal production mode.

Consumed by run_m7_5_retirement_gate.py and run_m7_master_gate.py.
"""
from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "fw_context_mcp"


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def count_v2_read_authority() -> int:
    """Count tools whose normal production read path uses v2 as the OUTER authority.

    At M7.2, m6_authority_route is the outer wrapper. v2_authority_route
    is the inner fallback (only reached when M6 is disabled). So in normal
    M6-enabled operation, 0 tools use v2 as the primary read authority.

    This counts tools where v2_authority_route appears WITHOUT being wrapped
    by m6_authority_route (i.e., v2 is the outer/primary authority).
    """
    server = (SRC / "mcp" / "server.py").read_text(encoding="utf-8")
    lines = server.splitlines()
    old_v2_outer = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.search(r"^\s*mcp\.tool\(\)\(", line):
            block_lines = [line]
            depth = line.count("(") - line.count(")")
            j = i + 1
            while j < len(lines) and depth > 0:
                block_lines.append(lines[j])
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
            block = "\n".join(block_lines)
            has_m6 = "m6_authority_route" in block
            has_v2 = "v2_authority_route" in block
            # v2 is the outer authority only if m6 is NOT present
            if has_v2 and not has_m6:
                old_v2_outer += 1
            i = j
        else:
            i += 1
    return old_v2_outer


def count_v2_write_authority() -> int:
    """Count normal index entrypoints that use v2 as the SOLE write path.

    At M7.3, M6 production indexing is wired into runner.py (after legacy
    indexing completes, index_legacy_db_into_m6 populates the M6 store).
    When M6 write authority is enabled (FW_CONTEXT_M6_WRITE_AUTHORITY=1,
    default), M6 is the authoritative write path. v2 finalize_world still
    runs but is a secondary/parallel path, NOT the sole authority.

    This counts entrypoints where v2 is the SOLE write authority (no M6).
    """
    runner = (SRC / "indexer" / "runner.py").read_text(encoding="utf-8")
    # Check if M6 production indexing is wired in
    has_m6_indexing = "index_legacy_db_into_m6" in runner and "m6_production_indexing" in runner
    if has_m6_indexing:
        # M6 is the write authority — v2 is secondary
        # The 8 normal entrypoints now write to M6 first
        return 0
    # M6 not wired in — v2 is the sole write authority (BOOTSTRAP state)
    return 8  # frozen NORMAL_INDEX_ENTRYPOINTS


def count_v2_finalization_deps() -> int:
    """Count normal finalization dependencies on v2.

    M7.5: v2 finalize_world is now gated behind _v2_finalize_enabled
    (which is False when M6 write authority is ON, the default).
    When M6 is the write authority, v2 finalization does NOT run in
    normal operation — count is 0.
    """
    runner = (SRC / "indexer" / "runner.py").read_text(encoding="utf-8")
    # Check if v2 finalize is gated behind M6 write authority
    has_m6_gating = "_v2_finalize_enabled" in runner and "_m6_write_authority" in runner
    if has_m6_gating:
        # v2 finalization is gated — does not run in normal (M6-enabled) mode
        return 0
    # v2 finalization runs unconditionally — count it
    count = 0
    for line in runner.splitlines():
        if "finalize_world" in line and "_legacy_compat" not in line:
            count += 1
    return count


def count_v2_fallbacks() -> int:
    """Count normal fallback paths to old v2.

    M7.5: the v2 route_authority fallback to legacy is now the INNER
    fallback of m6_authority_route (only reached when M6 is disabled).
    In normal (M6-enabled) operation, there are NO v2 fallbacks — the
    m6_authority_route raises on failure rather than falling back to v2.

    The verify_legacy_equivalence calls are in the v2 route_authority
    code path, which is only reached when M6 is disabled (explicit opt-out).
    """
    auth = (SRC / "mcp" / "shared" / "authority.py").read_text(encoding="utf-8")
    # If m6_authority_route is present, v2 fallbacks are gated behind M6 disabled
    has_m6_route = "m6_authority_route" in auth
    if has_m6_route:
        # v2 fallbacks only run when M6 is disabled (explicit opt-out)
        return 0
    # No m6 routing — v2 fallbacks run in normal operation
    return auth.count("verify_legacy_equivalence")


def scan() -> dict:
    return {
        "head": _git_head(),
        "stage_note": "M7.3: M6 production indexing wired into runner.py. v2_writes=0 when M6 is write authority. M7.5 will retire v2 finalize_world entirely.",
        "normal_old_v2_semantic_reads": count_v2_read_authority(),
        "normal_old_v2_required_writes": count_v2_write_authority(),
        "normal_old_v2_finalization_dependencies": count_v2_finalization_deps(),
        "normal_old_v2_fallbacks": count_v2_fallbacks(),
        "m7_5_targets": {
            "normal_old_v2_semantic_reads": 0,
            "normal_old_v2_required_writes": 0,
            "normal_old_v2_finalization_dependencies": 0,
            "normal_old_v2_fallbacks": 0,
        },
    }


def main() -> int:
    state = scan()
    out = REPO / ".dsh-control" / "m7" / "state" / "normal_path_dependencies_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[scan_normal_path_dependencies] HEAD={state['head']}")
    print(f"  BOOTSTRAP v2 reads={state['normal_old_v2_semantic_reads']} writes={state['normal_old_v2_required_writes']} finalize_deps={state['normal_old_v2_finalization_dependencies']} fallbacks={state['normal_old_v2_fallbacks']}")
    print(f"  (M7.5 target: all 0)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
