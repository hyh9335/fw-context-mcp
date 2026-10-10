# BENCHMARK METHODOLOGY FREEZE (§10-13, §18)

Frozen BEFORE benchmark measurements. Not movable after results.

## Corpus

- FreeRTOS M7.0B canary (pinned commit `8be86d4`)
- 2 translation units: `list.c`, `queue.c`
- Compile commands: `.dsh-control/corpora/builds/freertos_BASE/compile_commands.json`
- Clang: `C:\Program Files\LLVM\bin\clang.exe` v22.1.8
- Target: `--target=x86_64-pc-windows-msvc`
- Resource: `C:\Program Files\LLVM\lib\clang\22\include`

## Build matrix (§11)

Highly overlapping builds. Each build indexes the SAME 2 TUs but with
slightly different `-D` flags to create distinct build configurations:

| Build | Defines | Notes |
|---|---|---|
| BASE | (none) | Baseline FreeRTOS config |
| B2 | `-DconfigUSE_PREEMPTION=1` | Slightly different config |
| B3 | `-DconfigUSE_PORT_OPTIMISED_TASK_SELECTION=1` | Another config variant |
| B4 | `-DconfigUSE_TICKLESS_IDLE=1` | Another config variant |
| B5 | `-DconfigUSE_MUTEXES=1` | Another config variant |
| B6-B10 | Additional `-D` variants | Scale test |
| FULL | All 10 builds | Full matrix |

Each build sees the same source files — the semantic overlap is near 100%
(different `-D` flags don't change the function definitions in list.c/queue.c,
only their `#ifdef` branches). This is the worst case for dedup (highly
overlapping) and the best case for measuring whether M6 reuse works.

## Two benchmark modes (§10)

### REFERENCE MODE

Each build is indexed INDEPENDENTLY using a FRESH M6 store per build.
No cross-build sharing. This simulates the old independent-indexing model.

Implementation: for each build, create a new temp directory, copy the
source, create a new compile_commands.json with the build's flags, create
a new `.fw-context/` config, run `runner.run()`, measure time + facts.

### PRODUCTION M6 MODE

All builds are indexed into the SAME M6 store. Later builds benefit from
canonical fact reuse (shared facts are already present). This is the
actual shipped production path.

Implementation: one M6 store, index builds sequentially (B1, B2, ..., BN).
Measure cumulative time and per-build incremental time.

## Build scales (§11)

- 1 Build (BASE only)
- 2 Builds (BASE, B2)
- 5 Builds (BASE..B5)
- 10 Builds (BASE..B10)
- FULL (all 10)

## Repeats (§12)

- 3 repeats per scale run
- Report median + raw samples
- No discarding without documented environmental reason

## Cache policy (§13)

- REFERENCE mode: each build gets a COLD, fresh M6 store (recreated)
- PRODUCTION M6 mode: first build is COLD, subsequent builds are WARM
  (M6 store already has shared facts from prior builds)
- OS/compiler caches: unavoidable, same machine, same conditions
- REFERENCE and M6 modes compared under equivalent conditions (cold start
  for the first build in each mode)

## Cost breakdown (§14)

Per-build recorded:
- frontend_preprocess_parse (extract_all time)
- semantic_extraction (inside extract_all)
- semantic_materialization (materialize_unit_into_m6 time)
- total_wall_time
- facts_observed (total facts in ExtractionResult)
- canonical_facts_unique (unique canons in M6 store after this build)
- facts_reused (facts already in M6 store, reused)
- facts_new (new facts added to M6 store)
- semantic_storage_bytes (size of index-m6.db)

## Reuse reality (§15)

For each later build:
- reference_full_build_cost (cold independent)
- m6_incremental_cost (warm M6)
- absolute_saved_time = reference_full_build_cost - m6_incremental_cost
- relative_incremental_cost = m6_incremental_cost / reference_full_build_cost

## Acceptance formula (§18) — FROZEN BEFORE MEASUREMENT

The economic Goal is CLOSED if ALL of:

1. **Semantic materialization improves materially**: 
   `m6_incremental_cost < 0.85 * reference_full_build_cost` for later builds
   (at least 15% reduction on incremental builds)

2. **Semantic storage improves materially**:
   `m6_total_storage < 0.90 * reference_total_storage` at 10 builds
   (at least 10% storage reduction via dedup)

3. **Later overlapping Builds cost materially less than independent full indexing
   OR cumulative indexing shows meaningful end-to-end gain**:
   `m6_cumulative_10 < 0.90 * reference_cumulative_10`
   (at least 10% cumulative reduction at 10 builds)

4. **Query performance remains within previously accepted production bounds**:
   Query latency on M6 store < 100ms for lookup_symbol (already verified
   by tool_parity_green_test)

5. **No correctness regression**:
   - FreeRTOS GT parity (same symbols found)
   - Multi-build query isolation (no cross-build leakage)
   - False-shared-fact count = 0
   - All M6.5/M7 regression tests PASS

## Frontend interpretation (§19)

If frontend (extract_all) remains 70%+ of M6 time, that is NOT automatically
failure. The real question is whether M6 removed the REDUNDANT semantic
work. If M6 incremental = 3s where reference = 10s, the 70% frontend share
is irrelevant — the 70% reduction in total time is what matters.

## True frontend-bottleneck failure (§20)

If M6 later Build ≈ reference later Build (e.g., 9.5s vs 10s) despite high
semantic overlap, that IS failure: production authority is complete but
economic Goal is not. Do NOT start frontend incremental-parser work.
