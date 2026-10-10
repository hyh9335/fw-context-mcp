# TERMINAL VERDICT (§27) — FINAL ECONOMIC CLOSURE ADDENDUM

## Verdict: FUNCTIONAL_COMPLETE / ECONOMIC_GOAL_OPEN

---

## Summary

The FINAL ECONOMIC CLOSURE ADDENDUM is complete. The terminal verdict is
**FUNCTIONAL_COMPLETE_ECONOMIC_GOAL_OPEN** — production authority is
complete and verified, but the original economic Goal is NOT closed due
to the frontend (libclang parse) bottleneck.

This verdict was determined by the §26 hard gate
(`run_final_economic_closure_addendum_gate.py`), which exits 0 with this
verdict. It was accepted by a fresh final guardian G6 (§25).

---

## Part A: Production Write-Entrypoint Denominator (RESOLVED)

**Question:** Is `reindex_file` normal production? If yes, must use M6
direct materialization.

**Answer:** YES, `reindex_file` is normal production (MCP tool registered
at `server.py:1161`, called by LLM agent post-edit; also by CLI watch
daemon). It WAS M6-stale (defect). Now FIXED via `_reindex_m6_direct`
helper (maintenance.py:2072).

After the fix:
- All 3 normal write entrypoints are M6-direct:
  - `runner.run()` → `_process_unit` → `materialize_unit_into_m6`
  - `reindex_file_impl` → `_reindex_m6_direct` → `materialize_unit_into_m6`
  - CLI watch daemon → spawns `fw-context index` → `runner.run()` → M6-direct
- 0 normal old-semantic-materialization entrypoints
- Fresh write-entrypoint reviewer (§23): **PASS**

**§7 condition satisfied:** NORMAL_WRITE_ENTRYPOINTS == M6_DIRECT_WRITE_ENTRYPOINTS

---

## Part B: End-to-End Multi-Build Economics (RESOLVED)

**Question:** Does adding highly-overlapping Builds materially reduce
indexing work/wall-time vs independent whole-TU indexing?

**Answer:** NO. The M6 Shared Fact Graph architecture removes redundant
SEMANTIC work perfectly (89.8% storage reduction at scale 10, 87,273 facts
reused on Build 2 with 0 new), but the frontend (libclang parse, 98.2% of
M6 time) is NOT reused across builds. End-to-end wall-time benefit is
negligible (3.35% cumulative at scale 10, within the ~11% noise floor).

### Benchmark results (FreeRTOS corpus, 10 builds, direct API)

| Scale | REF Cumul (s) | M6 Cumul (s) | Cumul Reduction | Storage Reduction | M6 FE Share |
|---|---|---|---|---|---|
| 1 | 183.8 | 204.7 | -11.35% | 0% | 97.3% |
| 2 | 414.7 | 390.0 | +5.94% | 49.4% | 98.2% |
| 5 | 990.0 | 1043.1 | -5.36% | 79.7% | 98.3% |
| 10 | 2095.2 | 2025.0 | +3.35% | **89.8%** | 98.2% |

### Acceptance formula (§18, frozen before measurement)

| # | Criterion | Result | Detail |
|---|---|---|---|
| 1 | m6_incremental < 0.85 * ref_marginal | **FAIL** | 191.9s vs 176.9s threshold |
| 2 | m6_storage_10 < 0.90 * ref_storage_10 | **PASS** | 37.9 MB vs 333.4 MB threshold |
| 3 | m6_cumulative_10 < 0.90 * ref_cumulative_10 | **FAIL** | 2025.0s vs 1885.7s threshold |
| 4 | Query performance within bounds | **PASS** | tool_parity PASS |
| 5 | No correctness regression | **PASS** | GT parity 10/10, 0 false-shared |

**Result: 3/5 PASS, 2/5 FAIL → ECONOMIC GOAL NOT CLOSED**

### §20 Failure scenario: TRUE FRONTEND-BOTTLENECK FAILURE

The evidence matches the §20 failure scenario:
```
reference independent later Build = 204.9s
M6 later Build = 207.9s
```
Later Builds continue costing approximately full indexing despite high
semantic overlap (87,273 facts reused, 0 new). The end-to-end time benefit
does NOT materialize because frontend (98.3% of time) is not reused.

### Why this is NOT a design defect

The M6 architecture works PERFECTLY at the semantic level:
- Storage: 89.8% reduction (370 MB → 37.9 MB at scale 10)
- Materialization component: 28% reduction (4.9s → 3.5s on warm builds)
- Fact reuse: 87,273 facts reused, 0 new on Build 2

The frontend bottleneck is a SEPARATE concern (§19): libclang re-parses
every TU from scratch. Removing this requires incremental parsing, which
is explicitly OUT OF SCOPE per §28.

---

## Gate results (§26)

```
TOTAL: 11/13 PASS, 2 FAIL

PRODUCTION_AUTHORITY: PASS
CORRECTNESS: PASS
ECONOMIC: 1/3 PASS (storage only)
REVIEWS: PASS
HEAD: PASS

TERMINAL VERDICT (§27): FUNCTIONAL_COMPLETE_ECONOMIC_GOAL_OPEN
GATE EXIT: 0
```

### Fresh reviews

| Review | Verdict | Key finding |
|---|---|---|
| Write-entrypoint (§23) | PASS | No normal write path bypasses M6; 3 denominator reasoning errors corrected |
| Economics (§24) | PASS | Evidence honest, conclusion follows from data; 5 artifacts found, none conclusion-invalidating |
| Final guardian G6 (§25) | ACCEPT_COMPLETION | All 8 checks pass, 0 blocking issues |

---

## What was accomplished

1. **reindex_file M6-direct fix** (commit `03d58de`): closed the §7 bypass
   where reindex_file_impl bypassed M6 direct materialization. Added
   `_reindex_m6_direct` helper. Permanent regression test added.

2. **Benchmark methodology frozen** (before measurement): 10 builds with
   different -D flags, scales [1,2,5,10], acceptance formula with 5
   criteria.

3. **Benchmark executed**: direct API (bypassing runner.run for precise
   build_name control), REFERENCE vs M6 mode, 4 scales.

4. **Correctness verified**: GT parity 10/10, 0 false-shared facts,
   multi-build isolation confirmed.

5. **All regression tests green**: M6.5 adversarial, M7 direct materialization,
   tool parity, multi-build world, reindex_file green.

---

## What remains OPEN

The original economic Goal (`ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED`)
remains **PROVISIONAL** — the addendum does NOT close it. The economic
benefit of multi-Build reuse is real at the semantic level (storage +
materialization) but does NOT translate to end-to-end wall-time benefit
because the frontend (libclang parse) dominates (98.2%) and is not reused.

Closing the economic Goal would require incremental frontend parsing
(§28 stop scope — explicitly out of scope for this addendum).

---

## Binding

- HEAD: `588d83c` (branch `m3b-native-v2`)
- Denominator HEAD: `e828081` (the prior "TRUE COMPLETE" verdict being re-derived)
- reindex_file fix commit: `03d58de`
- Clang: v22.1.8
- FreeRTOS corpus: pin `8be86d4`
- Gate exit: 0
- Fresh guardian: ACCEPT_COMPLETION
