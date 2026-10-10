"""M7 scanner: tool denominator stability.

Re-derives CORE_SEMANTIC_TOOLS from current server.py and compares to the
frozen bootstrap denominator. Detects disappearing tools. Consumed by
run_m7_bootstrap_gate.py and run_m7_master_gate.py (§44 final enumeration).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Import the extraction logic from scan_production_authority
sys.path.insert(0, str(REPO / ".dsh-control" / "m7" / "scanners"))
from scan_production_authority import extract_tool_registrations, MAINTENANCE_TOOLS  # noqa: E402


FROZEN_DENOMINATOR = 32
FROZEN_V2_MIGRATED = 29
FROZEN_NON_V2 = 3


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def scan() -> dict:
    all_tools = extract_tool_registrations()
    semantic = [t for t in all_tools if not t.get("is_maintenance", False)]
    current_names = {t["name"] for t in semantic}
    # Frozen set from denominator.json
    denom_path = REPO / ".dsh-control" / "m7" / "state" / "denominator.json"
    frozen_names = set()
    if denom_path.exists():
        d = json.loads(denom_path.read_text(encoding="utf-8"))
        for t in d.get("A_CORE_SEMANTIC_TOOLS", {}).get("tools", []):
            frozen_names.add(t["name"])
    disappeared = frozen_names - current_names
    appeared = current_names - frozen_names
    return {
        "head": _git_head(),
        "bootstrap_denominator": FROZEN_DENOMINATOR,
        "current_denominator": len(semantic),
        "bootstrap_v2_migrated": FROZEN_V2_MIGRATED,
        "current_v2_migrated": sum(1 for t in semantic if t["v2_routed"]),
        "bootstrap_non_v2": FROZEN_NON_V2,
        "current_non_v2": sum(1 for t in semantic if not t["v2_routed"]),
        "denominator_stable": len(semantic) == FROZEN_DENOMINATOR,
        "disappeared_tools": sorted(disappeared),
        "appeared_tools": sorted(appeared),
        "current_tool_names": sorted(current_names),
    }


def main() -> int:
    state = scan()
    out = REPO / ".dsh-control" / "m7" / "state" / "tool_denominator_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[scan_tool_denominator] HEAD={state['head']}")
    print(f"  bootstrap={state['bootstrap_denominator']} current={state['current_denominator']} stable={state['denominator_stable']}")
    if state["disappeared_tools"]:
        print(f"  DISAPPEARED: {state['disappeared_tools']}")
    if state["appeared_tools"]:
        print(f"  APPEARED: {state['appeared_tools']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
