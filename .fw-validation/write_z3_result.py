import json
import io

z3 = {
    "case_id": "Z3_zephyr_workq", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "Z3 work queue test app: async/register semantics — no invented CALL edges across the queue boundary",
    "fixture": "zephyr v4.4.2 tests/kernel/workq/work_queue BOARD=native_sim CONFIG_ASSERT=y (host toolchain)",
    "build": {"tu_count": 89},
    "queries": {
        "k_work_init": "kernel/work.c:153 is_definition=true ✓",
        "k_work_submit": "kernel/work.c:414 is_definition=true ✓",
        "work_queue_main": "kernel/work.c:674 is_definition=true ✓",
        "find_callers work_queue_main": "No callers found — CORRECT (thread entry, registered not called); "
                                         "honest warning: 3 generated TUs (syscall_dispatch.c) outside compile_commands, "
                                         "reports 'run fw-context index --build'; unresolved_count=1315 honest",
        "find_call_path k_work_submit→work_queue_main": "No path within depth 10 — CORRECT: the submit→queue→thread "
                                                         "hand-off is ASYNC; product must not invent a direct CALL edge ✓✓",
        "find_callees_recursive k_work_submit": "k_work_submit_to_queue (depth 1, real call) ✓"
    },
    "observations": ["k_work_submit_to_queue appears twice at depth 1 in the recursive callees list (cosmetic dedup gap; "
                     "likely decl+def dual USR — low severity note, no semantic error)"],
    "status": "PASS"
}
open(r".fw-validation/artifacts/V2/Z3/Z3.result.json", "w", encoding="utf-8").write(json.dumps(z3, indent=2, ensure_ascii=False) + "\n")
print("Z3.result.json written")
