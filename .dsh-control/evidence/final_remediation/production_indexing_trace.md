# Production Indexing Trace — Final Remediation Audit

## HEAD: 16c9f7383dcbf2b7018eb658936e496b6f20e096
## Date: 2026-10-10

## ISSUE A: Production Semantic-Materialization Authority

### The REAL production indexing path

Traced from `src/fw_context_mcp/indexer/runner.py` `run()` function:

```
source files (C/C++)
  → Phase 1: discover compile_commands.json, build configs
  → Phase 2: parse translation units with libclang (legacy semantic extraction)
    → _process_unit() for each TU
    → writes symbols, call_sites, refs, overrides to legacy index.db
  → Phase 3: legacy index complete (db_path = legacy SQLite DB)
  → Phase 4: v2 World finalization (GATED OFF when M6 write authority ON)
  → M7.3 M6 PRODUCTION INDEXING (runner.py:1107-1141):
    → index_legacy_db_into_m6(legacy_db_path=db_path, ...)
    → opens legacy SQLite DB (sqlite3.connect(legacy_db_path))
    → reads symbols, call_sites, refs, overrides tables
    → converts rows to M6 canonical facts
    → writes to M6 store (index-m6.db)
  → return config_hash
```

### Answer: The production path is LEGACY-DB CONVERSION

```
source
→ legacy/v2 semantic indexing (full whole-TU materialization)
→ legacy DB (symbols, call_sites, refs, overrides)
→ index_legacy_db_into_m6 (reads legacy DB rows)
→ M6 canonical facts
```

This is NOT:
```
source → frontend → M6 certificate/reuse → M6 semantic materialization → M6 finalization
```

### Evidence

1. **runner.py:1107-1141**: M6 indexing runs AFTER legacy indexing completes. It receives `legacy_db_path=db_path` — the path to the legacy DB that was just written.

2. **m6_production_indexing.py:86-114**: `index_legacy_db_into_m6` opens the legacy SQLite DB, reads from `symbols`, `call_sites`, `refs`, `overrides` tables, and converts those rows into M6 facts.

3. **M6 store cannot be populated without the legacy DB**: If the legacy semantic materialization fails or is skipped, `index_legacy_db_into_m6` has nothing to read. The M6 store is a DERIVATIVE of the legacy DB, not an independent materialization.

4. **No certificate-based reuse**: The M6 store has `m6_certificates` table (0 rows in canary). There is no production code path that checks certificates before materializing facts. Every indexing run re-extracts all facts from the legacy DB.

5. **No incremental materialization**: There is no code path that detects which facts changed and only materializes those. Every run converts the entire legacy DB.

### ISSUE A VERDICT: CONFIRMED — M6 is NOT production semantic-materialization authority

Per §5 hard definition: "M6 counts as production semantic-materialization authority only if normal indexing can produce a correct M6 production semantic view without requiring a completed old semantic DB as the source of semantic facts."

**OLD_DB_ROWS_REQUIRED_AS_SEMANTIC_INPUT > 0** — the M6 store CANNOT be populated without the legacy DB. M6 is a post-hoc converter, not an independent materializer.

### The intended model (not yet implemented)

```
Clang/frontend
→ canonical semantic extraction
→ dependency certificate
→ safe reuse
→ only changed/new fact materialization
→ BuildSet ownership
→ M6 World finalization
```

### What needs to change

To make M6 truly production semantic-materialization authority:
1. Extract semantic facts DIRECTLY from the libclang frontend (during TU processing), not from the legacy DB after the fact
2. Check dependency certificates BEFORE materializing (skip unchanged TUs)
3. Only materialize changed/new facts
4. Write directly to M6 store (no legacy DB dependency)
5. The legacy DB may remain as a compat/benchmark mode, but NOT as a normal production prerequisite
