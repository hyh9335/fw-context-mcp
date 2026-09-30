# M5-3 Final LEGACY-GATE Review — fw-context

> HEAD `f57ff3d` (post M5-1 + M5-2). Every legacy code slice classified as
> KEEP_SEMANTIC_SAFETY / MOVE_TO_COMPATIBILITY / REMOVE_OBSOLETE.
> Guardian G3 reviews this.

## Method

Trace every legacy semantic-storage operation in the codebase and classify
it by its role in **normal/default operation** (compat mode OFF, the M5
production default). A slice is:

- **KEEP_SEMANTIC_SAFETY** — code that MUST stay in the normal path for
  v2 correctness (e.g., v2 query adapters that happen to be named "shadow").
- **MOVE_TO_COMPATIBILITY** — code that must be gated behind
  `_legacy_compat_on()` and only executes when the operator explicitly
  opts into legacy compatibility mode. Already gated by M5-1/M5-2.
- **REMOVE_OBSOLETE** — dead code that no longer serves any purpose in
  any mode. Per §41 stop boundary, M5 does NOT physically delete these —
  they are marked/quarantined for future optional cleanup.

## Classification

### KEEP_SEMANTIC_SAFETY (v2 read path — despite "shadow" naming)

| Module | Why kept |
|---|---|
| `v2_shadow_adapters.py` | These ARE the v2 read path. The "shadow" naming is from M1 when v2 was a shadow of legacy. After M3B, v2 is the read authority, so `v2_shadow_lookup_symbol`, `v2_shadow_find_callers`, etc. are the primary v2 query adapters called by `server.py`. Removing them would break all 29 CORE reads. |
| `authority.py: route_authority()` | v2-first routing with equivalence-gated fallback. This IS the M3B read authority layer. The `legacy_config_hash` lookup inside it is MOVE_TO_COMPATIBILITY (see below), but the function itself is the v2 read path. |
| `queryview.py` | Resolves QueryView → v2 World selection. Selects `world_status='active'` Worlds. This is v2-native. |
| `get_telemetry()` in `shadow.py` | Fallback-attempt counter. Used by `route_authority` for diagnostics. Not a legacy operation. |

### MOVE_TO_COMPATIBILITY (gated behind `_legacy_compat_on()`)

| Module / Operation | Gate | Status |
|---|---|---|
| `runner.py: open_db(db_path)` | `if _legacy_compat:` (line 508) | ✅ M5-1 |
| `runner.py: upsert_project / upsert_build_config` | `if _legacy_compat:` (line 534) | ✅ M5-1 |
| `runner.py: _store_linker_scripts` | `if _legacy_compat:` (line 983) | ✅ M5-1 |
| `runner.py: _store_asm` | `if asm_units and _legacy_compat:` (line 1001) | ✅ M5-1 |
| `runner.py: _run_postprocess` | `if _legacy_compat:` (line 1042) | ✅ M5-1 |
| `_unit_processor.py: store_symbols_for_unit` | Early return when `not _legacy_compat_on()` (line 551) | ✅ M5-1 |
| `authority.py: legacy_config_hash lookup` | `if _legacy_compat_on():` (line 347) | ✅ M5-1 |
| `stale.py: diagnose_empty_result` | `if _m5_legacy_compat:` (line 1153) | ✅ M5-1 |
| `shadow.py: run_shadow` body | `if _m5_compat:` (line 261) | ✅ M5-2 |
| `_postprocess.py` (all 18 steps) | Reached only via `_run_postprocess` (compat-gated) | ✅ M5-1 |

All 18 legacy postprocess steps (purge_missing, fts5, orphans, is_project,
manifest, generated_flag, coverage_purge, macros, dispatch_edges,
llm_analysis, embeddings, overrides, cross_tu_refs, variant_switches,
pagerank_hotspot, verify_integrity, finalize_manifest, cleanup_old,
wal_checkpoint) are compat-only — they execute only when
`_run_postprocess` is called, which is gated behind `_legacy_compat`.

### REMOVE_OBSOLETE (dead code — quarantined, not physically deleted)

| Module / Flag | Why obsolete | Action |
|---|---|---|
| `route_with_shadow()` in `authority.py` | M1/M2 dual-read comparator. Never called after M3B. Removed in M5-2. | ✅ Removed |
| `FW_CONTEXT_SHADOW_V2` env flag | Superseded by M4 write-authority (v2 always runs). Still read by `_v2_shadow_flag_on()` but that function no longer gates normal-mode behavior. | Quarantined — function retained for compat diagnostics but no normal-path call sites remain. |
| `_v2_shadow_flag_on()` calls in normal path | All 3 normal-path call sites removed in M5-2 (runner.py, _unit_processor.py, maintenance.py). Function definition retained (returns True when write-authority is on). | Quarantined — definition retained, no normal-path callers. |
| `shadow_v2_enabled()` call in `authority_mode()` | Replaced with `_legacy_compat_on()` check in M5-2. | ✅ Removed from `authority_mode()` |
| `shadow.py: run_shadow` comparator path | Dead code (never called in normal mode). Gated behind compat in M5-2. | Quarantined — compat-island only. |

## Verification

The M5 scanner (`scan_m5_state.py`) confirms all counters are 0:

```
LEGACY_DB reads=0 writes=0 creations=0
LEGACY_POSTPROCESS calls=0
LEGACY_FALLBACKS=0
LEGACY_MIRROR runs=0
LEGACY_ACTIVE_CONFIG deps=0
LEGACY_LIFECYCLE deps=0
SCAFFOLDING shadow_dual_read=0 temp_flags=0 comparators=0 dual_write=0
numeric_gate=PASS
```

## Conclusion

All legacy semantic-storage operations are gated behind `_legacy_compat_on()`
(default OFF). The normal v2-only path executes:

```
runner.run()
├── v2 world context init (ALWAYS)
├── per-TU: v2 certify + publish + bind (ALWAYS)
├── finalize_world(store, world_ctx) (ALWAYS — v2 authority)
└── return config_hash (SUCCESS)
```

No legacy DB opened. No legacy writes. No legacy postprocess. No migration
scaffolding in the normal path. The v2 read path (`v2_shadow_adapters.py`
functions + `route_authority`) is retained as KEEP_SEMANTIC_SAFETY.

Per §41 stop boundary: REMOVE_OBSOLETE items are quarantined, not physically
deleted. Physical deletion of legacy modules is a future optional task.
