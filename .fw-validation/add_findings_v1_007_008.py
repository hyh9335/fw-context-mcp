import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))
new = [
    {
        "id": "F-V1-007",
        "phase": "V1_GOLDEN",
        "area": "stored-function-bodies",
        "severity": "HIGH",
        "summary": "Any function whose entire definition spans a single source line (end_line == line) stores symbols.source='' — its body is invisible to search_bodies and any body-semantic query. Cross-case DB evidence: G01 master_entry/slave_entry (1 line, srclen=0) vs feature_value/role_value (7 lines, stored); G03 helper (line 14-14, srclen=0) vs selected (lines 4-12, stored 44-80 chars); G12 active_line (1 line, srclen=0); G13 shared_value/f_000 (all single-line, srclen=0). get_source masks the symptom by falling back to disk (source_origin='disk'), but search_bodies — the documented definition-text surface — returns nothing for every one-liner function. This is the GENERAL root cause behind F-V1-002's narrower description: the G03 'helper' failure was an instance of single-line emptiness, not of conditional wrapping (G13 has no conditionals at all and shows the same emptiness). Consumers lose body-semantic search for a very common C style (one-line accessors/inlines). Classification: SILENT_DATA_LOSS (broadened trigger vs F-V1-002; G03 classification unchanged).",
        "classification": "SILENT_DATA_LOSS",
        "status": "OPEN",
        "amends": "F-V1-002 (trigger analysis broadened; G03 verdict unchanged)",
        "case": "G13_fanout_scale (discovered) + G01/G03/G12 (cross-confirmed)",
        "evidence": [
            ".fw-validation/inspect_g13db.py output: G13 symbols source length 0 for shared_value AND f_000",
            ".fw-validation/inspect_g01db.py output: G01 7-line functions stored (75/68/76/64 chars), 1-line functions empty",
            ".fw-validation/inspect_g03db.py output: helper line 14-14 srclen=0; selected lines 4-12 stored",
            "G12 DB: active_line srclen=0; G13 pristine probe: search_bodies 'shared_value' -> [] while get_source returns disk body",
            "artifacts/V1/G13/G13.result.json notes"
        ],
        "repro": "Index any project with a one-line function definition (e.g. 'static inline int f(void){return 7;}'), then search_bodies query 'f' or 'return 7' -> []; get_source 'f' -> source_origin 'disk'."
    },
    {
        "id": "F-V1-008",
        "phase": "V1_GOLDEN",
        "area": "result-pagination",
        "severity": "LOW",
        "summary": "lookup_symbol/search_bodies/search_content cap results at limit<=100 (schema max) with no offset/page token, so a consumer cannot enumerate more than 100 same-name symbol rows in any single or composed query. G13 (n=250) needs 250 shared_value context rows; every query returns at most 100. The uncapped aggregate get_active_build.file_count==2n still evidences all 250 contexts, but per-row enumeration of >100 same-name symbols is impossible on the query surface. Impact: fanout audits and same-name symbol inventories at scale (>100) silently truncate.",
        "classification": "SILENT_DATA_LOSS",
        "status": "OPEN",
        "case": "G13_fanout_scale",
        "evidence": [
            "artifacts/V1/G13/G13.result.json: rows_p=100 for n=100 and n=250 (cap), file_count 200/500 proves contexts exist",
            "lookup_symbol schema: limit default 50 max 100; no offset parameter on any query tool"
        ],
        "repro": "Generate 250 TUs sharing one header (G13 generate.py 250); lookup_symbol name=shared_value limit=100 -> exactly 100 rows; no parameter reaches the remaining 150."
    }
]
have = {x["id"] for x in d["findings"]}
for f in new:
    if f["id"] not in have:
        d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [x["id"] for x in d["findings"]])
