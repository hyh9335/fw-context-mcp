# V2_PUBLIC_REAL — Phase Summary

Status: CONTROLLER_COMPLETE_PENDING_VERIFIER
Product: fw-context-mcp 0.31.0 @ HEAD d8f08a41d3fb9df6de255a187720f6332ed0f1c9 (WSL Ubuntu-22.04; real stdio MCP throughout)

## Final tally — 13 scenarios

| Case | Fixture | Status | Key facts |
|------|---------|--------|-----------|
| F1_freertos_callgraph | FreeRTOS v11 kernel | **PASS** | 6280 syms/533 files; 53 callers on vPortExitCritical; proven cross-file path xQueueGenericSend→vTaskPlaceOnEventList→vListInsert; honest unresolved_count=825 |
| F2_freertos_callbacks | FreeRTOS timers/tasks | **PASS** | pxCallbackFunction registration(timers.c:436)→invocation(:711) exact; task-code call site honestly UNRESOLVED |
| F3_freertos_qemu_demo | (optional) | NOT_RUN | coverage-equivalent via F1/F2+C1+Z4; provisioning had completed (qemu+arm-gcc) |
| C1_cmsis_vector | CMSIS_6@45dab712 CM3 startup | **FAIL_PRODUCT (WRONG_ANSWER, F-V2-001)** | get_vector_table returns false "No vector table" for the standard CMSIS 6 C-template; table indexed as varglobal with collapsed type `const int[240]`; SP slot symbol unresolved |
| R1_riot_native_hello | RIOT hello-world native | **PASS** | 98 TUs → 10721 files, 115729 syms; real hotspots; main@main.c:33; cib_avail 14906 callers |
| R2_riot_holdout_variant | RIOT +saul +uart | **PASS** | reindex converges: saul in, removed core defs out, no stale carry-over; config-hash-by-path observation recorded |
| N1_nuttx_sim_nsh | nuttx sim:nsh (cmake flow) | **PASS** | 1194 TUs → **1.49M symbols / 58254 files / 437k references**; nx_start defined; index survives a 937k-caller hotspot; DB 4.6GB, ~5h wall |
| N2_nuttx_variant_pair | nuttx sim:smp | **PASS** | distinct config_hash; Δ63k symbols; distinct hotspot profiles (nxmutex_lock out, invdiv_umulh64 in for smp); 125-key config diff |
| N3_nuttx_incremental | N1 copy + probe fn | **PASS** | mutation picked up: delta exactly +1, probe fn + body stored, ~30s pickup |
| Z1_zephyr_hello | zephyr v4.4.2 native_sim | **PASS** | 75 TUs → 85975 syms/8730 files; main weak-override chain (main_weak.c) correct; DB 1.7GB |
| Z2_zephyr_variant_pair | zephyr +CONFIG_ASSERT | **PASS** | variant separation confirmed (distinct config_hash across index dirs); **F-V2-002**: exact=True silent fallback |
| Z3_zephyr_workq | zephyr work_queue test | **PASS** | no invented CALL edges across the async queue boundary (submit→queue→thread hand-off correctly yields NO path); thread entry callerless; generated-TU gap honestly warned |
| Z4_qemu_cortex_m3 | zephyr real Cortex-M + QEMU | **PASS** | build + QEMU run "Hello World! qemu_cortex_m3/ti_lm3s6965"; **97-slot vector table fully recognized** (assembly form: SP data slot, reset, faults, _isr_wrapper dispatchers) |

**Score: 11 PASS, 1 FAIL_PRODUCT, 1 NOT_RUN (optional).**

## Findings opened in V2

- **F-V2-001 (WRONG_ANSWER / FAIL_PRODUCT)** — C1: standard CMSIS 6 startup-template C vector table unrecognized
  (`get_vector_table` → false "No vector table"). Z4 contrast: the ASSEMBLY table form on the same product is fully
  recognized slot-by-slot — the gap is specific to the C array-of-function-pointers template
  (typedef'd fn-ptr element + cast initial element + int-0 reserved slots). Links F-V1-006/F-V1-007.
- **F-V2-002 (API_CONTRACT / FAIL_PRODUCT)** — Z2: `lookup_symbol {exact:true}` on an absent name silently relaxes
  to unrelated-symbol rows marked `_fallback:true` (z_impl_printk → z_impl_k_sleep). DB truth: name absent.
  Violates the documented exact-match contract; automated existence checks get false positives.

## Scale facts

- zephyr native_sim: 75 TUs → 1.7GB DB (per-TU generated-header closure) — ~17 min
- RIOT native: 98 TUs → 0.4GB — ~4 min
- nuttx sim:nsh: 1194 TUs → 4.6GB, 1.49M symbols — **~5h wall** (~10× RIOT per-TU cost; header closure + FTS5)
- nuttx sim:smp: 1431k symbols, ~5h wall
- product stayed responsive at 1.5M symbols; 3 concurrent indexers ran stably

## Harness notes (product-neutral)

- two venvs: venv311 runs the product; venv312 only build deps (west/kconfiglib/jsonschema) — running product
  commands from venv312 silently no-ops (caught via not_initialized in V2)
- `--project` must match the REGISTERED root (work root vs source dir mismatch caused a silent 5s error no-op in N3 attempt 1)
- get_active_build commits its build_configs row at index END — mid-run queries read "No build config indexed" (N1)
- N2 build tree was externally reconfigured once (phantom cmake, root cause unidentified); artifact CC copy preserved, rebuilt cleanly
- Windows side imports the same source HEAD (V3-ready); pip metadata stale (0.24.0 vs source 0.31.0)

## Environment fixes (all reproducible, none touch production code)

python 3.12.10 source-built w/ libssl (zephyr ≥3.12) · gcc-multilib (native_sim 32-bit) · kconfiglib olddefconfig +
genromfs (nuttx cmake flow; NO configure.sh) · BOARD_CONFIG takes a config DIRECTORY path · RIOT `make
compile-commands` from app dir writes CC at repo root · west cmsis_6+cmsis with GIT_SSL_NO_VERIFY=1 in a work-copy
zephyr (public clone untouched)
