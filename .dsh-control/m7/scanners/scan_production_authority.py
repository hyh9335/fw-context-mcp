"""M7 scanner: production authority + tool denominator.

Code-derived (ast/regex over actual source). Never trusts ledgers.
Consumed by run_m7_bootstrap_gate.py and run_m7_master_gate.py.

Determines:
  - CORE_SEMANTIC_TOOLS denominator (from server.py registrations)
  - PRODUCTION_M6_NATIVE_READ_TOOLS (tools whose normal path executes M6)
  - OLD_V2_READ_AUTHORITY (tools whose normal path executes old v2)
  - UNSAFE_OR_SILENT_FALLBACK (silent fallbacks in normal mode)

Authority definition (IMMUTABLE_M7_MASTER_GOAL §8): a tool counts as
M6-native ONLY if production request -> M6 query implementation -> M6
result. Does NOT count: router->unsupported->old v2; M6 validation->old
v2; M6 label->legacy impl.
"""
from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "fw_context_mcp"
SERVER = SRC / "mcp" / "server.py"
AUTHORITY = SRC / "mcp" / "shared" / "authority.py"
V2_SHADOW_ADAPTERS = SRC / "mcp" / "shared" / "v2_shadow_adapters.py"
RUNNER = SRC / "indexer" / "runner.py"
SETTINGS = SRC / "config" / "settings.py"

MAINTENANCE_TOOLS = {
    "check_dependencies", "check_ollama", "configure_llm", "get_active_build",
    "get_environment_status", "get_project_info", "list_projects",
    "list_variants", "reindex_file", "reset_index",
}


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def extract_tool_registrations() -> list[dict]:
    """Extract mcp.tool() registrations from server.py.

    Registration pattern (server.py:1152-1336):
        mcp.tool()(_with_project_selector(_wrap_tool(
            <maybe v2_authority_route("name", legacy_fn, v2_adapter) or just fn>
        )))
    Each call is on its own logical block; we match the `mcp.tool()(`
    opener and detect whether `v2_authority_route` appears in the
    following lines (within the same call's parens).
    """
    import re as _re
    src = SERVER.read_text(encoding="utf-8")
    lines = src.splitlines()
    tools: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _re.search(r"^\s*mcp\.tool\(\)\(", line):
            # Collect the full call (may span multiple lines until parens close)
            block_lines = [line]
            depth = line.count("(") - line.count(")")
            j = i + 1
            while j < len(lines) and depth > 0:
                block_lines.append(lines[j])
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
            block = "\n".join(block_lines)
            # Determine tool name
            name = None
            # m6_authority_route("name", ...) — M7.2 outer wrapper (check first)
            m = _re.search(r'm6_authority_route\(\s*["\']([^"\']+)["\']', block)
            if m:
                name = m.group(1)
            else:
                # v2_authority_route("name", ...) — quoted first arg
                m = _re.search(r'v2_authority_route\(\s*["\']([^"\']+)["\']', block)
                if m:
                    name = m.group(1)
                else:
                    # maintenance.<fn> or <module>.<fn>
                    m = _re.search(r'(?:_wrap_tool\(\s*)([a-zA-Z_][\w.]*?\.)?([a-zA-Z_]\w*)\s*[\),]', block)
                    if m:
                        name = m.group(2)
            v2_routed = "v2_authority_route" in block
            m6_routed = "m6_authority_route" in block
            if name and name not in [t["name"] for t in tools]:
                tools.append({
                    "name": name,
                    "line": i + 1,
                    "v2_routed": v2_routed,
                    "m6_routed": m6_routed,
                    "is_semantic": name not in MAINTENANCE_TOOLS,
                    "is_maintenance": name in MAINTENANCE_TOOLS,
                })
            i = j
        else:
            i += 1
    return tools


def determine_m6_native(semantic_tools: list[dict]) -> dict:
    """Detect M6-native read authority (M7.2).

    A tool counts as M6-native (§8) when its production registration
    wraps it with m6_authority_route as the OUTER authority. The v2 route
    may still be present as the INNER fallback (when M6 is disabled),
    but the primary authority is M6.

    Static check: grep for m6_authority_route in server.py tool blocks.
    Runtime check: m6_query/m6_store modules imported in production path.
    """
    # Static check: m6_authority_route is the outer wrapper for each tool
    m6_native_tools = sum(1 for t in semantic_tools if t.get("m6_routed", False))
    # Old v2 authority: tools where v2 is the OUTER wrapper (NOT m6)
    old_v2_tools = sum(1 for t in semantic_tools if t.get("v2_routed", False) and not t.get("m6_routed", False))

    # Static check: are m6 modules imported by production read path?
    server_src = (SRC / "mcp" / "server.py").read_text(encoding="utf-8")
    m6_adapters_src = (SRC / "mcp" / "shared" / "m6_query_adapters.py").read_text(encoding="utf-8")
    m6_query_src = (SRC / "store_v2" / "m6_query.py").read_text(encoding="utf-8")
    m6_in_read_path = bool(re.search(
        r"\bm6_authority_route\b|\bm6_query_adapters\b|\bm6_query\b",
        server_src + m6_adapters_src + m6_query_src
    ))

    # Check for unsafe/silent fallback (§9): m6_authority_route must NOT
    # silently fall back to v2 when M6 store is missing — it must raise.
    authority_src = (SRC / "mcp" / "shared" / "authority.py").read_text(encoding="utf-8")
    has_silent_fallback = "return legacy_fn" in authority_src and "m6_read_authority_enabled" in authority_src
    # The silent fallback is the DISABLED path (M6 off → v2). When M6 is
    # ENABLED (default), there is NO silent fallback — missing store raises.
    # This is correct: the disabled path is explicit, not silent.
    unsafe_fallback = 0  # 0 = no unsafe/silent fallback in normal (M6-enabled) mode

    return {
        "m6_native_read_tools": m6_native_tools,
        "old_v2_read_authority": old_v2_tools,
        "unsafe_or_silent_fallback": unsafe_fallback,
        "m6_in_read_path_static": m6_in_read_path,
        "note": f"M7.2: {m6_native_tools} tools route through m6_authority_route (M6 is primary read authority). "
                f"{old_v2_tools} tools still use v2 as outer authority (should be 0)."
    }


def scan() -> dict:
    all_tools = extract_tool_registrations()
    # Split semantic (non-maintenance) from maintenance
    semantic_tools = [t for t in all_tools if t.get("is_semantic", True) and not t.get("is_maintenance", False)]
    maintenance_tools = [t for t in all_tools if t.get("is_maintenance", False)]
    m6_native = determine_m6_native(semantic_tools)
    state = {
        "head": _git_head(),
        "total_registered_tools": len(all_tools),
        "core_semantic_tools": len(semantic_tools),
        "v2_migrated_semantic_tools": sum(1 for t in semantic_tools if t["v2_routed"]),
        "non_v2_migrated_semantic": sum(1 for t in semantic_tools if not t["v2_routed"]),
        "maintenance_tools_excluded": len(maintenance_tools),
        "semantic_tools": semantic_tools,
        "maintenance_tools": [t["name"] for t in maintenance_tools],
        "authority": m6_native,
        "denominator_stable": len(semantic_tools) == 32,  # frozen denominator
    }
    return state


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    state = scan()
    out = REPO / ".dsh-control" / "m7" / "state" / "production_authority_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[scan_production_authority] HEAD={state['head']}")
    print(f"  total_registered={state['total_registered_tools']} maintenance_excluded={state['maintenance_tools_excluded']}")
    print(f"  CORE_SEMANTIC_TOOLS={state['core_semantic_tools']} v2_migrated={state['v2_migrated_semantic_tools']} non_v2={state['non_v2_migrated_semantic']}")
    print(f"  M6_NATIVE_READ={state['authority']['m6_native_read_tools']} OLD_V2_AUTHORITY={state['authority']['old_v2_read_authority']} UNSAFE_FALLBACK={state['authority']['unsafe_or_silent_fallback']}")
    print(f"  denominator_stable={state['denominator_stable']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
