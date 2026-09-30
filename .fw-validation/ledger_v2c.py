import json
import io

# ledger: final statuses for Z3, Z4, N2, N3
fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
ledger = d["cases"]

def upsert(case_id, **kw):
    for c in ledger:
        if c.get("case_id") == case_id:
            c.update(kw)
            return
    ledger.append({"case_id": case_id, **kw})

upsert("V2-Z1-ZEPHYR-HELLO", phase="V2_PUBLIC_REAL", status="PASS",
       description="Z1 native_sim hello: 75 TUs, 85975 syms/8730 files, main weak-override chain correct",
       evidence=["artifacts/V2/Z1/Z1.result.json", "artifacts/V2/Z1/responses_z1.json"])
upsert("V2-Z2-ZEPHYR-VARIANT", phase="V2_PUBLIC_REAL", status="PASS",
       description="Z2 CONFIG_ASSERT=y: variant separation confirmed (distinct config_hash); F-V2-002 exact-fallback contract finding",
       evidence=["artifacts/V2/Z2/Z2.result.json", "artifacts/V2/Z2/responses_z2_variants.json"])
upsert("V2-Z3-ZEPHYR-WORKQ", phase="V2_PUBLIC_REAL", status="PASS",
       description="Z3 workq: no invented CALL edges across async boundary; thread entry honestly callerless; generated-TU warning honest",
       evidence=["artifacts/V2/Z3/Z3.result.json", "artifacts/V2/Z3/responses_z3.json"])
upsert("V2-Z4-QEMU-CORTEX-M3", phase="V2_PUBLIC_REAL", status="PASS",
       description="Z4 real Cortex-M: build+QEMU run OK; 97-slot vector table fully recognized (assembly form) — sharpens F-V2-001 to C-template gap",
       evidence=["artifacts/V2/Z4/Z4.result.json", "artifacts/V2/Z4/responses_z4.json", "artifacts/V2/Z4/qemu_run.log"])
upsert("V2-N2-NUTTX-VARIANT-PAIR", phase="V2_PUBLIC_REAL", status="PASS",
       description="N2 smp vs nsh: distinct config_hash, Δ63k symbols, distinct hotspot profiles; 125-key config diff",
       evidence=["artifacts/V2/N2/N2.result.json", "artifacts/V2/N2/n2_variant_facts.json", "artifacts/V2/N2/responses_n2.json"])
upsert("V2-N3-NUTTX-INCREMENTAL", phase="V2_PUBLIC_REAL", status="PASS",
       description="N3 mutation pickup: delta exactly +1, probe fn + body stored, ~30s pickup; harness note on --project root",
       evidence=["artifacts/V2/N3/N3.result.json", "artifacts/V2/N3/n3_pre_db.json"])
upsert("V2-N1-NUTTX-NSH", phase="V2_PUBLIC_REAL", status="PASS",
       description="N1 sim:nsh final: 1194 TUs, 1.49M symbols/58254 files, nx_start definition + 937k-edge hotspot survived",
       evidence=["artifacts/V2/N1/N1.result.json", "artifacts/V2/N1/responses_n1.json"])
d["cases"] = ledger
json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger cases:", len(ledger))

# state.json
sp = r".fw-validation/state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V2_PUBLIC_REAL"]["status"] = "CONTROLLER_COMPLETE_PENDING_VERIFIER"
s["phases"]["V2_PUBLIC_REAL"]["tally"] = {
    "PASS": 11, "FAIL_PRODUCT": 1, "NOT_RUN_optional": 1,
    "decided": ["F1","F2","C1","R1","R2","N1","N2","N3","Z1","Z2","Z3","Z4","F3"]}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state updated:", s["phases"]["V2_PUBLIC_REAL"]["status"])
