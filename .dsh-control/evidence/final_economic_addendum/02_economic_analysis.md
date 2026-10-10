# ECONOMIC ANALYSIS — §14-17, §19-20

## Raw benchmark data (FreeRTOS corpus, direct API, 1 repeat)

| Scale | REF Cumul (s) | M6 Cumul (s) | Cumul Reduction | REF Marginal (s) | M6 Marginal (s) | Marginal Reduction | Storage Reduction | M6 FE Share |
|---|---|---|---|---|---|---|---|---|
| 1 | 183.8 | 204.7 | -11.35% | 183.8 | 204.7 | -11.35% | 0% | 97.3% |
| 2 | 414.7 | 390.0 | +5.94% | 208.2 | 191.9 | +7.84% | 49.4% | 98.2% |
| 5 | 990.0 | 1043.1 | -5.36% | 204.9 | 207.9 | -1.46% | 79.7% | 98.3% |
| 10 | 2095.2 | 2025.0 | +3.35% | 197.2 | 222.8 | -12.94% | **89.8%** | 98.2% |

## Cost breakdown (per build, M6 mode, scale 5, last build)

| Component | Time (s) | Share |
|---|---|---|
| frontend_preprocess_parse (extract_all) | ~203.8 | 98.1% |
| semantic_materialization (materialize_unit_into_m6) | ~3.9 | 1.9% |
| total_wall_time | ~207.9 | 100% |

## Reuse reality (§15)

For later builds (scale 2, B2):
- reference_full_build_cost: 208.2s
- m6_incremental_cost: 191.9s
- absolute_saved_time: 16.3s
- relative_incremental_cost: 92.2% (only 7.8% reduction)

For later builds (scale 5, B5):
- reference_full_build_cost: 204.9s
- m6_incremental_cost: 207.9s
- absolute_saved_time: -3.0s (M6 is SLOWER)
- relative_incremental_cost: 101.5% (no reduction)

## Storage curve (§17)

| N Builds | REF Storage | M6 Storage | Reduction |
|---|---|---|---|
| 1 | 37.0 MB | 37.0 MB | 0% |
| 2 | 74.1 MB | 37.5 MB | 49.4% |
| 5 | 185.1 MB | 37.6 MB | 79.7% |
| 10 | 370.4 MB | 37.9 MB | **89.8%** |

**Storage scaling is materially improved** — M6 storage grows sublinearly
(37.0 → 37.5 → 37.6 → 37.9 MB) while reference grows linearly (37 → 74 →
185 → 370 MB). At scale 10, M6 uses **9.8× less storage**. This confirms
the structural-sharing benefit is REALIZED in the production path.

## Semantic materialization scaling

Materialization time per build:
- Build 1: ~4.9s (cold — all facts new)
- Build 2: ~3.5s (warm — 87,273 facts reused, 0 new)
- Build 5: ~3.9s (warm — near-total reuse)

**Semantic materialization IS materially reduced** — from ~4.9s (cold) to
~3.5-3.9s (warm), a ~28% reduction in the materialization component.
The 87,273 facts reused vs 0 new on Build 2 proves M6 reuse works perfectly.

## Frontend interpretation (§19)

Frontend (libclang parse) is **97-98%** of total M6 time. This is NOT
automatically failure — the real question is whether M6 removed the
REDUNDANT semantic work. It DID:
- Materialization: 4.9s → 3.5s (28% reduction)
- Storage: 37 MB → 37.5 MB for 5 builds (vs 185 MB linear)

But the frontend (203.8s per build) is NOT reduced because libclang
re-parses every TU from scratch. The architecture cannot reduce frontend
time without incremental parsing (which is explicitly OUT OF SCOPE per §28).

## §20 Assessment — TRUE FRONTEND-BOTTLENECK FAILURE

The evidence matches the §20 failure scenario:

```
reference independent later Build = 204.9s
M6 later Build = 207.9s
```

Later Builds continue costing approximately full indexing despite high
semantic overlap (87,273 facts reused, 0 new). The end-to-end time benefit
does NOT materialize because frontend (98.3% of time) is not reused.

**Production authority is COMPLETE.**
**Original economic Goal is NOT closed** — the reuse architecture works
perfectly at the semantic level (storage + materialization), but the
frontend parse bottleneck prevents end-to-end time benefit.

## Acceptance formula evaluation (§18, frozen before measurement)

1. **Semantic materialization improves materially**: 
   m6_incremental < 0.85 * reference_full_build?
   - Scale 2: 191.9 < 0.85 * 208.2 = 176.9? **NO** (191.9 > 176.9)
   - Scale 10: 222.8 < 0.85 * 197.2 = 167.6? **NO** (222.8 > 167.6)
   - **FAIL** — materialization COMPONENT improves (4.9→3.5s, ~28%) but
     total incremental wall time does NOT improve because frontend dominates

2. **Semantic storage improves materially**:
   m6_total_storage < 0.90 * reference_total_storage at 10 builds?
   - Scale 10: 37.9 MB < 0.90 * 370.4 = 333.4 MB? **YES** (89.8% reduction)
   - **PASS**

3. **Later overlapping Builds cost materially less OR cumulative shows gain**:
   m6_cumulative_10 < 0.90 * reference_cumulative_10?
   - Scale 10: 2025.0 < 0.90 * 2095.2 = 1885.7? **NO** (2025.0 > 1885.7)
   - **FAIL** — cumulative time is only 3.35% reduced, not 10%

4. **Query performance within bounds**: **PASS** (verified by tool_parity)

5. **No correctness regression**: **PASS** (GT parity 10/10, 0 false-shared, isolation OK)

**Formula result: 3/5 PASS, 2/5 FAIL → ECONOMIC GOAL NOT CLOSED**

The M6 Shared Fact Graph architecture WORKS PERFECTLY at the semantic level:
- 89.8% storage reduction at scale 10
- 28% materialization time reduction on warm builds
- 87,273 facts reused, 0 new on Build 2

But the frontend (libclang parse, 98.2% of time) is NOT reused across
builds, so the end-to-end wall-time benefit is negligible (3.35% cumulative
at scale 10, negative marginal at scales 5 and 10).
