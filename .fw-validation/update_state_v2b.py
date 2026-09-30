import json
import io

sp = r".fw-validation/state.json"
d = json.load(io.open(sp, encoding="utf-8"))
d["controller_progress"]["V2_PUBLIC_REAL"]["cases_done"] = {
    "F1": "PASS (freertos callgraph: 6280 syms/533 files, real hotspots, cross-file paths, macro-miss correct)",
    "F2": "PASS (callbacks: pxCallbackFunction registration->invocation exact linkage timers.c:436->711; no invented edges)",
    "C1": "FAIL_PRODUCT F-V2-001: standard CMSIS 6 C vector table unrecognized (false 'No vector table'); "
          "__VECTOR_TABLE indexed as varglobal w/ collapsed element type int[240]; SP symbol unresolved",
    "R1": "PASS (riot native: 98 TUs, 115729 syms, makefile detected, real hotspots cib_avail=14906)",
    "R2": "PASS (holdout variant reindex converges: saul in, removed core defs out, no stale carry-over; "
          "OBSERVATION: config identity keyed by CC path not content — same hash across changed CCs, F-V1-003 family)",
    "N1": "PASS-at-build-level (1194 TUs; index running; kconfiglib+genromfs env fixes documented)",
}
d["controller_progress"]["V2_PUBLIC_REAL"]["pending"] = ["Z1", "Z2", "Z3", "Z4", "N2", "N3", "F3",
                                                          "N1-queries", "V2-verifier", "V2-summary"]
d["controller_progress"]["V2_PUBLIC_REAL"]["build_status"] = {
    "Z1": "BUILD OK 75 TUs (native_sim host toolchain; py3.12 ssl rebuild; multilib)",
    "Z2": "BUILD OK 76 TUs (CONFIG_ASSERT=y variant)",
    "Z3": "BUILD OK 89 TUs (tests/kernel/workq/work_queue)",
    "N2": "BUILD OK sim:smp (variant of N1 sim:nsh; .configs snapshotted)",
    "R2": "CC regenerated 88 TUs; reindexed into R1 index dir; verified",
    "indexing": "Z1+N1 (pwsh-175), Z2+Z3 (pwsh-185) running; zephyr per-TU header closure makes 75-TU index ~2GB/slow",
    "Z4_F3": "qemu-system-arm + gcc-arm-none-eabi downloading (pwsh-181)"
}
json.dump(d, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state updated: 6 cases decided, builds all OK except Z4/F3 provisioning")
