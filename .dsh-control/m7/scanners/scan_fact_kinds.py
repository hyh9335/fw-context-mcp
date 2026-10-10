"""M7 scanner: enumerate M6 fact kinds from schema + emitters.

Verifies count and naming consistency. Consumed by run_m7_bootstrap_gate.py.
"""
from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "fw_context_mcp" / "store_v2"
M6_SCHEMA = SRC / "m6_schema.py"
M6_INDEXING = SRC / "m6_indexing.py"

EXPECTED_KINDS = {
    "Symbol", "Function", "Call", "Variable", "Type", "Field",
    "EnumConst", "EnumConstant", "Inherits", "Reference",
    "ConditionalState", "Body", "FileContent",
}


def extract_emitted_kinds() -> set[str]:
    """Find string literals emitted as fact_kind by m6_indexing.py canonicalize_* functions."""
    src = M6_INDEXING.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(M6_INDEXING))
    kinds = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Look for fact_kind assignment patterns
            pass
    # Simpler: grep for the canonical kind strings
    for kind in EXPECTED_KINDS:
        if re.search(rf"['\"]({kind})['\"]", src):
            kinds.add(kind)
    return kinds


def extract_schema_declared() -> list[str]:
    """Extract the fact_kind enum/comment from m6_schema.py."""
    src = M6_SCHEMA.read_text(encoding="utf-8")
    # Find the comment listing kinds
    m = re.search(r"fact_kind[^']*(Symbol\|[^'\"]+)", src)
    if m:
        return m.group(1).split("|")
    return []


def scan() -> dict:
    emitted = extract_emitted_kinds()
    declared = extract_schema_declared()
    # EnumConst vs EnumConstant normalization
    emitted_normalized = set(k for k in emitted if k != "EnumConstant") | ({"EnumConst"} if "EnumConstant" in emitted else set())
    emitted_normalized = emitted_normalized | ({"EnumConst"} if "EnumConst" in emitted else set())
    count = len(emitted_normalized)
    return {
        "head": _git_head(),
        "declared_in_schema": declared,
        "emitted_by_indexer": sorted(emitted),
        "distinct_kinds_normalized": sorted(emitted_normalized),
        "count": count,
        "naming_inconsistency": "EnumConst" in declared and "EnumConstant" in emitted,
        "expected_count": 12,
        "count_matches_expected": count == 12,
    }


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    state = scan()
    out = REPO / ".dsh-control" / "m7" / "state" / "fact_kinds_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[scan_fact_kinds] HEAD={state['head']} count={state['count']} expected={state['expected_count']} match={state['count_matches_expected']}")
    if state["naming_inconsistency"]:
        print(f"  naming_inconsistency: schema declares 'EnumConst' but emitter writes 'EnumConstant' (count unchanged)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
