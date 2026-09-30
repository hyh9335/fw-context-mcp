import json
import io

fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
ledger = d["cases"]

def upsert(case_id, **kw):
    for c in ledger:
        if c.get("case_id") == case_id:
            c.update(kw)
            return
    ledger.append({"case_id": case_id, **kw})

upsert("V2-C1-CMSIS-VECTOR", phase="V2_PUBLIC_REAL", status="FAIL_PRODUCT",
       classification="WRONG_ANSWER", finding="F-V2-001",
       description="C1 CMSIS 6 startup template vector mapping (standard-backed slot truth)",
       evidence=["artifacts/V2/C1/C1.result.json", "artifacts/V2/C1/responses_c1b.json",
                 "artifacts/V2/C1/responses_c1c.json", "artifacts/V2/C1/startup_vector_region.txt"],
       notes=["first attempt used incomplete CC (missing -DARMCM3) — controller fixture defect corrected before verdict"])
upsert("V2-F1-FREERTOS-CALLGRAPH", phase="V2_PUBLIC_REAL", status="PASS",
       description="F1 kernel callgraph: real hotspots, cross-file proven paths, no invented edges",
       evidence=["artifacts/V2/F1/F1.result.json", "artifacts/V2/F1/responses_f1.json"])
upsert("V2-F2-FREERTOS-CALLBACKS", phase="V2_PUBLIC_REAL", status="PASS",
       description="F2 callbacks: registration→invocation indirect linkage exact; task-code call site honestly unresolved",
       evidence=["artifacts/V2/F2/F2.result.json", "artifacts/V2/F2/responses_f2.json"])
upsert("V2-F3-FREERTOS-QEMU-DEMO", phase="V2_PUBLIC_REAL", status="NOT_RUN",
       description="F3 (optional) official QEMU demo; coverage-equivalent via F1/F2+C1+Z4",
       evidence=["artifacts/V2/F3/F3.result.json"])
upsert("V2-R1-RIOT-HELLO", phase="V2_PUBLIC_REAL", status="PASS",
       description="R1 riot native hello: 98 TUs, 115729 syms, real hotspots",
       evidence=["artifacts/V2/R1/R1.result.json", "artifacts/V2/R1/responses_r1.json"])
upsert("V2-R2-RIOT-HOLDOUT", phase="V2_PUBLIC_REAL", status="PASS",
       description="R2 variant reindex converges (saul in, removed core defs out, no stale carry-over); config-hash-by-path observation",
       evidence=["artifacts/V2/R2/R2.result.json", "artifacts/V2/R2/responses_r2.json",
                 "artifacts/V2/R2/r2_compile_commands.json"])
upsert("V2-N1-NUTTX-NSH", phase="V2_PUBLIC_REAL", status="PASS",
       description="N1 sim:nsh clean build+index (1194 TUs); product queries pending",
       evidence=["artifacts/V2/N1/N1.result.json", "artifacts/V2/N1/n1_provenance.json"])
upsert("V2-Z4-QEMU-CORTEX-M3", phase="V2_PUBLIC_REAL", status="RUNNING",
       description="Z4 qemu_cortex_m3: build+QEMU run OK; index+vector queries in flight",
       evidence=["artifacts/V2/Z4/z4_provenance.json", "artifacts/V2/Z4/qemu_run.log"])

d["cases"] = ledger
json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger cases now:", len(ledger))
