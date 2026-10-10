# M7.0A — Corpus Provisioning & Baseline Freeze

**Verdict: `M7_0A_CORPUS_BASELINE_READY`**
**Hard Gate: 11/11 PASS, exit 0**
**Timestamp: 2026-10-09T06:23:21Z**

---

## 1. Purpose

This stage establishes a durable, auditable, reproducible local corpus
environment for FreeRTOS-Kernel and Mbed TLS before any larger multi-Build
production validation begins.

**This is NOT M7 production migration.** No 16 new configs were created,
no production authority was switched, no v2 retirement occurred, and no
reuse percentages were tuned. M7.0A is corpus provisioning ONLY.

---

## 2. Pinned Corpora

| Corpus | Upstream | Pinned Commit | Local Path | Clean |
|--------|----------|---------------|------------|-------|
| FreeRTOS-Kernel | https://github.com/FreeRTOS/FreeRTOS-Kernel.git | `8be86d4a24fd4091f8f4192018423ab590f408db` | `.dsh-control/m6_research/corpus/FreeRTOS-Kernel` | ✓ |
| mbedtls | https://github.com/Mbed-TLS/mbedtls.git | `de23569aa95ac518e1c794236219dcb04fca4ae5` | `.dsh-control/m6_research/corpus/mbedtls` | ✓ |

Both corpora verified CLEAN at pinned commits via direct `git rev-parse HEAD`
inspection. File counts: FreeRTOS 284 .c / 371 .h; Mbed TLS 69 .c / 62 .h.

---

## 3. Toolchain

- **clang**: 22.1.8 at `C:\Program Files\LLVM\bin\clang.exe` (target `x86_64-pc-windows-msvc`)
- **Python**: 3.11.9
- **git**: 2.50.1.windows.1
- **fw-context CLI**: `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\Scripts\fw-context.exe` (v0.31.0)
- **cmake/ninja**: NOT installed — manual `compile_commands.json` generation used instead
- **libclang resource dir**: `C:\Program Files\LLVM\lib\clang\22\include` (provides `stddef.h`, `stdint.h`)
- **Stubs**: `.dsh-control/corpora/harness/stubs/stdlib.h`, `string.h` (minimal, for missing MSVC headers)

---

## 4. Baseline Results

### FreeRTOS-Kernel

| Build | Symbols | Functions | Canon Facts | Parity | GC |
|-------|---------|-----------|-------------|--------|-----|
| BASELINE | 424 | 125 | 1315 | 0 failures | ✓ |
| BASE | 424 | 125 | 1315 | PASS | ✓ |
| NO_PREEMPT | 424 | 125 | 1303 | PASS | ✓ |
| NO_MUTEXES | 413 | 122 | 1233 | PASS | ✓ |
| STATIC_ONLY | 411 | 123 | 1278 | PASS | ✓ |
| FULL_FEATURE | 424 | 125 | 1315 | PASS | ✓ |

- **5/5 builds parity PASS**, 0 failures
- Sharing ratio: 1187 shared / 1322 total = 0.898
- GC removes all facts: ✓
- Sources: `list.c`, `queue.c`
- Includes: `FreeRTOS-Kernel/include`, `portable/MSVC-MingW`, temp `FreeRTOSConfig.h`, stubs, clang resource
- Compilation: clang `-fsyntax-only` PASSES 2/2

### Mbed TLS

| Build | Symbols | Functions | Canon Facts | Parity | GC |
|-------|---------|-----------|-------------|--------|-----|
| BASE | 1602 | 754 | 2076 | PASS | ✓ |
| MBEDTLS_ENTROPY_C | 1602 | 754 | 2076 | PASS | ✓ |
| MBEDTLS_PLATFORM_MEMORY_C | 1602 | 754 | 2076 | PASS | ✓ |
| FULL_FEATURE | 1602 | 754 | 2076 | PASS | ✓ |

- **4/4 builds parity PASS**, 0 failures
- Sharing ratio: 2076 shared / 2076 total = 1.000 (all builds identical — defines don't affect version.c/timing.c)
- GC removes all facts: ✓
- Sources: `version.c`, `timing.c` (from `library/` subdir)
- Includes: `mbedtls/include`, `mbedtls/library`, clang resource, stubs

---

## 5. Reproducibility Tests

### Rebuild-from-Clean (§17)

- Build directories deleted (GT files + `builds/freertos_BASE/`)
- Source repos preserved (NOT deleted)
- All GT files regenerated from scratch
- **Semantic reproducibility: PASS** (same symbol counts, same build counts)
- Bit-exact differs only due to timestamps (expected)

### Source-Absence Recovery (§18)

- FreeRTOS source absence: **detected** (harness raises clear error)
- Mbed TLS source absence: **detected** (harness raises clear error)
- Manifest correctly reports presence/absence: ✓

### Corpus Bootstrap (§19)

- `bootstrap_real_corpora.py` — idempotent entrypoint
- Both corpora at pinned commits: ✓
- Stubs present: ✓
- Verdict: `M7_0A_CORPUS_BOOTSTRAP_READY`

---

## 6. Hard Gate (§21)

**11/11 PASS, exit 0**

| # | Check | Status | Detail |
|---|-------|--------|--------|
| 1 | corpus_bootstrap_ready | PASS | READY |
| 2 | freertos_baseline_smoke | PASS | 424 symbols, 0 parity failures |
| 3 | freertos_5build_compat | PASS | 5/5 parity, GC OK |
| 4 | mbedtls_4build_compat | PASS | 4/4 parity, GC OK |
| 5 | rebuild_from_clean | PASS | semantic=True, source_preserved=True |
| 6 | source_absence_recovery | PASS | all_detected=True |
| 7 | corpus_status_all_ready | PASS | all_ready=True |
| 8 | toolchain_audit | PASS | evidence=present |
| 9 | corpus_manifest_valid | PASS | manifest=valid |
| 10 | production_authority_unchanged | PASS | M6 shadow-only, no v2 retirement |
| 11 | stop_boundary_respected | PASS | M7.0A scope respected |

Gate report: `.dsh-control/corpora/reports/m7_0a_corpus_gate.json`

---

## 7. STOP BOUNDARY (§22)

M7.0A is **corpus provisioning ONLY**. The following were NOT done:

- ❌ 16 new Build configurations (M7 production scope)
- ❌ Production authority switch (M6 remains shadow-only)
- ❌ v2 retirement
- ❌ Reuse percentage tuning
- ❌ MCP tool switching

Production authority MUST remain unchanged. M6 is shadow-only. The v2 store
remains the production write/read engine. No corpus artifacts were committed
to the source tree — all corpus state lives under `.dsh-control/corpora/`.

---

## 8. Deliverables

| Artifact | Path |
|----------|------|
| Corpus manifest | `.dsh-control/corpora/manifests/corpus_manifest.json` |
| Corpus inventory | `.dsh-control/evidence/M7_0A_CORPUS_INVENTORY.md` |
| M6.5 provenance | `.dsh-control/evidence/M7_0A_M6_5_CORPUS_PROVENANCE.md` |
| Toolchain audit | `.dsh-control/evidence/M7_0A_TOOLCHAIN.md` |
| Bootstrap entrypoint | `.dsh-control/corpora/bootstrap_real_corpora.py` |
| Status command | `.dsh-control/corpora/corpus_status.py` |
| Hard gate | `.dsh-control/corpora/run_m7_0a_corpus_gate.py` |
| FreeRTOS baseline harness | `.dsh-control/corpora/harness/freertos_baseline.py` |
| FreeRTOS smoke GT | `.dsh-control/corpora/harness/freertos_baseline_smoke.py` |
| FreeRTOS 5-build harness | `.dsh-control/corpora/harness/freertos_5build_compat.py` |
| Mbed TLS 4-build harness | `.dsh-control/corpora/harness/mbedtls_4build_compat.py` |
| Rebuild-from-clean test | `.dsh-control/corpora/harness/rebuild_from_clean.py` |
| Source-absence test | `.dsh-control/corpora/harness/source_absence_recovery.py` |
| GT files | `.dsh-control/corpora/gt/freertos_BASELINE_gt.json` |
| | `.dsh-control/corpora/gt/freertos_5build_gt.json` |
| | `.dsh-control/corpora/gt/mbedtls_4build_gt.json` |
| Reports | `.dsh-control/corpora/reports/*.json` |

---

## 9. Conclusion

**M7.0A is COMPLETE.** The corpus baseline is frozen and ready for M7
production validation. The environment is:

- **Durable**: Corpora are pinned to exact commits, verified clean.
- **Auditable**: Every artifact has a manifest entry and a report.
- **Reproducible**: Rebuild-from-clean test proves GT regeneration works.
- **Safe**: Production authority unchanged, M6 remains shadow-only.

The next stage (M7.0B or M7.1) may begin multi-Build production validation
using these frozen corpora. M7.0A's stop boundary is absolute — no production
migration occurred.
