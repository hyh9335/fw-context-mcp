import json
import io

OUT = r".fw-validation/artifacts/V3/PARITY_DIFF.json"
CASES = ["G01", "G02", "G03", "G05", "G06", "G07", "G10", "G12"]
diff = {
    "phase": "V3_CROSS_PLATFORM",
    "method": "V1 artifacts = WINDOWS baseline (venv-win, C:\\Users work dirs); V3-WSL = WSL leg (fwctx-venv311, /home work dirs), same recipes + same expected.json oracles",
    "platforms": {
        "windows": {"python": "venv-win 3.11", "mcp_sdk": "1.28.1", "work_root": "C:\\Users\\h00969597\\fwctx-validation-work\\V1|V3"},
        "wsl": {"python": "fwctx-venv311 (3.11)", "mcp_sdk": "1.30.0", "work_root": "~/fwctx-validation-work/V3-WSL"}
    },
    "cases": {},
    "summary": {}
}
same = 0
for cid in CASES:
    v1 = json.load(io.open(rf".fw-validation/artifacts/V1/{cid}/{cid}.result.json", encoding="utf-8"))
    v3 = json.load(io.open(rf".fw-validation/artifacts/V3/wsl/{cid}/{cid}.result.json", encoding="utf-8"))
    c1, c3 = v1.get("comparison") or {}, v3.get("comparison") or {}
    flags = {}
    for k in sorted(set(c1) | set(c3)):
        a, b = c1.get(k), c3.get(k)
        if a != b:
            flags[k] = {"windows": a, "wsl": b}
    match = v1.get("status") == v3.get("status")
    same += match
    diff["cases"][cid] = {
        "windows_status": v1.get("status"), "wsl_status": v3.get("status"),
        "status_match": match,
        "comparison_flags_differing": flags,
        "comparison_flags_identical": not flags
    }
diff["summary"] = {
    "cases": len(CASES), "status_matches": same,
    "parity": f"{same}/{len(CASES)} identical classifications Windows vs WSL",
    "materially_identical_comparison_flags": all(not c["comparison_flags_differing"] for c in diff["cases"].values()),
    "notes": [
        "F-V1-001 (G02), F-V1-006 (G10) and the G03/G06/G07 defects reproduce on BOTH platforms — platform-independent product defects",
        "G03 explain_condition resolved a different conditional context across runs (nondeterminism noted for V5)",
        "MCP SDK version differs per venv (1.28.1 vs 1.30.0) — environment fact, not product drift",
        "G04/G11 remain WSL-only (Windows toolchain absent) — BLOCKED_ENVIRONMENT recorded per plan"
    ]
}
io.open(OUT, "w", encoding="utf-8").write(json.dumps(diff, indent=2, ensure_ascii=True) + "\n")
print(json.dumps(diff["summary"], indent=1))
