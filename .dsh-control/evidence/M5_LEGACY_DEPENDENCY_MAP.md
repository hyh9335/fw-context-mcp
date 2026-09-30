# M5-0 Legacy Dependency Map — fw-context

> Runtime archaeology of legacy dependencies in normal/default operation
> at HEAD `8a75ce9` (post-M4 baseline). Classified by dependency type.
> Guardian G1 reviews this.

## Method

Trace `runner.run()` (the normal index entry point) and all functions it
calls UNCONDITIONALLY (not gated by a compatibility flag). A call is
"normal mode" if it executes when `v2_write_authority=True` (the M4
default) with no explicit compatibility mode selected.

## Normal index path — `runner.run()`

```
runner.run()
├── open_db(db_path)                    [LEGACY DB CREATION] line 500
├── conn.execute("SELECT ... build_configs")  [LEGACY DB READ] line 504
├── write_lock + transaction:
│   ├── upsert_project(conn, ...)       [LEGACY DB WRITE] line 524
│   └── upsert_build_config(conn, ...)  [LEGACY DB WRITE] line 525
├── [v2 path: world_ctx init + per-TU certify/publish/bind]  (M4)
├── finalize_world(store, world_ctx)    [v2 — M4 authority] line 927
├── _store_linker_scripts(conn, ...)     [LEGACY DB WRITE] line 967
├── _store_asm(conn, ...)                [LEGACY DB WRITE] line 992
└── _run_postprocess(conn, ...)          [LEGACY POSTPROCESS] line 1020
    ├── purge_missing                    [LEGACY_COMPAT]
    ├── fts5                             [LEGACY_COMPAT]
    ├── orphans                          [LEGACY_COMPAT]
    ├── is_project                       [LEGACY_COMPAT]
    ├── manifest                         [LEGACY_COMPAT]
    ├── generated_flag                   [LEGACY_COMPAT]
    ├── coverage_purge                   [LEGACY_COMPAT]
    ├── macros                           [LEGACY_COMPAT]
    ├── dispatch_edges                   [LEGACY_COMPAT]
    ├── llm_analysis                     [LEGACY_COMPAT]
    ├── embeddings                       [LEGACY_COMPAT]
    ├── overrides                        [LEGACY_COMPAT]
    ├── cross_tu_refs                    [LEGACY_COMPAT]
    ├── variant_switches                 [LEGACY_COMPAT]
    ├── pagerank_hotspot                 [LEGACY_COMPAT]
    ├── verify_integrity                 [LEGACY_COMPAT — M4-5 degraded]
    ├── finalize_manifest                [LEGACY_COMPAT — mirror only]
    ├── cleanup_old                      [LEGACY_COMPAT]
    └── wal_checkpoint                   [LEGACY_COMPAT]
```

## Normal read path — MCP handlers

After M3B, all 29 CORE semantic reads route through v2 (queryview selects
`world_status='active'` Worlds). No legacy fallback in normal read path.

## Dependency inventory

### SEMANTIC_SAFETY (needed by v2 correctness)
- **NONE.** v2 has its own certification, publication, binding, and World
  finalization. The legacy DB is not needed for v2 semantic correctness.
  M4 proved this: v2 finalization is the authoritative success criterion,
  and CASE B (v2 ok + legacy fail) still succeeds.

### EXPLICIT_COMPATIBILITY (needed only when compat mode is selected)
- **NONE currently explicit.** The legacy path runs unconditionally. M5-1
  must gate it behind an explicit `LEGACY_COMPATIBILITY_MODE` flag.

### MIGRATION_SCAFFOLDING (needed only during M1-M4 transition)
- `open_db(db_path)` — legacy DB open in runner.py:500 (migration scaffold)
- `upsert_project` / `upsert_build_config` — legacy writes (mirror)
- `_store_linker_scripts` — legacy linker script storage (mirror)
- `_store_asm` — legacy assembly storage (mirror)
- `_run_postprocess` — all 18 legacy postprocess steps (mirror)
- `load_manifest` / `manifest.get` — legacy manifest (mirror)
- `_v2_shadow_flag_on()` — M4 shadow flag (migration scaffold)
- `v2_shadow_adapters.py` — shadow dual-read adapters (migration scaffold)
- `authority.py` legacy_fallback references — migration scaffold

### OBSOLETE (no longer needed)
- `FW_CONTEXT_SHADOW_V2` env flag — superseded by M4 write-authority
- Shadow dual-read paths in `v2_shadow_adapters.py` — M3B retired read
  authority; shadow adapters are dead code in normal mode

### OPERATIONAL_NON_SEMANTIC
- `db_path` argument plumbing — the path is derived from config but the
  legacy DB doesn't need to be opened for v2 to work
- `write_lock` — file-system lock (useful for both, but v2 store has its
  own connection management)

## Key findings

1. **The legacy DB is opened UNCONDITIONALLY** (runner.py:500). This is
   the primary M5-1 target: gate it behind compat mode.

2. **Legacy postprocess runs UNCONDITIONALLY** (runner.py:1020). After
   M4-5, `verify_integrity` is degraded (CASE B), but all 18 steps still
   execute. M5-1 must gate the entire `_run_postprocess` call.

3. **Legacy writes are a mirror** (upsert_project, upsert_build_config,
   _store_linker_scripts, _store_asm). These are the "LEGACY_MIRROR_RUNS"
   detected by the scanner.

4. **Shadow adapters exist** (`v2_shadow_adapters.py`) but M3B retired
   read authority — they are dead code in normal mode. M5-2 removes them.

5. **The v2 path is already correct** (M4): finalize_world is the
   authority. M5 doesn't need to change v2 semantics — it needs to
   REMOVE the unconditional legacy path from normal mode.

## M5-1 implementation plan

Gate the legacy path behind `_legacy_compat_on()`:
- `open_db` → only if compat mode
- `upsert_project` / `upsert_build_config` → only if compat mode
- `_store_linker_scripts` → only if compat mode
- `_store_asm` → only if compat mode
- `_run_postprocess` → only if compat mode

When compat mode is OFF (default), the normal path is:
```
runner.run()
├── [v2 path: world_ctx init + per-TU certify/publish/bind]
├── finalize_world(store, world_ctx)    [v2 authority]
└── return config_hash                  [SUCCESS]
```

No legacy DB opened. No legacy writes. No legacy postprocess.

## M5-2 implementation plan

Remove migration scaffolding:
- `v2_shadow_adapters.py` — dead code (M3B retired read authority)
- `authority.py` legacy_fallback references — dead code
- `FW_CONTEXT_SHADOW_V2` flag — superseded
- `_v2_shadow_flag_on()` — superseded (write-authority implies shadow)

## M5-4 implementation plan

v2 lifecycle states (currently only 'active' and 'unfinalized'):
- Add 'failed' / 'abandoned' states for incomplete Worlds
- Add 'retained' for explicit retention
- Add 'pinned' for session pin protection
- Add 'gc_eligible' for GC candidacy

GC implementation:
- Reachability-based: roots = current completed Worlds + retained + pinned
- Shared SemanticState survives until ALL referencing Worlds are unreachable
- Staged: ANALYZE (dry-run) → APPLY (transactional) → VERIFY (integrity)
