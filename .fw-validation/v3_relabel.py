import json
import io

# --- ledger: fix V3-G04/G11 evidence paths + relabel V3 entries platform wording
lp = r".fw-validation/case_ledger.json"
d = json.load(io.open(lp, encoding="utf-8"))
for c in d["cases"]:
    if c.get("case_id") in ("V3-G04", "V3-G11"):
        cid = c["case_id"].split("-")[1]
        c["evidence"] = [f"artifacts/V1/{cid}/wsl/{cid}.result.json"]
        if c["case_id"] == "V3-G04":
            c["description"] = "G04_variant_semantic: Windows side BLOCKED (toolchain absent); WSL semantic coverage = V1 wsl run (FAIL_PRODUCT, F-V1-003)"
        else:
            c["description"] = "G11_riscv_mtvec: Windows side BLOCKED (RISC-V toolchain absent); WSL semantic coverage = V1 wsl run (PASS)"
    if str(c.get("case_id", "")).startswith("V3-") and c["case_id"] not in ("V3-G04", "V3-G11"):
        c["description"] = (c.get("description") or "").replace("WSL PASS", "V1-Windows PASS").replace(
            "WSL FAIL_PRODUCT", "V1-Windows FAIL_PRODUCT")
        c["description"] = c["description"].replace(
            "parity SAME (WSL", "parity SAME (Windows-vs-WSL: V1 baseline")
json.dump(d, io.open(lp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger V3 entries fixed")

# --- state: correct the parity wording
sp = r".fw-validation/state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V3_CROSS_PLATFORM"]["parity"] = (
    "8/8 identical classifications WINDOWS(V1 baseline) vs WSL(V3-WSL leg); "
    "PARITY_DIFF.json emitted; G04/G11 Windows-BLOCKED with WSL coverage from V1")
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state wording fixed")
