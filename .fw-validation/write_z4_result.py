import json
import io

ART = r".fw-validation/artifacts/V2"

z4 = {
    "case_id": "Z4_qemu_cortex_m3", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "Z4 qemu_cortex_m3 real cross-build: compile DB + startup/vector facts + QEMU runtime smoke",
    "fixture": "zephyr v4.4.2@dccb0959 hello_world BOARD=qemu_cortex_m3, gnuarmemb (/usr arm-none-eabi-gcc)",
    "env_method": ["work-copy zephyr (public clone untouched) + west workspace; GIT_SSL_NO_VERIFY=1; west update cmsis_6 cmsis",
                   "earlier attempts: no-west build fails on HAS_CMSIS_CORE Kconfig warning (module registration missing) — documented"],
    "build": {"tu_count_prov": "z4_provenance.json", "qemu_run": "QEMU smoke OK: 'Hello World! qemu_cortex_m3/ti_lm3s6965' (qemu_run.log)"},
    "queries": {
        "get_vector_table": {"total_slots": 97,
                              "status_mix": {"c": 45, "dispatcher": 43, "assembly": 8, "data": 1},
                              "slot0": "z_main_stack (data, SP — from vector_table.S:39 assembly)",
                              "slot1": "z_arm_reset (assembly, reset.S)",
                              "slot2": "z_arm_nmi (c)",
                              "table_file": "arch/arm/core/cortex_m/vector_table.S:39+ (assembly table of address words)"},
        "z_arm_reset": "reset.S:83 is_definition=true", "__start": "reset.S:91 is_definition=true",
        "main": "samples/hello_world/src/main.c:9 is_definition=true",
        "config_hash": "1ff587fd84ad01b4…"
    },
    "contrast_with_C1": "on the SAME product, the ASSEMBLY vector table (zephyr vector_table.S) is recognized "
                        "slot-by-slot with named handlers, while the CMSIS 6 C-template array form (C1) is not — "
                        "F-V2-001 is scoped to the C array-of-function-pointers template recognition, not vector "
                        "support in general",
    "status": "PASS"
}
open(rf"{ART}/Z4/Z4.result.json", "w").write(json.dumps(z4, indent=2, ensure_ascii=False) + "\n")

# sharpen F-V2-001 with the Z4 contrast
fp = r".fw-validation/findings.json"
d = json.load(io.open(fp, encoding="utf-8"))
for f in d["findings"]:
    if f["id"] == "F-V2-001":
        f["analysis"] += (" CONTRAST (Z4): on the same product build (qemu_cortex_m3, zephyr), the ASSEMBLY vector "
                          "table is fully recognized (97 slots: SP data slot, z_arm_reset, faults, _isr_wrapper "
                          "dispatchers) — the recognition gap is specific to the CMSIS 6 C-template form "
                          "(typedef'd function-pointer array with cast initial element).")
        f["cross_case_evidence"].append("V2-Z4 (assembly table form recognized — contrast)")
json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("Z4.result.json written; F-V2-001 sharpened")
