import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))
f = {
    "id": "F-V1-004",
    "phase": "V1_GOLDEN",
    "area": "callback-registration-flow",
    "severity": "MEDIUM",
    "summary": "G06 (callbacks.c): direct function-pointer flows resolve correctly (global_cb->plus2 via variable initializer, ops.fn->plus1 via designated initializer, correct indirect call sites, plus1/plus2 reachable via indirect refs, and the opaque parameter cb is NOT claimed to have a definite callee target). The transitive registration flow is missing: find_indirect_targets('registered') returns only the parameter row (registered = cb inside register_cb, certainty=exact) and never reaches plus1 through the registration call argument at setup() line 16 (register_cb(plus1)). expected.json may_targets.invoke_registered = ['plus1'] with evidence_required = registration_site — the may-target through the registration API is unobservable, so invoke_registered's reachable callback set is silently under-reported.",
    "classification": "SILENT_DATA_LOSS",
    "status": "OPEN",
    "case": "G06_indirect_callbacks",
    "evidence": [
        ".fw-validation/artifacts/V1/G06/G06.result.json (invoke_registered_may_plus1=false; all other cells true)",
        ".fw-validation/artifacts/V1/G06/raw/responses.json (find_indirect_targets registered -> single row rhs_name='cb' method=assignment assign_line=13; no call_arg row for setup:16 register_cb(plus1))",
        ".fw-validation/peek_g0506.py output"
    ],
    "repro": "python .fw-validation/v1_runner.py G06"
}
if f["id"] not in {x["id"] for x in d["findings"]}:
    d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [x["id"] for x in d["findings"]])
