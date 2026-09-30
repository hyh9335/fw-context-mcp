import json
import io
from pathlib import Path

path = Path(r".fw-validation/case_ledger.json")
d = json.load(io.open(path, encoding="utf-8"))

for c in d["cases"]:
    if c["case_id"] == "G13_fanout_scale":
        c.update({
            "description": "Fanout scale 1/10/50/100/250 + shared-header mutation invariant "
                           "(incremental_after_shared_header_mutation == clean_full)",
            "status": "PASS",
            "exit_code": 0,
            "evidence": [
                "artifacts/V1/G13/G13.result.json (definitive comparison v5: all 9 checks true at "
                "every size; file_count==2n uncapped context evidence 2/20/100/200/500; "
                "modified_files_count==0 on both incremental and clean-full; invariant holds "
                "incremental==clean_full on rows/f_/file_count/symbol_count/phrase-value surfaces)",
                "first-pass FAIL_PRODUCT was a harness artifact (sort_keys key mismatch in runner, "
                "default limit=50 on lookups, OR-widened body-text probe) — superseded by v5 with "
                "daemon-disciplined requeries and exact-phrase value probes",
                "observations recorded as F-V1-007 (single-line bodies not stored) and F-V1-008 "
                "(100-row cap, no pagination)"
            ],
        })
    if c["case_id"] == "G11_riscv_mtvec":
        c["evidence"] = c["evidence"] + [
            "verifier note: Windows-side raw G11.result.json FAIL_PRODUCT label is more precisely "
            "BLOCKED_ENVIRONMENT (missing clang for .S preprocessing); ledger carries the correct "
            "effective classification (wsl PASS)"]
        c["status_windows_side"] = "BLOCKED_ENVIRONMENT"

d.setdefault("planned_required_case_ids", [])
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger updated:",
      {c["case_id"]: c["status"] for c in d["cases"] if c["phase"] == "V1_GOLDEN"})
