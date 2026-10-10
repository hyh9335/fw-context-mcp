# M7.0A Corpus Inventory

**Generated:** 2026-01-20 (filesystem inspection, not status reports)
**Inspector:** M7.0A autonomous provisioning agent
**Method:** Direct filesystem inspection via PowerShell + git

---

## Summary

Two corpus copies were discovered on the local filesystem, both at the exact pinned commits, both clean. The M6.5 test scripts and GT artifacts are present and recoverable. No download was needed.

| Corpus | Path | HEAD | Pinned Match | Clean | Used by M6.5 |
|--------|------|------|-------------|-------|--------------|
| FreeRTOS-Kernel | `.dsh-control/m6_research/corpus/FreeRTOS-Kernel/` | `8be86d4a24fd4091f8f4192018423ab590f408db` | YES | YES | YES |
| Mbed TLS | `.dsh-control/m6_research/corpus/mbedtls/` | `de23569aa95ac518e1c794236219dcb04fca4ae5` | YES | YES | YES |

---

## FreeRTOS-Kernel

### Local path
`D:\github\fw-context-mcp\.dsh-control\m6_research\corpus\FreeRTOS-Kernel\`

### Repository type
Git clone (full history)

### Origin remote
`https://github.com/FreeRTOS/FreeRTOS-Kernel.git` (fetch/push)

### Current HEAD
`8be86d4a24fd4091f8f4192018423ab590f408db`

### Pinned commit exists
YES — `git cat-file -t 8be86d4a24fd4091f8f4192018423ab590f408db` returns `commit`

### Dirty/clean state
CLEAN — `git status --porcelain` returns empty

### Source file counts
- `.c` files: 284
- `.h` files: 371

### FreeRTOSConfig.h variants
Two FreeRTOSConfig.h files exist in the upstream source:
- `examples/coverity/FreeRTOSConfig.h`
- `examples/template_configuration/FreeRTOSConfig.h`

The M6.5 test harness created its OWN 5 config variants under `.dsh-control/m6_research/corpus/FREERTOS/`:
- `FreeRTOSConfig_BASE.h`
- `FreeRTOSConfig_NO_PREEMPT.h`
- `FreeRTOSConfig_NO_MUTEXES.h`
- `FreeRTOSConfig_STATIC_ONLY.h`
- `FreeRTOSConfig_FULL_FEATURE.h`

These are project-local harness files, NOT upstream source modifications.

### Build artifacts
- No `compile_commands.json` exists for FreeRTOS-Kernel. M6.5 used direct libclang parsing via the Python `clang.cindex` binding (`extract_build_facts()`), NOT CMake/compile_commands.
- No CMake build directory exists.

### GT artifacts
- `.dsh-control/m6_research/ground_truth/FREERTOS_gt.json` — EXISTS

### M6.5 usage
YES — `test_freertos.py` references this exact path and commit. The `FREERTOS_gt.json` was generated from `list.c` + `queue.c` with 5 build configurations.

---

## Mbed TLS

### Local path
`D:\github\fw-context-mcp\.dsh-control\m6_research\corpus\mbedtls\`

### Repository type
Git clone (full history)

### Origin remote
`https://github.com/Mbed-TLS/mbedtls.git` (fetch/push)

### Current HEAD
`de23569aa95ac518e1c794236219dcb04fca4ae5`

### Pinned commit exists
YES — `git cat-file -t de23569aa95ac518e1c794236219dcb04fca4ae5` returns `commit`

### Dirty/clean state
CLEAN — `git status --porcelain` returns empty

### Source file counts
- `.c` files: 69
- `.h` files: 62

### Build artifacts
- No `compile_commands.json` exists for Mbed TLS. M6.5 used direct libclang parsing.
- No CMake build directory exists.

### GT artifacts
- `.dsh-control/m6_research/ground_truth/MBEDTLS_gt.json` — EXISTS

### M6.5 usage
YES — `test_mbedtls.py` references this exact path and commit. The `MBEDTLS_gt.json` was generated from `version.c` + `timing.c` with 4 build configurations.

---

## Other discovered corpus copies

### FREERTOS (non-git config directory)
`D:\github\fw-context-mcp\.dsh-control\m6_research\corpus\FREERTOS\`
- NOT a git repository — a project-local directory containing 5 FreeRTOSConfig_*.h variant files
- Created by `test_freertos.py` as the harness config directory
- 0 .c files, 5 .h files

### M6.5 synthetic torture corpus
`.dsh-control/m6_research/corpus/` contains 48 synthetic cases (T01-T48, C01-C18, etc.) each with their own compile_commands.json. These are NOT real-project corpora — they are hand-crafted C/C++ torture cases for structural-reuse mutation testing.

### Other benchmark directories
Various other compile_commands.json files exist under `.fw-closeout/`, `.fw-final/`, `.fw-orchestrator/`, `implementation/`, and `multi_version_index_review/` — these are from earlier M3/M4/M5 milestone work and are NOT relevant to M7.0A corpus provisioning.

---

## Conclusion

Both pinned corpora are present, clean, and at the exact pinned commits. No re-download is needed. The M6.5 build definitions, GT artifacts, and test scripts are fully recoverable from the existing filesystem state.
