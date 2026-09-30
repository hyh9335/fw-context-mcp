import json
import io

path = r".fw-validation/state.json"
d = json.load(io.open(path, encoding="utf-8"))
d["phases"]["V1_GOLDEN"] = "CONTROLLER_RUNNING"

# controller progress block for V1
d.setdefault("controller_progress", {})
d["controller_progress"]["V1_GOLDEN"] = {
    "updated": "2026-09-18",
    "cases_done": {
        "G01": "PASS", "G02": "FAIL_PRODUCT (F-V1-001)",
        "G03": "FAIL_PRODUCT (F-V1-002)", "G04": "FAIL_PRODUCT (F-V1-003, wsl)",
        "G05": "PASS", "G06": "FAIL_PRODUCT (F-V1-004)",
        "G07": "FAIL_PRODUCT (F-V1-005)", "G08": "PASS", "G09": "PASS",
        "G10": "FAIL_PRODUCT (F-V1-006)", "G11": "PASS (wsl)", "G12": "PASS",
    },
    "pending": ["G13_fanout_scale (running in background)"],
    "runner": ".fw-validation/v1_runner.py (G01-G10,G12 recipes; G04/G11/G13 custom flows)",
    "notes": [
        "G04/G11 ran on WSL: bare generator needs gcc in PATH; .S preprocessing needs clang binary (Windows BLOCKED)",
        "findings F-V1-001..F-V1-006 recorded in findings.json; ledger updated",
        "V1 verifier per A_VERIFY.md still required before phase close"
    ],
}
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state updated")
