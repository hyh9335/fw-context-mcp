# M6 Architecture Reference (frozen at HEAD 143642b)

This documents the ACCEPTED M6 architecture that M7 migrates INTO production
authority. It is a reference, not a redesign target.

## Source of truth

- Schema: `src/fw_context_mcp/store_v2/m6_schema.py`
- Store: `src/fw_context_mcp/store_v2/m6_store.py`
- Indexing adapter: `src/fw_context_mcp/store_v2/m6_indexing.py`
- Certification: `src/fw_context_mcp/store_v2/certify.py`
- Publication: `src/fw_context_mcp/store_v2/publish.py`
- Worlds/lifecycle: `src/fw_context_mcp/store_v2/worlds.py`
- GC: `src/fw_context_mcp/store_v2/gc.py`

## Architecture

```
Clang Concrete Build Oracle
        ↓
Canonical Shared Semantic Facts
        ↓
Build Presence / BuildSets
        ↓
Physical Bundles + Dependency Certificates
        ↓
Exact Build-specific semantic projection
```

## M6 fact kinds (12, frozen)

Symbol, Function, Call, Variable, Type, Field, EnumConst, Inherits,
Reference, ConditionalState, Body, FileContent.

Source: `m6_schema.py:55` enum comment + `m6_indexing.py:257-279` emitters.
Known naming inconsistency: schema enum says "EnumConst", emitter writes
"EnumConstant" (`m6_indexing.py:140`). Count is 12 either way.

## M6 status at HEAD 143642b

SHADOW ONLY. Proven by zero production-path imports:
- `grep 'm6_' src/fw_context_mcp/store_v2/query.py` → 0 matches
- `grep 'm6_store|m6_indexing|m6_schema' src/fw_context_mcp/mcp/shared/v2_shadow_adapters.py` → 0 matches
- `grep 'm6_store|m6_indexing|m6_schema|M6IndexingAdapter' src/fw_context_mcp/indexer/runner.py` → 0 matches

Only consumer: `m6_indexing.py:435 index_corpus_into_m6_store` — a §34
shadow-differential research function, not a production path.

## M6 validation evidence (frozen)

- M6 Phase I gate: 13/13 PASS (`run_m6_stage_gate.py`).
- M6.5 adversarial gate: 14/14 PASS (`run_m6_5_validation_gate.py`).
- M7.0B FreeRTOS matrix: 16 builds, 18/18 PASS, M6 projection parity = 0
  missing facts vs FULL_BUILD_GT across all 16 builds.

## What M7 changes

M7 does NOT redesign this architecture. M7:
1. M7.1: runs M6 alongside v2 through the real production MCP path
   (dual read), proving equivalence.
2. M7.2: makes M6 the production read authority (v2 becomes comparator).
3. M7.3: makes M6 the production write/materialization authority.
4. M7.4: makes M6 the production lifecycle authority.
5. M7.5: retires whole-TU v2 from normal operation (kept as benchmark/
   rollback island).
6. Economic closure + original Goal final audit.
