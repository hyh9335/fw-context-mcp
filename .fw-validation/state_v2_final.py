import json
import io

sp = r".fw-validation/state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V2_PUBLIC_REAL"] = {
    "status": "CONTROLLER_COMPLETE_PENDING_VERIFIER",
    "tally": {"PASS": 11, "FAIL_PRODUCT": 1, "NOT_RUN_optional": 1,
              "decided": ["F1", "F2", "C1", "R1", "R2", "N1", "N2", "N3", "Z1", "Z2", "Z3", "Z4", "F3"]},
    "findings_opened": ["F-V2-001", "F-V2-002"],
    "verifier": None
}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state V2:", s["phases"]["V2_PUBLIC_REAL"]["status"])
