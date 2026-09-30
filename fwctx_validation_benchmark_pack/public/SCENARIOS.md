# Public-real scenarios

## PUB-ZEPHYR

Z1 — compile database truth
- build `samples/hello_world` native_sim
- record compile_commands TU count and representative flags
- fw-context must index the same TU set modulo generated/tooling exclusions that it explicitly reports

Z2 — variant/config truth
- build the same small sample twice with a controlled CONFIG option difference
- use build-generated `.config` / compile commands as truth
- compare variants via fw-context
- only the expected conditional/call/content deltas may appear

Z3 — workqueue async
- use `tests/kernel/workq/work_queue`
- source establishes initialization handler and submission sites
- independent truth: source + Clang AST for registration, official test/runtime for actual work execution
- fw-context should classify async/register semantics without inventing direct CALL edges

Z4 — QEMU Cortex-M
- qemu_cortex_m3 sample
- validate generated compile database + startup/vector facts where visible
- runtime success is an integration oracle, not semantic GT

## PUB-NUTTX

N1 — sim:nsh clean index
- use CMake build because it exports compile_commands directly
- index cleanly and record scale

N2 — defconfig variant pair
- choose two configs for the same or closely related board with clear CONFIG differences
- compare build/context provenance and symbol/config deltas
- source/build system is truth; do not handwave expected differences

N3 — incremental mutation in a copied checkout
- modify one shared header in a disposable clone
- compare incremental result to clean full result

## PUB-FREERTOS

F1 — kernel callgraph
- tasks.c / queue.c / timers.c / event_groups.c

F2 — callbacks
- timer callback registration / task creation / queue operations
- independent AST/source-site oracle

F3 — optional official Cortex-M QEMU demo
- use official demo linker script + map + --gc-sections where buildable
- linker truth is authoritative for retention/GC

## PUB-CMSIS

C1 — Cortex-M vector mapping
- use CMSIS 6 startup template / example
- vector slot names and reserved entries are exact standard-backed truth
- compare fw-context vector result slot-by-slot

## PUB-RIOT

R1 — native hello-world
- use native board
- generate compilation DB with official `make compile-commands`
- clean index + query smoke

R2 — holdout variant
- switch BOARD or module set
- regenerate compilation DB
- verify context/variant separation and no stale carry-over

Purpose: test whether fw-context assumptions accidentally overfit Zephyr/FreeRTOS.
