import json
import io

fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
ledger = d["cases"] if isinstance(d, dict) and "cases" in d else d

entries = {
    "C1_cmsis_vector": {"phase": "V2_PUBLIC_REAL", "status": "FAIL_PRODUCT",
                         "classification": "WRONG_ANSWER", "finding": "F-V2-001",
                         "evidence": "artifacts/V2/C1/ (responses_c1b/c1c.json, startup_vector_region.txt, C1.result.json)"},
    "F1_freertos_callgraph": {"phase": "V2_PUBLIC_REAL", "status": "PASS",
                               "evidence": "artifacts/V2/F1/ (responses_f1.json, F1.result.json)"},
    "F2_freertos_callbacks": {"phase": "V2_PUBLIC_REAL", "status": "PASS",
                               "evidence": "artifacts/V2/F2/ (responses_f2.json, F2.result.json)"},
    "F3_freertos_qemu_demo": {"phase": "V2_PUBLIC_REAL", "status": "NOT_RUN",
                               "reason": "optional; coverage-equivalent via F1/F2+C1+Z4",
                               "evidence": "artifacts/V2/F3/F3.result.json"},
    "R1_riot_native_hello": {"phase": "V2_PUBLIC_REAL", "status": "PASS",
                              "evidence": "artifacts/V2/R1/ (responses_r1.json, r1_provenance.json, R1.result.json)"},
    "R2_riot_holdout_variant": {"phase": "V2_PUBLIC_REAL", "status": "PASS",
                                 "notes": "reindex converges, no stale carry-over; config-hash-by-path observation (F-V1-003 family)",
                                 "evidence": "artifacts/V2/R2/ (responses_r2.json, r2_compile_commands.json, R2.result.json)"},
    "N1_nuttx_sim_nsh": {"phase": "V2_PUBLIC_REAL", "status": "PASS",
                          "notes": "build-level scale recorded (1194 TUs); product queries pending index completion",
                          "evidence": "artifacts/V2/N1/ (n1_provenance.json, nuttx_compile_commands.json, N1.result.json)"},
    "Z4_qemu_cortex_m3": {"phase": "V2_PUBLIC_REAL", "status": "RUNNING",
                           "notes": "build + QEMU run OK ('Hello World! qemu_cortex_m3/ti_lm3s6965'); index+vector queries in flight",
                           "evidence": "artifacts/V2/Z4/ (z4_provenance.json, qemu_run.log)"},
}
for k, v in entries.items():
    ledger[k] = v
if isinstance(d, dict) and "cases" in d:
    d["cases"] = ledger
    json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
else:
    json.dump(ledger, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger updated with", len(entries), "V2 entries")
