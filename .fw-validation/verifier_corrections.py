import json
import io

A = r".fw-validation/artifacts/V2"


def load(p):
    return json.load(io.open(p, encoding="utf-8", errors="replace"))


def save(p, o):
    io.open(p, "w", encoding="utf-8").write(json.dumps(o, indent=2, ensure_ascii=True) + "\n")


# 1) N3.result.json — disclose failed attempt-1, cite corrected dump
n3 = load(f"{A}/N3/N3.result.json")
n3["results"]["direct_oracle"] = "n3_post_db_corrected.json (delta=1, probe is_definition=1, body stored=true, refs delta +1)"
n3["method_notes"].append(
    "VERIFIER CORRECTION: the original n3_post_db.json is the FAILED attempt-1 artifact (wrong --project); "
    "the corrected direct oracle is n3_post_db_corrected.json, produced by direct DB inspection after the "
    "verifier audit. Indirect corroboration also present in responses_n2.json (+1 on the same N1 index).")
n3["evidence_grade"] = "PASS (direct oracle restored post-audit; initially indirect-only)"
save(f"{A}/N3/N3.result.json", n3)

# 2) R1 file_count correction 10721 -> 9755 (10721 is R2's post-reindex figure)
r1 = load(f"{A}/R1/R1.result.json")
def fix_fc(o):
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k == "file_count" and v == 10721:
                o[k] = 9755
            else:
                fix_fc(v)
    elif isinstance(o, list):
        for v in o:
            fix_fc(v)
fix_fc(r1)
r1.setdefault("notes", []).append(
    "VERIFIER CORRECTION: file_count 10721 was the R2 post-reindex figure; R1's own get_active_build says 9755. Fixed.")
save(f"{A}/R1/R1.result.json", r1)

# 3) Z2/Z4 index-health disclosure
z2 = load(f"{A}/Z2/Z2.result.json")
z2.setdefault("notes", []).append(
    "VERIFIER DISCLOSURE: get_active_build here carries reindex_needed=true / stale=true (3 generated TUs outside "
    "compile_commands.json); queries still served correct results.")
save(f"{A}/Z2/Z2.result.json", z2)

z4 = load(f"{A}/Z4/Z4.result.json")
z4.setdefault("notes", []).append(
    "VERIFIER DISCLOSURE: get_active_build carries reindex_needed=true / stale=true (3 generated TUs outside "
    "compile_commands.json), same condition Z3 discloses; queries unaffected. This file re-serialized strict UTF-8.")
save(f"{A}/Z4/Z4.result.json", z4)

# 4) Z3 include_possible scoping note
z3 = load(f"{A}/Z3/Z3.result.json")
z3.setdefault("notes", []).append(
    "VERIFIER SCOPING NOTE: find_call_path ran include_possible=false (product hinted 2661 possible rows, not "
    "traversed); the no-invented-edge claim is scoped to PROVEN edges. Traversing possible edges would test a "
    "different, weaker property and was intentionally not archived.")
save(f"{A}/Z3/Z3.result.json", z3)

# 5) N2 cross-reference of the N1 1-symbol drift
n2 = load(f"{A}/N2/N2.result.json")
n2["analysis"] += (" Note: N1's count here (1494310) postdates the N3 probe (+1) vs N1.result.json (1494309, "
                    "pre-probe snapshot); same index, different times.")
save(f"{A}/N2/N2.result.json", n2)

# 6) ledger: R1 file_count fix + verifier signoff marker
lp = r".fw-validation/case_ledger.json"
d = json.load(io.open(lp, encoding="utf-8"))
for c in d["cases"]:
    if c.get("case_id") == "V2-R1-RIOT-HELLO":
        for e in c.get("evidence", []):
            pass
        c["notes"] = (c.get("notes") or "").replace("10721", "9755") if isinstance(c.get("notes"), str) else c.get("notes")
        if isinstance(c.get("notes"), dict):
            pass
    if c.get("case_id", "").startswith("V2-R1"):
        s = json.dumps(c, ensure_ascii=True)
        s = s.replace("10721 files", "9755 files").replace("file_count=10721", "file_count=9755")
        c.clear(); c.update(json.loads(s))
json.dump(d, io.open(lp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

# 7) state.json: verifier signoff + final phase entry
sp = r".fw-validation/state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V2_PUBLIC_REAL"] = {
    "status": "SIGN_OFF",
    "verifier": "1299923d-eb43-460c-8f53-7157b60b3920 (independent subagent)",
    "verdict": "SIGN_OFF",
    "tally": {"PASS": 11, "FAIL_PRODUCT": 1, "NOT_RUN_optional": 1},
    "findings_opened": ["F-V2-001", "F-V2-002"],
    "corrections_applied": ["N3 corrected direct oracle archived (n3_post_db_corrected.json)",
                             "R1 file_count 10721->9755", "Z2/Z4 reindex_needed disclosed",
                             "Z3 include_possible scoping note", "N1/N2 +1 drift cross-referenced",
                             "Z4 re-serialized strict UTF-8"],
    "v5_notes": ["FreeRTOS linker/map-truth residual (F3 NOT_RUN) to appear in coverage matrix",
                  "N3 evidence grade now direct"]
}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("all verifier corrections applied; V2 SIGN_OFF recorded")
