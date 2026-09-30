import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))
f = {
    "id": "F-V1-006",
    "phase": "V1_GOLDEN",
    "area": "vector-table-data-slots",
    "severity": "MEDIUM",
    "summary": "G10 (Cortex-M CMSIS vector table): get_vector_table correctly recognizes the 'vectors' array and names all 8 handler slots exactly (1=Reset_Handler, 2=NMI, 3=HardFault, 11=SVC, 14=PendSV, 15=SysTick, 16=IRQ0, 17=IRQ1) and correctly leaves reserved zero slots (4-10, 12, 13) unnamed. Slot 0 — the initial stack pointer (const pFunc vectors[] = { (pFunc)&__initial_sp, ... }) — is NOT resolved: coverage says 'No name in slots 0'. The underlying index HAS the evidence: symbols holds __initial_sp (varglobal) and refs holds the initializer reference (startup.c:13, ref_kind=ref), so the data-symbol slot is resolvable but the c-source table extraction only emits function-typed words. Consumers reading the table cannot see the initial-SP slot; the coverage note discloses the gap but not the recoverable name.",
    "classification": "SILENT_DATA_LOSS",
    "status": "OPEN",
    "case": "G10_cmsis_vector",
    "evidence": [
        ".fw-validation/artifacts/V1/G10/G10.result.json (slot_map_exact=false, initial_sp_is_data_slot0=false; 8 handler slots exact)",
        ".fw-validation/artifacts/V1/G10/raw/responses.json (get_vector_table coverage: 'No name in slots 0, 4-10, 12-13')",
        "G10 index DB: symbols __initial_sp varglobal id=10; refs row to_usr=c:@__initial_sp from_file=startup.c:13 ref_kind=ref (.fw-validation/inspect_g10.py output)"
    ],
    "repro": "python .fw-validation/v1_runner.py G10"
}
if f["id"] not in {x["id"] for x in d["findings"]}:
    d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [x["id"] for x in d["findings"]])
