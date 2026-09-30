import json
import io
from pathlib import Path

ART = Path(r".fw-validation/artifacts/V2")

results = {
    "C1": {
        "case_id": "C1_cmsis_vector", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
        "scenario": "C1 Cortex-M vector mapping (CMSIS 6 startup template, ARMCM3 RTE target)",
        "fixture": "cmsis_6@45dab712 CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3/startup_ARMCM3.c",
        "command": {"cc": ["clang", "-ICMSIS/Core/Include", "-IRTE", "-DARMCM3", "-DCMSIS_DEV", "-c", "startup_ARMCM3.c"],
                    "index": "fw-context index (no-analyze no-embeddings)"},
        "exit_code": 0,
        "expected": {"vector_slots": "standard CMSIS CM3 table: slot0 SP (__INITIAL_SP), 1 Reset, 2 NMI, "
                                     "3 HardFault, 4 MemManage, 5 BusFault, 6 UsageFault, 7-10 reserved(0), "
                                     "11 SVC, 12 DebugMon, 13 reserved(0), 14 PendSV, 15 SysTick, 16-25 Interrupt0-9, "
                                     "26-239 zero-filled", "source": "startup_vector_region.txt (oracle dump)"},
        "observed": {
            "get_vector_table": "No vector table in this build (array-of-function-addresses not recognised)",
            "__VECTOR_TABLE_symbol": {"indexed": True, "kind": "varglobal", "line": 80,
                                       "signature": "const int[240] __VECTOR_TABLE (element type collapsed from function-pointer typedef)"},
            "conditional_branches": "ARMCM3 if=1, __GNUC__ if=1 (table branch ACTIVE at index time)",
            "handlers": "NMI_Handler/HardFault_Handler etc. indexed (weak alias declarations + definitions)",
            "search_bodies_array": "empty for the table initializer (F-V1-007 class)",
            "find_references___INITIAL_SP": "Symbol not found (slot-0 data symbol unresolved, F-V1-006 class)"
        },
        "comparison": "slot-by-slot comparison impossible: product emits no table for the standard CMSIS 6 template form",
        "status": "FAIL_PRODUCT",
        "classification": "WRONG_ANSWER",
        "finding": "F-V2-001 (pending verification): get_vector_table returns a false 'No vector table' for the standard CMSIS 6 startup template C table (__VECTOR_TABLE[240], typedef'd function-pointer element type, cast initial-SP element, integer-0 reserved slots); the table IS indexed as a symbol, its element type collapses to int[240]; SP data symbol unresolved",
        "notes": ["first attempt used an incomplete compile command (missing -DARMCM3) — controller fixture "
                  "defect, corrected before verdict; the no-table answer reproduced with the corrected command",
                  "oracle duty: vector region dumped byte-level to startup_vector_region.txt for comparison"]
    },
    "F1": {
        "case_id": "F1_freertos_callgraph", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
        "scenario": "F1 kernel callgraph (tasks/queue/timers/event_groups + list/croutine/stream_buffer + Posix port)",
        "fixture": "freertos-kernel@3a22924e + minimal FreeRTOSConfig.h (fixture prep) + ThirdParty/GCC/Posix port",
        "command": {"cc": "8 TUs synthesized (clang -Iinclude -Iport -Icfg)", "index": "fw-context index"},
        "exit_code": 0,
        "expected": {"scale": "real kernel symbols", "hotspots": "plausible kernel hotspots",
                     "paths": "cross-file kernel call paths"},
        "observed": {
            "scale": {"symbol_count": 6280, "file_count": 533, "reference_count": 3182},
            "hotspots": "vPortExitCritical (53 callers), vPortEnterCritical (44), kernel-real",
            "cross_file_path": "xQueueGenericSend → vTaskPlaceOnEventList → vListInsert (queue.c→tasks.c→list.c, depth 2, proven)",
            "callees": "xQueueGenericSend → prvCopyDataToQueue, prvIsQueueFull (depth 1-2)",
            "xTimerStart_not_found": "CORRECT — xTimerStart is a macro (xTimerGenericCommand wrapper), not a function"
        },
        "status": "PASS",
        "notes": ["honest unresolved_count 825 recorded by the product (kernel macros/asm)"]
    },
    "F2": {
        "case_id": "F2_freertos_callbacks", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
        "scenario": "F2 callbacks: timer callback registration/invocation + task creation",
        "fixture": "same index as F1",
        "exit_code": 0,
        "expected": {"indirect": "callback invocation through pxCallbackFunction recognized as indirect site",
                     "no_invention": "no direct CALL edges invented from registration to app callback"},
        "observed": {
            "indirect_call_site_pxCallbackFunction": "timers.c:711 prvReloadTimer — EXACT, evidence=ast",
            "indirect_target_pxCallbackFunction": "assignment timers.c:436 prvInitialiseNewTimer (param→field) linked to call site",
            "task_code": "pxTaskCode assignment tasks.c:1741 xTaskCreate; call site honestly UNRESOLVED (no invented site)",
            "registration_path": "xTimerCreate → prvInitialiseNewTimer depth 1 proven",
            "references": "xTimerCreate: no in-kernel references (API surface) — correct"
        },
        "status": "PASS"
    }
}

for case, res in results.items():
    p = ART / case / f"{case}.result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", p)
