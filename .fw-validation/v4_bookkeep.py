import json
import io
from pathlib import Path

ROOT = Path(r"D:\github\fw-context-mcp\.fw-validation")

# --- ledger: append 8 V4 cases
lp = ROOT / "case_ledger.json"
d = json.load(io.open(lp, encoding="utf-8"))
have = {c.get("case_id") for c in d["cases"]}
V4 = {
    "V4-A1": ("A1_shared_header_change", "PASS", ["artifacts/V4/A1_shared_header_change/A1_shared_header_change.result.json"]),
    "V4-A2": ("A2_macro_config_change", "PASS", ["artifacts/V4/A2_macro_config_change/A2_macro_config_change.result.json"]),
    "V4-A3": ("A3_build_variant_switch", "PASS", ["artifacts/V4/A3_build_variant_switch/A3_build_variant_switch.result.json"]),
    "V4-A4": ("A4_source_delete_add", "PASS", ["artifacts/V4/A4_source_delete_add/A4_source_delete_add.result.json"]),
    "V4-A5": ("A5_stale_noop_reindex", "PASS", ["artifacts/V4/A5_stale_noop_reindex/A5_stale_noop_reindex.result.json"]),
    "V4-A6": ("A6_reindex_x3_idempotence", "PASS", ["artifacts/V4/A6_reindex_x3_idempotence/A6_reindex_x3_idempotence.result.json"]),
    "V4-A7": ("A7_unsupported_arch_query", "PASS", ["artifacts/V4/A7_unsupported_arch_query/A7_unsupported_arch_query.result.json"]),
    "V4-A8": ("A8_malformed_env", "PASS", ["artifacts/V4/A8_malformed_env/A8_malformed_env.result.json"]),
}
for cid, (slug, status, ev) in V4.items():
    if cid in have:
        continue
    d["cases"].append({
        "case_id": cid, "phase": "V4_ADVERSARIAL", "slug": slug, "platform": "windows",
        "status": status, "evidence": ev,
        "description": f"V4 mutation class on small golden fixture: {slug} — 8/8 PASS, real stdio MCP + direct DB oracles",
    })
json.dump(d, io.open(lp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger cases:", len(d["cases"]))

# --- state: V3 verifier delivered rev-2 evidence; V4 controller-complete
sp = ROOT / "state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V3_CROSS_PLATFORM"]["status"] = "SIGN_OFF_PENDING_REAUDIT"
s["phases"]["V4_ADVERSARIAL"] = {
    "status": "CONTROLLER_COMPLETE_PENDING_VERIFIER",
    "cases": 8, "pass": 8, "fail": 0,
    "summary": "artifacts/V4/V4_SUMMARY.md — 8/8 PASS on small golden fixtures, real entrypoints, direct DB oracles",
}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state updated")
