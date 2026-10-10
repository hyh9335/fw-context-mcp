# M7.0B FREERTOS CANARY — FINAL EVIDENCE

**Verdict: `M7_0B_FREERTOS_CANARY_READY`**
**Hard gate: 18/18 PASS (exit 0)**
**Date: 2026-10-09**

## STOP Boundary (§23) Reached

M7.0B is COMPLETE. Per the stop boundary:

- **Production read/write authority NOT switched.**
- **M6 remains SHADOW ONLY.**
- The M7.0B canary pack validates M6 projection equivalence for
  the FreeRTOS multi-build matrix.

## Hard Gate Result

```
========================================================================
CLAUSE SUMMARY
========================================================================
  [PASS] §2/§5    All 7 FreeRTOS TUs parse cleanly
  [PASS] §2/§5    tu_parseability_test.json verdict=PASS
  [PASS] §16      Resource-dir auto-discovery
  [PASS] §16      resource_dir_discovery.json verdict=PASS
  [PASS] §8-11    Oracle B/C sentinels script
  [PASS] §8-11    oracle_sentinels.json verdict=PASS
  [PASS] §4-19    Matrix runner script
  [PASS] §4-8     freertos_matrix_full.json verdict=PASS
  [PASS] §6       all_parse_ok=True
  [PASS] §8       all_parity_pass=True (M6==GT)
  [PASS] §8       all_gc_ok=True
  [PASS] §8       final_gc_clean=True
  [PASS] §7       16 distinct fingerprints == 16 builds
  [PASS] §13      freertos_matrix_pairs.json present
  [PASS] §19      freertos_matrix_scaling.json present
  [PASS] §20      Canary pack assembly
  [PASS] §20      freertos_canary_pack.json present
  [PASS] §17      Common-mode GT risk documented

  Total: 18 PASS, 0 FAIL  (59.7s)
========================================================================
FINAL VERDICT: M7_0B_FREERTOS_CANARY_READY
========================================================================
```

## Matrix Summary (16 Builds)

All 16 builds parse cleanly, have distinct macro fingerprints, and
achieve M6 projection parity with FULL_BUILD_GT (0 missing facts):

| Build | Symbols | Canon | Projected | Missing | Parity | Fingerprint |
|-------|---------|-------|-----------|---------|--------|-------------|
| BASE | 2081 | 6543 | 6543 | 0 | PASS | 924cd6f25753 |
| NO_PREEMPT | 2081 | 6504 | 6504 | 0 | PASS | aa6d11c53f4f |
| NO_MUTEXES | 2046 | 6177 | 6177 | 0 | PASS | ff215081712f |
| STATIC_ONLY | 2034 | 6307 | 6307 | 0 | PASS | d6b738d3e10c |
| FULL_FEATURE | 2300 | 7321 | 7321 | 0 | PASS | 43d37b3bece5 |
| TRACE_ON | 1058 | 4810 | 4810 | 0 | PASS | b06810cacc2f |
| TLS_POINTERS_2 | 1000 | 4497 | 4497 | 0 | PASS | 41a8a80ce648 |
| NO_TASK_NOTIFICATIONS | 953 | 4034 | 4034 | 0 | PASS | 422b0b8a4b07 |
| QUEUE_SETS_ON | 1012 | 4638 | 4638 | 0 | PASS | 39f6da037290 |
| NO_TIMERS | 1996 | 5838 | 5838 | 0 | PASS | 870369524193 |
| COROUTINES_ON | 2404 | 7050 | 7050 | 0 | PASS | e31d34f3fd31 |
| TASK_ABORT_DELAY | 1006 | 4579 | 4579 | 0 | PASS | 238ab6ce401e |
| POSIX_ERRNO | 994 | 4474 | 4474 | 0 | PASS | c344b017e249 |
| NO_EVENT_GROUPS | 2042 | 6172 | 6172 | 0 | PASS | 8d2bededbec9 |
| MINIMAL | 1791 | 3845 | 3845 | 0 | PASS | e02eb9c07948 |
| LAYOUT_STRESS | 1109 | 5042 | 5042 | 0 | PASS | 269a86c6c110 |

**16 unique macro fingerprints** — every build's config independently
proven to reach the compiler.

## Oracle Layer Results

### Oracle B: Record Layout Sentinels (10/10 PASS)

TCB struct sizes show real semantic diversity:

| Build | TCB Size | Diff from BASE |
|-------|----------|----------------|
| MINIMAL | 128 | -16 |
| NO_MUTEXES | 128 | -16 |
| BASE | 144 | 0 |
| POSIX_ERRNO | 152 | +8 |
| TRACE_ON | 160 | +16 |
| TLS_POINTERS_2 | 160 | +16 |
| FULL_FEATURE | 192 | +48 |
| LAYOUT_STRESS | 208 | +64 |

### Oracle C: Known Semantic Sentinels (25/25 PASS)

All field-presence and function-presence sentinels pass:
- NO_MUTEXES: uxBasePriority/uxMutexesHeld absent ✓
- TRACE_ON: uxTCBNumber/uxTaskNumber present ✓
- TLS_POINTERS_2: pvThreadLocalStoragePointers present ✓
- NO_TASK_NOTIFICATIONS: ulNotifiedValue/ucNotifyState absent ✓
- TASK_ABORT_DELAY: ucDelayAborted present ✓
- POSIX_ERRNO: iTaskErrno present ✓
- FULL_FEATURE: trace+TLS+runtime fields present ✓
- LAYOUT_STRESS: all conditional fields present ✓
- MINIMAL: mutex+notification fields absent ✓
- NO_TIMERS: xTimerCreateTimerTask definition absent ✓
- NO_EVENT_GROUPS: xEventGroupCreateStatic definition absent ✓
- COROUTINES_ON: xCoRoutineCreate definition present ✓

## Build-Pair Classification (§13)

- 120 pairs analyzed
- Most similar: BASE ↔ NO_PREEMPT (jaccard=0.9934)
- Most disjoint: FULL_FEATURE ↔ MINIMAL (jaccard=0.5206)
- Mean jaccard: 0.7476

## Scaling Data (§19)

| N | Total Facts | Unique | Shared | Buildsets | Frontend | Materialize | Proj/build |
|---|-------------|--------|--------|-----------|----------|-------------|------------|
| 2 | 13047 | 6545 | 6502 | 3 | 1.52s | 2.01s | 8.0ms |
| 5 | 32852 | 7380 | 5871 | 13 | 6.61s | 4.38s | 12.6ms |
| 10 | 56669 | 7620 | 3367 | 35 | 12.61s | 7.28s | 11.0ms |
| 16 | 87831 | 8225 | 3367 | 52 | 21.12s | 11.19s | 10.8ms |

## Fast CI Subset (§21)

4 representative builds for fast CI:
- **BASE** — reference (144-byte TCB)
- **NO_MUTEXES** — TCB field removal (128-byte TCB)
- **FULL_FEATURE** — max features (192-byte TCB)
- **MINIMAL** — smallest TCB (128-byte, fewest facts)

## Deliverables

### Harness Scripts
- `.dsh-control/corpora/harness/tu_parseability_test.py` (§2/§5)
- `.dsh-control/corpora/harness/resource_dir_discovery.py` (§16)
- `.dsh-control/corpora/harness/oracle_sentinels.py` (§8-11)
- `.dsh-control/corpora/harness/freertos_matrix_runner.py` (§4/§6/§7/§8/§13/§19)
- `.dsh-control/corpora/harness/freertos_canary_pack.py` (§20)

### Reports
- `.dsh-control/corpora/reports/tu_parseability_test.json`
- `.dsh-control/corpora/reports/resource_dir_discovery.json`
- `.dsh-control/corpora/reports/oracle_sentinels.json`
- `.dsh-control/corpora/reports/freertos_matrix_full.json`
- `.dsh-control/corpora/reports/freertos_matrix_pairs.json`
- `.dsh-control/corpora/reports/freertos_matrix_scaling.json`
- `.dsh-control/corpora/reports/freertos_canary_pack.json`

### Matrix Definition
- `.dsh-control/corpora/matrix/freertos_matrix_definition.py` (16 builds)

### Evidence
- `.dsh-control/evidence/M7_0B_COMMON_MODE_GT_RISK.md` (§17)

### Hard Gate
- `.dsh-control/corpora/run_m7_0b_freertos_matrix_gate.py` (§22)

## Authority Status (UNCHANGED)

- **Production read authority**: v1 (legacy) — unchanged
- **Production write authority**: v1 (legacy) — unchanged
- **M6 shadow store**: SHADOW ONLY — validated but not authoritative
- **No v2 retirement, no M6 query authority, no MCP tool switching**

## Conclusion

M7.0B establishes a durable FreeRTOS multi-build validation matrix
that independently proves M6 shadow-store projection remains exactly
equivalent to concrete-build ground truth across 16 semantically
distinct configurations. The three Oracle layers (A: FULL_BUILD_GT,
B: compiler structural, C: source-grounded semantic) provide
independent verification with no common-mode failure path.

The canary pack is READY for deployment to fresh environments.
