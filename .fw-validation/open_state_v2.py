import json
import io

sp = r".fw-validation/state.json"
d = json.load(io.open(sp, encoding="utf-8"))
d["phases"]["V2_PUBLIC_REAL"] = "CONTROLLER_RUNNING"
d.setdefault("controller_progress", {})["V2_PUBLIC_REAL"] = {
    "scenarios": ["Z1", "Z2", "Z3", "Z4", "N1", "N2", "N3", "F1", "F2", "F3", "C1", "R1", "R2"],
    "cases_done": {"F1": "PASS", "F2": "PASS",
                   "C1": "FAIL_PRODUCT (F-V2-001 pending verification: standard CMSIS 6 C vector table unrecognized — false 'No vector table' answer; symbol indexed but element type collapsed)"},
    "pending": ["Z1", "Z2", "Z3", "Z4", "N1", "N2", "N3", "F3", "R1", "R2"],
    "env_notes": ["WSL: gcc/g++/make/cmake/ninja/clang OK; zephyr needs python>=3.12 (uv setup in progress); "
                  "nuttx needs kconfig-frontends (apt in progress); Z4 needs qemu-system-arm + arm-none-eabi-gcc (apt in progress)",
                  "R1 build OK (compile-commands at riot root, ~900 TUs); index running",
                  "public clones verified at locked commits: zephyr dccb0959, nuttx 273c7712, apps 20ffb1a3, "
                  "freertos 3a22924e, cmsis 45dab712, riot 50ae2eba"]
}
json.dump(d, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state: V2_PUBLIC_REAL = CONTROLLER_RUNNING")
