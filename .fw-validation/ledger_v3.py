import json
import io

CASES = {
    "V3-G01": ("G01_multi_tu_header", "PASS", "parity SAME (WSL PASS -> WIN PASS)"),
    "V3-G02": ("G02_same_source_multi_command", "PARITY_CONFIRMED", "parity SAME (WSL FAIL_PRODUCT -> WIN FAIL_PRODUCT, identical comparison flags)"),
    "V3-G03": ("G03_conditional_matrix", "PARITY_CONFIRMED", "parity SAME (WSL FAIL_PRODUCT -> WIN FAIL_PRODUCT)"),
    "V3-G05": ("G05_cpp_override_template", "PASS", "parity SAME (WSL PASS -> WIN PASS)"),
    "V3-G06": ("G06_indirect_callbacks", "PARITY_CONFIRMED", "parity SAME (WSL FAIL_PRODUCT -> WIN FAIL_PRODUCT)"),
    "V3-G07": ("G07_dead_keep", "PARITY_CONFIRMED", "parity SAME (WSL FAIL_PRODUCT -> WIN FAIL_PRODUCT)"),
    "V3-G10": ("G10_cmsis_vector", "PARITY_CONFIRMED", "parity SAME (WSL FAIL_PRODUCT -> WIN FAIL_PRODUCT; C-template gap platform-independent)"),
    "V3-G12": ("G12_raw_crlf", "PASS", "parity SAME (WSL PASS -> WIN PASS; CRLF handled on native Windows)"),
    "V3-G04": ("G04_variant_semantic", "BLOCKED_ENVIRONMENT", "Windows side blocked per pack plan (WSL V1 covers semantics)"),
    "V3-G11": ("G11_riscv_mtvec", "BLOCKED_ENVIRONMENT", "Windows side blocked (RISC-V toolchain unavailable on Windows; WSL V1 covers)"),
}

fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
ledger = d["cases"]

def upsert(case_id, **kw):
    for c in ledger:
        if c.get("case_id") == case_id:
            c.update(kw)
            return
    ledger.append({"case_id": case_id, **kw})

for cid, (name, status, desc) in CASES.items():
    upsert(cid, phase="V3_CROSS_PLATFORM", status=status,
           description=f"{name}: {desc}",
           evidence=[f"artifacts/V3/{name.split('_')[0]}/{name.split('_')[0]}.result.json",
                      f"artifacts/V1/{name.split('_')[0]}/{name.split('_')[0]}.result.json"])
d["cases"] = ledger
json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger cases:", len(ledger))

sp = r".fw-validation/state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V3_CROSS_PLATFORM"] = {
    "status": "CONTROLLER_COMPLETE_PENDING_VERIFIER",
    "parity": "8/8 identical classifications WSL vs Windows",
    "windows_blocked": ["G04", "G11"],
    "verifier": None
}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state V3:", s["phases"]["V3_CROSS_PLATFORM"]["status"])
