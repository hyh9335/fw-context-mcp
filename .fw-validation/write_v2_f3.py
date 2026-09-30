import json
from pathlib import Path

p = Path(r".fw-validation/artifacts/V2/F3/F3.result.json")
p.parent.mkdir(parents=True, exist_ok=True)
res = {
    "case_id": "F3_freertos_qemu_demo", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "F3 (optional) official FreeRTOS Cortex-M QEMU demo with linker/map/--gc-sections truth",
    "status": "NOT_RUN",
    "reason": "optional scenario; coverage equivalence: kernel semantics covered by F1/F2 "
              "(real kernel callgraph + callback registration on the actual kernel sources) and "
              "Cortex-M vector/startup/linker truth covered by C1 (CMSIS template) and Z4 "
              "(qemu_cortex_m3 build + QEMU run + vector queries). The official demo's legacy "
              "Makefile (qt addon, symlink-based source layout) carries high fixture-prep risk "
              "with no unique validation signal beyond what F1/F2/C1/Z4 already provide.",
    "notes": ["provisioning completed during the phase (qemu-system-arm + gcc-arm-none-eabi); "
              "the skip is a coverage-equivalence decision, not an environment block"]
}
p.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", p)
