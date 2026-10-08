# M6 Completion Report — CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE

**Date:** 2026-10-01
**HEAD:** See `git rev-parse HEAD` for current durable checkpoint
**Milestone:** `M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE_COMPLETE`

## Executive Summary

M6 implements a **Build-Conditioned Shared Semantic Fact Graph** that
preserves an exact semantic view for every explicit Snapshot × Build while
sharing unchanged semantic structure across Builds at finer granularity
than the whole TU. The implementation uses a 4-layer architecture:
Clang Oracle → Canonical Shared Facts → BuildSets → Physical Bundles +
Semantic Dependency Certificates.

**All 12 hard gates PASS.** The M6 store achieves 75.1% cross-build
sharing on a real multi-file C firmware corpus (DEBUG vs RELEASE builds)
with zero parity failures and zero false reuse.

## Phase R (Research/Prototype) — COMPLETE

| Prototype | Result |
|---|---|
| Prototype A (Fact + BuildSet) | 21/21 cases PASS, false_shared=0, parity_failures=0 |
| Prototype C (Certificate) | safety_violation=False, total_false_reuse=0 |
| Mutation testing | 14/14 CAUGHT (M1-M14) |
| Arbiter verdict | ACCEPT_WITH_MODIFICATIONS (5 mods incorporated) |

## Phase I (Production Implementation) — COMPLETE

### Implementation Layers

| Layer | Description | Status |
|---|---|---|
| I1 | m6_schema.py v2 (tables, fact_kind enum) | DONE |
| I2 | m6_store.py §M1 GC model | DONE |
| I3 | Concurrency (WAL, write lock) | DONE (4/4 tests) |
| I4 | Body + FileContent fact extraction | DONE |
| I5 | Reference fact extraction (6 ref_kinds) | DONE |
| I6 | ConditionalState fact extraction | DONE |
| I7 | Certificate store integration (5 legs) | DONE (7/7 steps) |
| I8 | M6 indexing adapter | DONE |
| I9 | Cross-TU resolution (per-Build symbol table) | DONE |
| I10 | GC integration with Worlds lifecycle | DONE (8/8 steps) |
| I11 | §36 real corpus shadow differential | DONE (75.1% sharing) |
| I12 | Cost class separation + cert safety | DONE |

### Hard Gate Results (12/12 PASS)

| Gate | Requirement | Result |
|---|---|---|
| GATE 1 | QUERY_PARITY_FAILURES == 0 (§34) | PASS (39/39 builds, 21 cases) |
| GATE 2 | MUTATION_ALL_CAUGHT (§35) | PASS (21/21: M1-M21) |
| GATE 3 | GC_SAFETY (§M1.6) | PASS (5/5 tests) |
| GATE 4 | CERT_SAFETY | PASS (safety_violation=False) |
| GATE 5 | Phase R regression | PASS (21/21, false_shared=0) |
| GATE 6 | M5 regression | PASS (39/39 clauses) |
| I3 | Concurrency | PASS (4/4 tests) |
| I7 | Certificate Store | PASS (7/7 steps) |
| I9 | Cross-TU Resolution | PASS |
| I10 | GC Lifecycle | PASS (8/8 steps) |
| I11 | Real Corpus §36 | PASS (75.1% sharing, 11/12 kinds) |
| I12 | Cost + Cert Safety | PASS (3 cost classes, no false reuse) |

## Architecture (FROZEN)

### 4-Layer Model

1. **Clang Oracle** — libclang extracts semantic facts per TU
2. **Canonical Shared Facts** — fact identity independent of any Build
3. **BuildSets** — interned frozensets of build_names; projection = filter
4. **Physical Bundles + Certificates** — storage + dependency certs

### 12 Fact Kinds

Symbol, Function, Call, Variable, Type, Field, EnumConst, Inherits,
Reference (6 ref_kinds), ConditionalState, Body, FileContent.

### Certificate (5 Legs)

- L0: file bytes (SHA-256)
- L1: include closure (libclang `tu.get_includes()`)
- L2: macro state (MACRO_DEFINITION cursor walk)
- MacroEnv: stateful macros (`__COUNTER__`, `__DATE__`, `__TIME__`)
- L3: toolchain flags
- Defense-in-depth: `fact_set_digest` comparison

### GC (Reachability-Based)

- World → Builds map (`m6_world_builds`)
- Per-build refcounts
- Fact reachability = `BuildSet ∩ {builds with refcount > 0} ≠ ∅`
- Per-World removal FORBIDDEN
- Age-only GC FORBIDDEN

## Key Results

### Cross-Build Sharing (Real Corpus)

| Metric | Value |
|---|---|
| DEBUG facts (RED baseline, no sharing) | 362 |
| RELEASE facts (RED baseline, no sharing) | 307 |
| RED total (no sharing) | 669 |
| GREEN total (M6 store with sharing) | 229 |
| Shared facts | 172 (75.1%) |
| Storage reduction vs RED | 65.8% (669 → 229) |
| Semantic materialization reduction | 94.5% (217 → 12 new facts for 2nd build) |
| Query overhead | 0.001s |
| Fact-kind coverage | 11/12 (only Inherits missing — C, not C++) |

### §10 Usefulness Gate — Frozen Thresholds

| Check | Measured | Threshold (conservative) |
|---|---|---|
| storage_reuse_ratio | 0.751 | ≥ 0.50 |
| storage_reduction_vs_red | 0.658 | ≥ 0.25 |
| semantic_materialization_reduction | 0.945 | ≥ 0.50 |
| query_overhead_s | 0.001s | ≤ 5.0s |

### Cost Class Separation

| Build | frontend_parse_s | semantic_materialization_s | storage_writes |
|---|---|---|---|
| DEBUG | 0.0616s | 0.0206s | 217 new facts |
| RELEASE | 0.0307s | 0.0116s | 12 new facts (205 reused) |

### Certificate Safety

- Same defines → same cert_hash → same facts (no false reuse)
- Different defines → different cert_hash → different facts
- safety_violation = False

## Files

### Production Code
- `src/fw_context_mcp/store_v2/m6_schema.py` — schema v2
- `src/fw_context_mcp/store_v2/m6_store.py` — BuildSetStore with GC
- `src/fw_context_mcp/store_v2/m6_indexing.py` — M6IndexingAdapter

### Research/Prototype
- `.dsh-control/m6_research/prototype_a.py` — Fact + BuildSet prototype
- `.dsh-control/m6_research/prototype_c.py` — Certificate prototype
- `.dsh-control/m6_research/mutation_testing.py` — M1-M14 mutations
- `.dsh-control/m6_research/full_build_gt.py` — GT extraction

### Tests
- `.dsh-control/m6_research/test_shadow_differential.py` — §34
- `.dsh-control/m6_research/test_shadow_mutation.py` — §35 (M1-M21)
- `.dsh-control/m6_research/test_shadow_store.py` — §M1.6 GC
- `.dsh-control/m6_research/test_concurrency.py` — I3
- `.dsh-control/m6_research/test_certificate_store.py` — I7
- `.dsh-control/m6_research/test_cross_tu.py` — I9
- `.dsh-control/m6_research/test_gc_lifecycle.py` — I10
- `.dsh-control/m6_research/test_real_corpus.py` — I11/§36
- `.dsh-control/m6_research/test_cost_and_cert.py` — I12

### Contracts
- `.dsh-control/contracts/IMMUTABLE_M6_GOAL_CONTRACT.md` — frozen
- `.dsh-control/contracts/M6_ARCHITECTURE_CONTRACT.md` — frozen

### Gate
- `.dsh-control/gates/run_m6_stage_gate.py` — M6 Hard Gate

## §10 Usefulness Gate

The M6 store achieves 75.1% cross-build sharing on a real C firmware
corpus, well above the implicit RED baseline of 0% (no sharing). The
storage work scales with semantic differences (12 new facts for RELEASE
vs 217 for DEBUG), not build count. Query overhead is negligible (parity
checks complete in <1s per build).

## Final Acceptance

### Fresh M5 Reviewers (at HEAD `21c1f08`)
- **M5 Verifier:** `.dsh-control/evidence/m5_verifier_20261008_154126_e1b3480.json`
  (verdict=PASS, head=21c1f08, m5_code_unchanged=true, 39/39 clauses)
- **M5 Guardian G6:** `.dsh-control/evidence/m5_guardian_20261008074407Z_e1b3480d3f32.json`
  (verdict=ACCEPT_COMPLETION, head=21c1f08, all 6 adversarial checks TRUE)

### Fresh M6 Reviewers (at HEAD `21c1f08`)
- **M6 Verifier:** `.dsh-control/evidence/m6_verifier_final_20261008_155104.json`
  (verdict=ACCEPT, 13/13 gates, all contract compliance true)
- **M6 Guardian G6:** `.dsh-control/evidence/m6_guardian_final_20261008T155201Z.json`
  (verdict=ACCEPT_COMPLETION, 10/10 adversarial checks pass, both blockers resolved)

### Milestone Status

**`M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE_COMPLETE`**

Declared complete at HEAD `21c1f0892589d61575d29db7e67027d6a2fb21d5`:
- All 13 M6 Hard Gates PASS (executable: `run_m6_stage_gate.py` exits 0)
- All 39 M5 clauses PASS (executable: `run_m5_stage_gate.py` exits 0)
- Fresh Verifier + Fresh Guardian G6 accepted this exact HEAD
- M6 store remains shadow-only; v2 store remains Ground Truth
- Production authority NOT transitioned (M6 validated as shadow;
  transition is a separate future decision per Architecture Contract §3)
