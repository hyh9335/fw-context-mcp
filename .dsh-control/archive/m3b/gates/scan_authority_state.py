#!/usr/bin/env python3
"""Code-derived authority-state scanner for the fw-context M3B milestone.

DERIVES its counters from CURRENT production code. It does NOT read
PROJECT_STATUS.md, AUTHORITY_LEDGER.md, or any Lead report.

What it inspects:
  * src/fw_context_mcp/mcp/server.py  — `v2_authority_route("<tool>", legacy, v2_fn)`
    registrations  -> the CORE_NORMAL_USE_SEMANTIC denominator + adapter mapping.
  * src/fw_context_mcp/mcp/shared/v2_shadow_adapters.py — each `def v2_shadow_*`
    adapter body, classified by whether it actually reads v2 World-scoped facts
    (calls a v2 query primitive / delegates to another v2_shadow adapter) vs.
    merely `raise V2UnsupportedOperation` (legacy semantic execution).
  * src/fw_context_mcp/mcp/shared/authority.py — `v2_authority_route` wrapper +
    `route_authority` / `route_with_shadow` for UNGUARDED legacy fallback sites
    (fallback not gated by `_can_safely_fallback` -> CORE_UNSAFE_FALLBACK).
  * .dsh-control/state/v2_bugs.json — open bug register (UNRESOLVED_NORMAL_USE_V2_BUGS).

Writes machine JSON to .dsh-control/evidence/authority_state.json.

Usage:
    python .dsh-control/gates/scan_authority_state.py [--project-root PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

# v2 query primitives that, when called inside an adapter body, prove a real
# v2 World-scoped read is happening.
V2_READ_PRIMITIVE_CALLS = {
    "v2_lookup_symbol",
    "v2_symbol_edges",
    "resolve_bindings",
    "v2_resolve_bindings",
    "open_query_connection",
}
# sqlite-style query methods on a v2 connection object also prove a v2 read.
CONN_QUERY_ATTRS = {"execute", "executemany", "fetchone", "fetchall"}

GUARD_FN = "_can_safely_fallback"
LEGACY_RETURN_NAMES = {"legacy_fn", "legacy_result"}


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


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(node: ast.Call) -> str | None:
    """Return a readable name for a Call's func, or None."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_v2_read_call(node: ast.Call) -> bool:
    name = _call_name(node)
    if name in V2_READ_PRIMITIVE_CALLS:
        return True
    if name in CONN_QUERY_ATTRS and isinstance(node.func, ast.Attribute):
        return True
    return False


def _is_raise_v2unsupported(node: ast.Raise) -> bool:
    exc = node.exc
    if exc is None:
        return False
    if isinstance(exc, ast.Call):
        name = _call_name(exc)
        return name == "V2UnsupportedOperation"
    if isinstance(exc, ast.Name):
        return exc.id == "V2UnsupportedOperation"
    return False


def _adapter_calls_other_v2_shadow(node: ast.Call) -> str | None:
    """If this Call targets another v2_shadow_* adapter, return its name."""
    name = _call_name(node)
    if name and name.startswith("v2_shadow_"):
        return name
    return None


def _scan_function_body(func_node: ast.FunctionDef) -> dict:
    """Inspect one adapter function body for v2 reads and V2Unsupported raises."""
    v2_read_calls: list[str] = []
    delegated_to: list[str] = []
    raise_lines: list[int] = []
    raise_snippets: list[str] = []

    for n in ast.walk(func_node):
        if isinstance(n, ast.Call):
            if _is_v2_read_call(n):
                nm = _call_name(n) or "?.<attr>"
                v2_read_calls.append(f"{nm}@{n.lineno}")
            tgt = _adapter_calls_other_v2_shadow(n)
            if tgt is not None:
                delegated_to.append(f"{tgt}@{n.lineno}")
        elif isinstance(n, ast.Raise) and _is_raise_v2unsupported(n):
            raise_lines.append(n.lineno)
            # grab a short snippet of the raised message if literal
            try:
                exc = n.exc
                if isinstance(exc, ast.Call) and exc.args:
                    a0 = exc.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        raise_snippets.append(a0.value[:120])
            except Exception:
                pass
    return {
        "v2_read_calls": v2_read_calls,
        "delegated_to": delegated_to,
        "raise_v2unsupported_lines": raise_lines,
        "raise_snippets": raise_snippets,
    }


def classify_adapters(adapters_path: Path) -> dict[str, dict]:
    """Return adapter_name -> inspection dict (raw, before fixpoint + class)."""
    tree = _parse(adapters_path)
    out: dict[str, dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("v2_shadow_"):
            info = _scan_function_body(node)
            info["file"] = str(adapters_path)
            info["def_line"] = node.lineno
            info["end_line"] = node.end_lineno or node.lineno
            out[node.name] = info
    return out


def _propagate_delegation(adapters: dict[str, dict]) -> None:
    """Fixed-point: an adapter that delegates to a v2-shadow adapter with a real
    v2 read inherits has_v2_read=True (e.g. find_references -> find_callers)."""
    changed = True
    # initial direct has_v2_read
    for name, info in adapters.items():
        info["has_v2_read_direct"] = bool(info["v2_read_calls"])
    while changed:
        changed = False
        for name, info in adapters.items():
            cur = info.get("has_v2_read", info["has_v2_read_direct"])
            new = cur
            for tgt in info["delegated_to"]:
                tgt_name = tgt.split("@", 1)[0]
                t = adapters.get(tgt_name)
                if t and t.get("has_v2_read", t["has_v2_read_direct"]):
                    new = True
            if new != cur:
                info["has_v2_read"] = new
                changed = True
        for name, info in adapters.items():
            info.setdefault("has_v2_read", info["has_v2_read_direct"])


def classify(adapter_info: dict) -> str:
    has_read = adapter_info.get("has_v2_read", False)
    raises = bool(adapter_info["raise_v2unsupported_lines"])
    if has_read and not raises:
        return "V2_NATIVE"
    if has_read and raises:
        return "PARTIAL_NATIVE_LEGACY_DEFER"
    if (not has_read) and raises:
        return "V2_ROUTED_LEGACY_EXEC"
    return "UNKNOWN"


def enumerate_core_tools(server_path: Path) -> list[dict]:
    """Find every v2_authority_route("<tool>", legacy, v2_fn) registration."""
    tree = _parse(server_path)
    src = server_path.read_text(encoding="utf-8")
    core: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "v2_authority_route":
            if not node.args:
                continue
            tool_arg = node.args[0]
            tool_name = tool_arg.value if isinstance(tool_arg, ast.Constant) else None
            legacy_name = None
            if len(node.args) >= 2:
                a1 = node.args[1]
                if isinstance(a1, ast.Attribute):
                    legacy_name = a1.attr
                elif isinstance(a1, ast.Name):
                    legacy_name = a1.id
            v2_name = None
            if len(node.args) >= 3:
                a2 = node.args[2]
                if isinstance(a2, ast.Name):
                    v2_name = a2.id
                elif isinstance(a2, ast.Attribute):
                    v2_name = a2.attr
            core.append({
                "tool": tool_name,
                "legacy_handler": legacy_name,
                "v2_adapter": v2_name,
                "server_line": node.lineno,
            })
    return core


def _walk_own_body(node: ast.AST):
    """Yield descendants of *node* WITHOUT descending into nested function/lambda
    definitions. Used so a function's returns are not re-counted under an outer
    function that lexically contains it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_own_body(child)


def _return_is_legacy(ret: ast.Return) -> bool:
    v = ret.value
    if v is None:
        return False
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
            and v.func.id == "legacy_fn":
        return True
    if isinstance(v, ast.Name) and v.id == "legacy_result":
        return True
    return False


def _if_is_guard(node: ast.If) -> bool:
    return (
        isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == GUARD_FN
    )


def count_unsafe_fallbacks(authority_path: Path) -> list[dict]:
    """Count UNGUARDED legacy fallback sites in the authority layer.

    A fallback site is a `return legacy_fn(...)` / `return legacy_result` that
    lives inside an ``except`` handler (i.e. v2 was attempted and FAILED) and is
    NOT wrapped in an ``if _can_safely_fallback(...):`` guard. Pre-v2 returns
    (v2 disabled, no project_root) are NOT fallbacks and are ignored.

    Inspects: v2_authority_route (and its nested ``wrapper``), route_authority,
    route_with_shadow.
    """
    tree = _parse(authority_path)
    unsafe: list[dict] = []

    target_names = {"v2_authority_route", "route_authority", "route_with_shadow"}

    def scan_func(fn: ast.FunctionDef) -> None:
        # Collect this function's OWN except handlers (not nested functions').
        own_excepts: list[ast.ExceptHandler] = []
        for n in _walk_own_body(fn):
            if isinstance(n, ast.ExceptHandler):
                own_excepts.append(n)
        for eh in own_excepts:
            # Within this except handler, find legacy returns and check guard.
            eh_returns: list[ast.Return] = []
            guarded_ifs: list[ast.If] = []
            for n in ast.walk(eh):
                if isinstance(n, ast.Return):
                    eh_returns.append(n)
                elif isinstance(n, ast.If) and _if_is_guard(n):
                    guarded_ifs.append(n)
            for ret in eh_returns:
                if not _return_is_legacy(ret):
                    continue
                guarded = False
                rl = ret.lineno
                re_ = ret.end_lineno or ret.lineno
                for g in guarded_ifs:
                    gl = g.lineno
                    ge = g.end_lineno or g.lineno
                    if gl <= rl <= ge:
                        guarded = True
                        break
                if not guarded:
                    unsafe.append({
                        "function": fn.name,
                        "except_line": eh.lineno,
                        "return_line": ret.lineno,
                        "site": "except -> return legacy_fn/legacy_result (unguarded)",
                    })

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in target_names:
            scan_func(node)
            # also scan nested wrapper (v2_authority_route returns a closure)
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, ast.FunctionDef):
                    scan_func(sub)
    return unsafe


def enumerate_non_core(server_path: Path) -> dict:
    """Classify the known non-core tool registrations (informational)."""
    src = server_path.read_text(encoding="utf-8")
    specialized = []
    for name in ("get_vector_table", "compare_variants", "group_equivalent_variants"):
        if name in src:
            specialized.append(name)
    # maintenance.* are registered via maintenance module; mark informational.
    return {
        "SPECIALIZED_OPTIONAL": specialized,
        "OPERATIONAL_NON_SEMANTIC": [
            "check_dependencies", "check_ollama", "configure_llm",
            "get_active_build", "get_environment_status", "get_project_info",
            "list_projects", "list_variants", "reindex_file", "reset_index",
        ],
        "note": "non-core classifications are informational; only CORE counters feed the gate",
    }


def scan(project_root: Path) -> dict:
    repo = project_root.resolve()
    server_path = repo / "src" / "fw_context_mcp" / "mcp" / "server.py"
    adapters_path = repo / "src" / "fw_context_mcp" / "mcp" / "shared" / "v2_shadow_adapters.py"
    authority_path = repo / "src" / "fw_context_mcp" / "mcp" / "shared" / "authority.py"
    bugs_path = repo / ".dsh-control" / "state" / "v2_bugs.json"

    core = enumerate_core_tools(server_path)
    adapter_infos = classify_adapters(adapters_path)
    _propagate_delegation(adapter_infos)

    # classify each core tool by its adapter
    core_rows = []
    for c in core:
        adapter = c.get("v2_adapter")
        info = adapter_infos.get(adapter)
        if info is None:
            cls = "UNKNOWN"
            detail = {"reason": f"adapter {adapter!r} not found in {adapters_path.name}"}
        else:
            cls = classify(info)
            detail = {
                "file": info["file"],
                "def_line": info["def_line"],
                "end_line": info["end_line"],
                "v2_read_calls": info["v2_read_calls"],
                "delegated_to": info["delegated_to"],
                "raise_v2unsupported_lines": info["raise_v2unsupported_lines"],
                "raise_snippets": info["raise_snippets"],
            }
        core_rows.append({
            "tool": c["tool"],
            "v2_adapter": adapter,
            "legacy_handler": c.get("legacy_handler"),
            "server_line": c["server_line"],
            "classification": cls,
            "detail": detail,
        })

    # counters
    core_count = len(core_rows)
    v2_routed = core_count  # all are wrapped by v2_authority_route by construction
    v2_native_read = sum(
        1 for r in core_rows
        if r["classification"] in {"V2_NATIVE", "PARTIAL_NATIVE_LEGACY_DEFER"}
    )
    legacy_exec = sum(
        1 for r in core_rows
        if r["classification"] in {"V2_ROUTED_LEGACY_EXEC", "PARTIAL_NATIVE_LEGACY_DEFER"}
    )
    unsafe = count_unsafe_fallbacks(authority_path)

    bugs = []
    if bugs_path.exists():
        try:
            bugs = json.loads(bugs_path.read_text(encoding="utf-8"))
        except Exception:
            bugs = []
    unresolved_bugs = [b for b in bugs if b.get("status") != "resolved"]

    head = _git(["rev-parse", "HEAD"], repo)
    short = _git(["rev-parse", "--short", "HEAD"], repo)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    dirty = _git_dirty(repo)

    state = {
        "schema_version": 1,
        "milestone": "M3B_NATIVE_V2_READ_ENGINE_COMPLETE",
        "generated_at_utc": _now_utc(),
        "project_root": str(repo),
        "git": {"head": head, "short": short, "branch": branch, "dirty": dirty},
        "counters": {
            "CORE_NORMAL_USE_SEMANTIC_TOOLS": core_count,
            "CORE_V2_ROUTED": v2_routed,
            "CORE_V2_NATIVE_READ": v2_native_read,
            "CORE_LEGACY_SEMANTIC_EXECUTION": legacy_exec,
            "CORE_UNSAFE_FALLBACK": len(unsafe),
            "UNRESOLVED_NORMAL_USE_V2_BUGS": len(unresolved_bugs),
        },
        "unsafe_fallback_sites": unsafe,
        "unresolved_bugs": unresolved_bugs,
        "core_tools": core_rows,
        "non_core": enumerate_non_core(server_path),
        "classification_legend": {
            "V2_NATIVE": "has a v2 read path, no V2Unsupported deferral",
            "V2_ROUTED_LEGACY_EXEC": "raises V2UnsupportedOperation, no v2 read (pure legacy exec)",
            "PARTIAL_NATIVE_LEGACY_DEFER": "has BOTH a v2 read path AND a V2Unsupported deferral",
            "UNKNOWN": "scanner could not classify -> treated as failing",
        },
    }
    return state


def _now_utc() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description="fw-context M3B authority-state scanner")
    ap.add_argument("--project-root", default=str(Path.cwd().resolve()))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo = Path(args.project_root).resolve()
    # refuse to operate outside a git repo
    if not (repo / ".git").exists():
        print(f"[scan] ERROR: {repo} is not a git repository", file=sys.stderr)
        return 2

    state = scan(repo)
    out_path = Path(args.out) if args.out else (
        repo / ".dsh-control" / "evidence" / "authority_state.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    c = state["counters"]
    print("[scan] authority_state.json written ->", out_path)
    print("[scan] HEAD:", state["git"]["short"], "(dirty)" if state["git"]["dirty"] else "(clean)")
    print(f"[scan] CORE={c['CORE_NORMAL_USE_SEMANTIC_TOOLS']} "
          f"V2_ROUTED={c['CORE_V2_ROUTED']} "
          f"V2_NATIVE_READ={c['CORE_V2_NATIVE_READ']} "
          f"LEGACY_EXEC={c['CORE_LEGACY_SEMANTIC_EXECUTION']} "
          f"UNSAFE_FALLBACK={c['CORE_UNSAFE_FALLBACK']} "
          f"UNRESOLVED_BUGS={c['UNRESOLVED_NORMAL_USE_V2_BUGS']}")
    # gate projection
    ok = (
        c["CORE_NORMAL_USE_SEMANTIC_TOOLS"] > 0
        and c["CORE_V2_ROUTED"] == c["CORE_NORMAL_USE_SEMANTIC_TOOLS"]
        and c["CORE_V2_NATIVE_READ"] == c["CORE_NORMAL_USE_SEMANTIC_TOOLS"]
        and c["CORE_LEGACY_SEMANTIC_EXECUTION"] == 0
        and c["CORE_UNSAFE_FALLBACK"] == 0
        and c["UNRESOLVED_NORMAL_USE_V2_BUGS"] == 0
    )
    print("[scan] M3B numeric gate:", "PASS" if ok else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
