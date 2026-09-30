#!/bin/bash
# F1/F2 work setup: disposable freertos-kernel copy + minimal FreeRTOSConfig.h + compile DB
set -x
FK=~/fwctx-bench/public/freertos-kernel
WORK=~/fwctx-v2-work/F1
mkdir -p "$WORK/kernel" "$WORK/port" "$WORK/cfg"
cp "$FK"/*.c "$WORK/kernel/"
cp -r "$FK/include" "$WORK/include"
cp "$FK/portable/ThirdParty/GCC/Posix/portmacro.h" "$WORK/port/"
cp "$FK/portable/ThirdParty/GCC/Posix/port.c" "$WORK/port/" 2>/dev/null || true
cp -r "$FK/portable/ThirdParty/GCC/Posix/utils" "$WORK/port/utils" 2>/dev/null || true

cat > "$WORK/cfg/FreeRTOSConfig.h" <<'EOF'
/* Minimal indexing config for the FreeRTOS kernel (validation fixture prep). */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#define configUSE_PREEMPTION                    1
#define configCPU_CLOCK_HZ                      1000000UL
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    5
#define configMINIMAL_STACK_SIZE                128
#define configTOTAL_HEAP_SIZE                   10240
#define configMAX_TASK_NAME_LEN                 16
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configQUEUE_REGISTRY_SIZE               8
#define configUSE_QUEUE_SETS                    0
#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               2
#define configTIMER_QUEUE_LENGTH                10
#define configTIMER_TASK_STACK_DEPTH            256
#define configUSE_TASK_NOTIFICATIONS            1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES   1
#define configSUPPORT_STATIC_ALLOCATION         0
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configUSE_TRACE_FACILITY                0
#define configUSE_MALLOC_FAILED_HOOK            0
#define configCHECK_FOR_STACK_OVERFLOW          0
#define configSTACK_DEPTH_TYPE                  uint32_t
#define configMESSAGE_BUFFER_LENGTH_TYPE        size_t
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configAPPLICATION_ALLOCATED_HEAP        0
#endif
EOF

python3 - "$WORK" <<'EOF'
import json, sys, os
work = sys.argv[1]
srcs = ["kernel/tasks.c", "kernel/queue.c", "kernel/timers.c", "kernel/event_groups.c",
        "kernel/list.c", "kernel/croutine.c", "kernel/stream_buffer.c", "port/port.c"]
cc = []
for s in srcs:
    p = os.path.join(work, s)
    if not os.path.exists(p):
        continue
    cc.append({"directory": work, "file": p,
               "arguments": ["clang", f"-I{work}/include", f"-I{work}/port",
                             f"-I{work}/cfg", "-c", p, "-o", s.replace("/", "_") + ".o"]})
json.dump(cc, open(os.path.join(work, "compile_commands.json"), "w"), indent=1)
print("CC entries:", len(cc))
EOF

PY=~/fwctx-venv311/bin/python
IDX=~/fwctx-v2-work/F1/index
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli init --project "$WORK" --quick
$PY -m fw_context_mcp.cli index "$WORK/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings
echo "F1/F2-INDEXED"
