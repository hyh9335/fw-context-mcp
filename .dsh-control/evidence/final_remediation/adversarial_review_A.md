# Adversarial Review — Issue A Remediation (M6 Production Semantic-Materialization Authority)

**Reviewer:** Fresh adversarial reviewer (no prior context)
**Date:** M7 FINAL CLOSURE REMEDIATION AUDIT
**Scope:** Verify that M6 is the production semantic-materialization authority via DIRECT materialization from libclang `ExtractionResult` (NOT legacy DB conversion).
**§5 hard definition:** *"M6 counts as production semantic-materialization authority only if normal indexing can produce a correct M6 production semantic view WITHOUT requiring a completed old semantic DB as the source of semantic facts."*

---

## VERDICT: APPROVED

The §5 hard definition is **satisfied**. Normal indexing (`runner.run` → `_process_unit`) materializes canonical M6 facts DIRECTLY from the libclang `ExtractionResult` (symbols, call_sites, references, inheritance) without opening, querying, or requiring the legacy semantic DB. The legacy-DB conversion path (`index_legacy_db_into_m6`) is opt-in (`FW_CONTEXT_M6_LEGACY_CONVERSION=1`, default `"0"`) and is never reached in normal production. The FreeRTOS canary proves 1756 facts produced with `legacy_db_exists=false`.

Five holes were found (none block §5; they are robustness / observability / completeness / documentation concerns). They are documented below with severity.

---

## Question-by-Question Findings

### Q1. Does `materialize_unit_into_m6()` read from the libclang `ExtractionResult` (NOT the legacy DB)?

**FINDING: YES.**

`materialize_unit_into_m6()` reads exclusively from the in-memory `parsed` object (`ExtractionResult`):
- `parsed.symbols` → `m6_direct_materialization.py:90`
- `parsed.call_sites` → `m6_direct_materialization.py:179`
- `parsed.references` → `m6_direct_materialization.py:208`
- `parsed.inheritance` → `m6_direct_materialization.py:234`

All four are real attributes of the `ExtractionResult` dataclass (`symbols.py:3485-3495`: `symbols`, `references`, `inheritance`, `indirect_call_sites`, `call_sites`). The function uses `getattr(parsed, ..., None) or []` for safety, but the attributes genuinely exist on the frontend result.

The function performs **no** `sqlite3.connect` against any legacy DB and imports no legacy-DB reader. The only `sqlite3.connect` to a legacy `index.db` in the M6 stack is in `index_legacy_db_into_m6` (`m6_production_indexing.py:86`), which is the explicit compat path — not this function. The `BuildSetStore` it writes to targets `index-m6.db` (`m6_query.py:29-32`), a separate file from the legacy `index.db`.

Every fact payload is stamped `"source": "direct_frontend"` (e.g. `m6_direct_materialization.py:130,140,150,166,197,223,245`), providing traceability.

### Q2. Is the per-TU hook in `_unit_processor.py` wired to run for EVERY TU during normal indexing?

**FINDING: YES, for every TU that reaches Phase-2 storage. Two correct-by-design skips, plus one real gap.**

The hook lives in `_process_unit` at `_unit_processor.py:548-593`. It is gated on `_m6_write_authority` (env `FW_CONTEXT_M6_WRITE_AUTHORITY`, default `"1"` → ON) and `world_ctx is not None`. It runs AFTER the v2 certify hook (`_unit_processor.py:536-546`) and BEFORE the `_legacy_compat_on()` early-return (`_unit_processor.py:601-606`). So it runs in **both** v2-only (default) and legacy-compat modes.

`_process_unit` is invoked once per TU from `runner.run`'s main loop (`runner.py:914`), inside the per-TU write lock, with `world_ctx=world_ctx` (`runner.py:931`). `world_ctx` is built once per run (`runner.py:752`) and the M6 store is attached once (`runner.py:770-774`).

**Correct-by-design skips (NOT holes):**
- Fast-reuse admission hit (`runner.py:842-858`): a TU with a committed v2 binding `continue`s before `_process_unit`. On a FRESH index there are no prior bindings, so every TU falls through to parse + `_process_unit`. For unchanged TUs on a re-index, M6 facts already persist in the M6 store from the prior run — skipping re-materialization is correct.
- Staleness "unchanged" (`runner.py:891-905`): same reasoning.

**GAP (see Hole H2):** the single-file `reindex_file` path (`ops.py`) does NOT call `_process_unit` (grep of `ops.py` for `_process_unit` / `runner.run` / `from.*_unit_processor` returns no matches) and therefore does NOT run the M6 direct-materialization hook. This is out of §5's "normal indexing" scope but is an operational gap.

### Q3. Does the runner.py M6 World finalization use the direct-materialized facts (NOT `index_legacy_db_into_m6`) in normal production mode?

**FINDING: YES.**

`runner.py:1141-1205` reads two flags:
- `FW_CONTEXT_M6_WRITE_AUTHORITY` default `"1"` → ON (line 1142-1143)
- `FW_CONTEXT_M6_LEGACY_CONVERSION` default `"0"` → OFF (line 1144-1146)

When `_m6_legacy_conversion` is OFF (the default), the `if _m6_legacy_conversion:` branch (line 1155, calling `index_legacy_db_into_m6`) is skipped, and the `elif _m6_store is not None:` branch (line 1177) runs `finalize_m6_world` (`m6_direct_materialization.py:259`) on the store that was populated per-TU by direct materialization. The finalization raises `IndexIntegrityError` on failure (lines 1190-1205) — it does NOT swallow.

### Q4. Is `FW_CONTEXT_M6_LEGACY_CONVERSION=0` the DEFAULT (so normal production uses direct materialization)?

**FINDING: YES.**

`runner.py:1145`: `os.environ.get("FW_CONTEXT_M6_LEGACY_CONVERSION", "0")`. Default `"0"`. The truthy set is `("1","true","yes","on")` (line 1146). `"0"` is not in that set, so `_m6_legacy_conversion` is `False` in normal production. Direct materialization is the default path.

The GREEN test (`test_direct_materialization_green.py`) and the FreeRTOS canary (`test_freertos_production_canary.py:66`) both set `FW_CONTEXT_M6_LEGACY_CONVERSION=0` explicitly, matching the default.

### Q5. Are there any code paths where M6 still depends on the legacy DB in normal mode?

**FINDING: NO materialization/read dependency in normal mode. One stale docstring.**

- The ONLY call site of `index_legacy_db_into_m6` is `runner.py:1163`, gated behind `FW_CONTEXT_M6_LEGACY_CONVERSION=1` (default off). In normal mode it is never imported (lazy import at line 1160-1162) and never executed.
- The M6 query layer (`m6_query.py`) reads only from the M6 store (`index-m6.db`); its docstring explicitly states "It does NOT read from the legacy DB or v2 store" (`m6_query.py:8-9`). Confirmed: `open_m6_store` (`m6_query.py:35-40`) opens `index-m6.db`, never `index.db`.
- Per `AGENTS.local.md`: "Importing an isolated, never-executed compatibility module is NOT semantic authority." The legacy converter is lazily imported only inside the opt-in branch — it is never executed in normal production. ✓

**Hole H4 (LOW):** `m6_query.py:11` docstring is STALE: it says "The M6 store is populated by m6_production_indexing.index_legacy_db_into_m6() during the normal indexing pipeline." This contradicts the remediation (normal path is now `materialize_unit_into_m6`). Documentation defect that could mislead future reviewers; does not affect runtime behavior.

### Q6. Does the FreeRTOS canary prove that 1756 facts were produced with `legacy_db_exists=False`?

**FINDING: YES. The canary proves the core claim. One completeness caveat.**

`freertos_production_canary.json`:
- `"total_m6_facts": 1756` ✓
- `"legacy_db_exists": false` ✓
- `"legacy_db_rows": 0` ✓
- `"direct_materialization_used": true` ✓
- `"old_db_rows_required_as_semantic_input": "0"` ✓
- `"m6_is_materialization_authority": true` ✓

The canary ran the REAL `runner.run()` (`test_freertos_production_canary.py:76-86`) with `force=True`, `FW_CONTEXT_M6_LEGACY_CONVERSION=0` (line 66), `FW_CONTEXT_M6_WRITE_AUTHORITY=1` (line 65). It copied a real FreeRTOS `compile_commands.json` into a clean project root (lines 51-55) and verified the legacy `index.db` was not created / had zero symbol rows (lines 145-158). This is genuine end-to-end production evidence: 1756 M6 facts materialized directly from libclang with no legacy DB.

**Hole H3 (LOW-MEDIUM): completeness / weak pass criterion.** The canary found only 5 of 7 expected FreeRTOS symbols: `xQueueCreate` and `xQueueSend` are MISSING (`freertos_production_canary.json:33-36`). The test explicitly tolerates this (`test_freertos_production_canary.py:139`: "Not all may be present depending on config; count what we found") and passes on the weak criterion `total_facts > 0` (line 169). The two missing symbols are almost certainly macro-defined (FreeRTOS `queue.h` often wraps `xQueueCreate`/`xQueueSend` in macros, which libclang symbol extraction does not surface as function definitions). This is a completeness gap in the direct-materialization output vs. an idealized symbol set, NOT a §5 authority violation (§5 requires M6 CAN produce a correct view without the legacy DB, which it does). Recommend strengthening the canary to compare direct-materialization fact counts against a legacy-conversion baseline on the same corpus to detect silent regressions in coverage.

### Q7. Is the world_id derivation in `_unit_processor.py` correct (NOT hardcoded 1)?

**FINDING: YES in the per-TU hook. One dead-code `1` in runner finalization.**

Per-TU hook (`_unit_processor.py:564-578`):
```python
_m6_world_id = getattr(world_ctx, "world_id", None)
if not _m6_world_id:
    _m6_build_name_fallback = getattr(world_ctx, "build_name", None) or "default"
    _m6_world_id = (int(hashlib.sha256(_m6_build_name_fallback.encode()).hexdigest()[:8], 16) % 1000000) + 1
    world_ctx.world_id = _m6_world_id
```
The primary source is `world_ctx.world_id`, which is set by the v2 certify path: `certify_and_publish_for_unit` (`certify.py:1081`) → `_v2_bind_tu` (`certify.py:1214-1218`) → `bind_tu` → `_ensure_world_rows` (`worlds.py:219`) → `ctx.world_id = int(row["world_id"])` (`worlds.py:304`). Because the v2 certify hook (`_unit_processor.py:540`) runs BEFORE the M6 hook (`_unit_processor.py:559`), `world_ctx.world_id` is populated by the time the M6 hook reads it. The fallback is a deterministic SHA-256 hash of `build_name` — NOT hardcoded `1`, and stable across restarts. The hook also writes the derived id back to `world_ctx.world_id` (line 578), so runner finalization reads the same value.

In the canary, `FW_CONTEXT_SHADOW_V2=1` (`test_freertos_production_canary.py:70`) keeps v2 certify active (`v2_shadow_enabled()`, `certify.py:1042-1044`), so `_ensure_world_rows` runs and sets a real `world_id` even though `FW_CONTEXT_V2_WRITE_AUTHORITY=0`.

**Hole H5 (LOW): dead `or 1` fallback.** `runner.py:1151-1152`:
```python
_m6_world_id = (getattr(world_ctx, "world_id", None) if world_ctx else None) or 1
```
This `or 1` is effectively dead code: if `world_ctx is None`, then `_m6_store` is also `None` (line 1153), so the `elif _m6_store is not None:` finalization branch (line 1177) is skipped entirely — `world_id=1` is never used for finalization in production. Still, a hardcoded `1` is misleading in a module that claims "MUST NOT be hardcoded to 1"; recommend replacing with an explicit guard that raises if `world_id` is unset when M6 authority is on.

### Q8. Are there any silent fallbacks that could hide failures?

**FINDING: YES — this is the primary hole. Three silent-swallow points.**

**Hole H1 (MEDIUM) — silent M6 failures.**

1. **M6 store open failure** (`runner.py:782-785`): if `open_m6_store(project_root)` raises, it is caught, logged at `log.debug`, and `world_ctx.m6_store = None`. The per-TU hook then skips (`_unit_processor.py:581`: `if _m6_store is not None:`), and finalization skips (`runner.py:1177`: `elif _m6_store is not None:`). Indexing completes "successfully" with **ZERO M6 facts and NO error**. If M6 is the production semantic-materialization authority, a total failure to open the authority store should be a hard error, not a debug log.

2. **Per-TU materialization failure** (`_unit_processor.py:591-593`): any exception inside `materialize_unit_into_m6` is caught and logged at `log.debug` (`# pragma: no cover` — untested). That TU's facts are silently absent from M6, and finalization counts only the surviving facts with no signal that TUs were dropped.

3. These match the existing v2-shadow swallow pattern (`_unit_processor.py:544-546`), so they are consistent with codebase conventions — but for a module now claimed to be the *authority*, the bar should be higher.

The finalization step itself (`runner.py:1184-1205`) does NOT swallow — it raises `IndexIntegrityError` on failure. ✓ But that only catches finalization-time errors; per-TU and store-open errors upstream are invisible.

**Impact on §5:** §5 asks whether normal indexing *can* produce a correct M6 view without the legacy DB. It can (proven by the canary + GREEN test). The silent fallbacks are a robustness/observability defect: when M6 *fails*, it fails invisibly. This does not violate §5's capability requirement, but it weakens the operational guarantee that "M6 is the authority" — an operator could be running with an empty M6 store and never know. Recommend: when `_m6_write_authority` is ON and the store fails to open or a per-TU materialization fails, emit at least a `log.warning` (not `debug`) and, for store-open failure, raise unless an explicit `FW_CONTEXT_M6_BEST_EFFORT=1` escape is set.

---

## Holes / Defects Summary

| ID | Severity | Location | Description |
|----|----------|----------|-------------|
| H1 | MEDIUM | `runner.py:782-785`; `_unit_processor.py:591-593` | Silent fallbacks: M6 store-open failure and per-TU materialization failure are swallowed at `log.debug`. A total M6 failure produces zero facts with no error, undermining the "authority" guarantee operationally. Does not violate §5 (capability) but weakens operational trust. |
| H2 | MEDIUM (secondary) | `indexer/ops.py` (`reindex_file`) | The single-file `reindex_file` path does not call `_process_unit` and does not run the M6 direct-materialization hook. Re-indexing one file leaves the M6 store stale for that file. Out of §5 "normal indexing" scope, but an operational gap. The comment at `runner.py:9` ("runner, reindex_file, and auto-reindex all use the same code path") appears stale for the M6 hook. |
| H3 | LOW-MEDIUM | `test_freertos_production_canary.py:139,169`; canary JSON `missing_symbols` | Canary pass criterion is weak (`total_facts > 0`) and 2/7 expected FreeRTOS symbols (`xQueueCreate`, `xQueueSend`) are missing. Likely macro-defined symbols not surfaced by libclang symbol extraction — a completeness gap, not an authority failure. Recommend a direct-vs-legacy fact-count baseline comparison. |
| H4 | LOW | `m6_query.py:11` | Stale docstring claims M6 is "populated by index_legacy_db_into_m6() during the normal indexing pipeline," contradicting the direct-materialization remediation. Documentation-only; no runtime effect. |
| H5 | LOW | `runner.py:1151-1152` | Dead `or 1` world_id fallback. Unreachable in production (requires `world_ctx is None` → `_m6_store is None` → finalization skipped), but the hardcoded `1` contradicts the "MUST NOT be hardcoded to 1" contract stated in the per-TU hook. |

---

## §5 Hard Definition — Satisfaction Assessment

§5: *"M6 counts as production semantic-materialization authority only if normal indexing can produce a correct M6 production semantic view WITHOUT requiring a completed old semantic DB as the source of semantic facts."*

| Clause | Evidence | Met? |
|--------|----------|------|
| "normal indexing" | `runner.run` → `_process_unit` per-TU hook (`_unit_processor.py:548-593`); default flags `M6_WRITE_AUTHORITY=1`, `M6_LEGACY_CONVERSION=0` | ✅ |
| "can produce a correct M6 production semantic view" | GREEN test: 14 facts incl. `public_func`, `helper`, `Point`, Call fact (`direct_materialization_green_test.json`); canary: 1756 facts across Function/Type/Variable/Call/Reference/Symbol kinds | ✅ |
| "WITHOUT requiring a completed old semantic DB" | `materialize_unit_into_m6` reads only `ExtractionResult` attrs (`m6_direct_materialization.py:90,179,208,234`); no `sqlite3.connect` to legacy DB; canary `legacy_db_exists=false, legacy_db_rows=0` | ✅ |
| "as the source of semantic facts" | Legacy converter (`index_legacy_db_into_m6`) is opt-in, lazily imported, never executed in normal mode (`runner.py:1155-1163`); kill test confirms old path produces 0 facts without legacy rows | ✅ |

**§5 IS SATISFIED.** The direct-materialization path is real, is wired into the normal indexing loop, is the default, reads from the libclang frontend (not the legacy DB), and is proven end-to-end by the GREEN unit test (14 facts) and the FreeRTOS production canary (1756 facts with no legacy DB).

---

## Final Notes

- The remediation is **architecturally sound**: direct materialization reuses the same proven `canon_*` canonicalization functions (imported from the M7.1 prototype, `m6_direct_materialization.py:39-42`) and writes to the same `BuildSetStore` (`add_fact` with BuildSet merge semantics, `m6_store.py:146-178`) that the legacy converter used — only the *source* changed (in-memory `ExtractionResult` instead of legacy DB rows). This is the correct shape for the fix.
- The RED/kill test (`test_old_materialization_kill.py`) correctly demonstrates that `index_legacy_db_into_m6` produces 0 facts from a non-existent / empty / symbol-less legacy DB (cases 1-3), confirming the old path is a post-hoc converter, not an independent materializer. The GREEN test proves the new path is independent. Together they form a proper anti-regression pair.
- The `register_world` call per-TU (`m6_direct_materialization.py:87`) is idempotent (`INSERT OR IGNORE`, `m6_store.py:277`), so calling it once per TU is safe.
- **Recommendation (non-blocking):** address H1 (silent fallbacks) before relying on M6 as the sole production authority in operational settings — an invisible total M6 failure is the most likely path by which the "authority" claim silently degrades in the field.

**VERDICT: APPROVED.** §5 hard definition is satisfied. Holes H1-H5 are documented for follow-up; none rise to the level of rejecting the remediation, because none cause normal indexing to depend on the legacy DB for semantic facts.
