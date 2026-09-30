import json
import io

fp = r".fw-validation/findings.json"
d = json.load(io.open(fp, encoding="utf-8"))
findings = d["findings"] if isinstance(d, dict) and "findings" in d else d

new = {
    "id": "F-V2-001",
    "phase": "V2_PUBLIC_REAL",
    "case": "C1_cmsis_vector",
    "severity": "WRONG_ANSWER",
    "title": "get_vector_table returns false 'No vector table' for the standard CMSIS 6 startup template C table",
    "reproduction": {
        "fixture": "cmsis_6@45dab712 CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3/startup_ARMCM3.c",
        "cc": ["clang", "-ICMSIS/Core/Include", "-IRTE", "-DARMCM3", "-DCMSIS_DEV", "-c", "startup_ARMCM3.c"],
        "index": "fw-context index --no-analyze --no-embeddings",
        "query": "get_vector_table {}"
    },
    "expected": "vector table recognized: __VECTOR_TABLE[240] with slot0=__INITIAL_SP, Reset_Handler, NMI/HardFault/... , Interrupt0-9, zero-filled tail (oracle dump artifacts/V2/C1/startup_vector_region.txt)",
    "observed": [
        "get_vector_table -> 'No vector table in this build... no array of function addresses was recognised'",
        "the table IS indexed as a symbol: lookup __VECTOR_TABLE (exact) -> varglobal startup_ARMCM3.c:80",
        "element type collapsed: signature 'const int[240]' (typedef'd function-pointer VECTOR_TABLE_Type lost)",
        "search_bodies on the array initializer -> empty (F-V1-007 class: stored body empty)",
        "find_references __INITIAL_SP -> 'Symbol not found' (slot-0 data symbol unresolved, F-V1-006 class)"
    ],
    "analysis": "conditional branches evaluate correctly (ARMCM3 if=1, __GNUC__ if=1) — the table region IS in the "
                "active view; the vector-table shape recognizer rejects the standard CMSIS 6 template form: "
                "typedef'd function-pointer element type + cast first element ((VECTOR_TABLE_Type)(&__INITIAL_SP)) "
                "+ integer-0 reserved slots + __VECTOR_TABLE_ATTRIBUTE macro. The false 'no table' claim is a "
                "WRONG_ANSWER (presence claimed absent), not merely a silent omission.",
    "classification": "FAIL_PRODUCT",
    "first_observed": "V2_PUBLIC_REAL",
    "cross_case_evidence": ["F-V1-006 (slot-0 data symbol class)", "F-V1-007 (empty stored body class)"]
}

if not any(f.get("id") == "F-V2-001" for f in findings):
    findings.append(new)
    d["findings"] = findings
    json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("F-V2-001 recorded; total findings:", len(findings))
else:
    print("F-V2-001 already present")
