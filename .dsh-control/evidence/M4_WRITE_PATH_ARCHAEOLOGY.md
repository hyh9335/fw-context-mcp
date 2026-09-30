# M4 Write-Path Archaeology — fw-context

> Evidence file for the M4 milestone (`M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`).
> Read-only archaeology of the CURRENT production indexing write architecture.
> Every claim cites `file:line`. No code was modified.

**Scope.** This document reconstructs, end-to-end, how a normal production
fw-context indexing run writes semantic data today (HEAD at the time of
writing), and answers the M4 authority questions:
- WHO currently determines index success?
- WHEN does a v2 World become queryable?
- WHICH legacy failures propagate to abort indexing?
- WHICH postprocesses remain mandatory for success?
- WHERE does LEGACY-GATE affect v2?
- WHERE is legacy DB directly required for v2 indexing?

The repository is the fw-context MCP server itself (a Python project). The
fw-context MCP tools are NOT used here — they are for C/C++ targets, not this
repo's own Python code. All file references are relative to the repo root
`D:\github\fw-context-mcp`.

---

## Entry points

Normal production indexing reaches the write path through these entry points.
"SX" = Slice-X v2 hook involvement. "Calls `runner.run`?" = does the entry
reach the canonical `indexer/runner.py::run()` pipeline.

| # | Name | Location | Type | Calls `runner.run`? | v2 path? |
|---|---|---|---|---|---|
| 1 | `cmd_index` (single-build) | `src/fw_context_mcp/cli/_index.py:811` | CLI foreground | YES (`_index.py:982`) | YES (via `runner.run`, flag-gated) |
| 2 | `_run_multi` (multi-variant) | `src/fw_context_mcp/cli/_index.py:578` | CLI foreground | YES per build (`_index.py:670`) | YES (via `runner.run`, flag-gated) |
| 3 | MCP `reindex_file_impl` | `src/fw_context_mcp/mcp/handlers/maintenance.py:2002` | MCP per-file reindex | **NO** — uses `extract_all` + `store_symbols_for_unit` directly (`maintenance.py:1494`, `maintenance.py:1525`) | **NO** — no `certify_and_publish_for_unit`, no `world_ctx`, no `finalize_world` |
| 4 | MCP `reindex_file` (tool wrapper) | `src/fw_context_mcp/mcp/handlers/maintenance.py:2351` | MCP tool | delegates to `reindex_file_impl` (`maintenance.py:2379`) | same as #3 — **NO v2 path** |
| 5 | Daemon background reindex | `src/fw_context_mcp/mcp/daemon.py:376` (`_reindex_loop`) → `_run_index_async` (`daemon.py:611`) | Watcher/daemon | YES — spawns `fw-context index --background` subprocess (`daemon.py:671-677`) which enters `cmd_index` | YES (via `runner.run`, flag-gated) |
| 6 | `reindex_file` MCP path used by daemon? | n/a | — | The daemon does NOT call `reindex_file_impl`; it always re-spawns the full index subprocess. (`_watch.py:5` docstring claims the daemon calls `reindex_file_impl`, but the live daemon code at `daemon.py:432-435` calls `_run_index_async` only.) | — |

**Library `def run` / `def index` callers.** A grep for
`runner\.run\(|from .*runner import.*\brun\b` over `src/` returns only
`cli/_index.py` (lines 599, 830). The `search.py:444,589` and
`search/pipeline.py:240` matches are an unrelated search-pipeline `runner`,
not the indexer. So the **only** production caller of `indexer.runner.run` is
the CLI (`cmd_index` / `_run_multi`), reached directly by the foreground CLI
and indirectly by the daemon via the `--background` subprocess.

**Critical asymmetry.** Entry point #3/#4 (`reindex_file_impl`) is a normal
production indexing entry point that writes semantic data but bypasses
`runner.run` entirely. It therefore bypasses every v2 hook (certification,
SemanticState publication, TUBinding, World finalization). This is a
first-class M4 gap: a header re-indexed through `reindex_file_impl` mutates
the legacy DB while leaving the v2 World untouched, so the v2 World goes
silently stale and the M3B read authority (which already routes through v2)
will return stale results.

---

## The `runner.run()` pipeline

`src/fw_context_mcp/indexer/runner.py:271` defines `run(...)`. Its phases,
in execution order, with file:line anchors:

### Phase A — Setup (no writes yet)
1. **Project resolution.** Resolve `project_root`, `project_id`, `name`
   (`runner.py:349-362`). Collect git description (`runner.py:366`).
2. **Cleanup orphaned cc artifacts** (`runner.py:369`).
3. **Parse `compile_commands.json`** → split into C/C++ TUs vs assembly units
   (`runner.py:373-401`).
4. **Compute vendor patterns** from the parsed units' flags
   (`runner.py:410-419`).
5. **Manifest + config_hash.** `compute_structural_hash` → `load_manifest` or
   `build_preliminary` (`runner.py:447-473`). The `config_hash` returned at
   the end is the one decided here.
6. **Heartbeat thread** (optional, env-gated; `runner.py:480-493`).

### Phase B — Legacy DB bootstrap (LEGACY-GATE: opens legacy DB unconditionally)
7. **`open_db(db_path)`** (`runner.py:498`) — legacy `index.db` is opened
   whether or not v2 is enabled. This is a legacy-DB requirement on the v2
   index path (see LEGACY-GATE section).
8. **Read previous `manifest_verification`** (`runner.py:502-509`). New
   `config_hash` ⇒ `"indexing"` (hidden from MCP queries until complete).
9. **Serialize writers:** `with write_lock(...): with transaction(conn):`
   `upsert_project` + `upsert_build_config` (`runner.py:520-540`). The
   `manifest_verification` written here is `initial_manifest_verification`
   (`"indexing"` for a new config_hash).

### Phase C — Per-context identity + staleness pre-pass (legacy)
10. **Register build_contexts rows** (`runner.py:561-572`) — one row per
    compile action, ADR-001 identity.
11. **Read existing file hashes per (path, context)** (`runner.py:578-580`).
12. **Build manifest lookup** (`runner.py:589-610`).
13. **Header staleness pre-pass** (`runner.py:626-634`).
14. **Drop FTS5 triggers** for bulk load (`runner.py:643-654`).

### Phase D — v2 World context construction (flag-gated, exception-isolated)
15. **Build `world_ctx`** via `store_v2.worlds.build_world_context`
    (`runner.py:714-729`). Gated by `_v2_shadow_flag_on()`
    (`_unit_processor.py:69`). Any exception ⇒ `world_ctx = None` and the
    entire Slice-2 leg silently skips (`runner.py:726-729`).

### Phase E — Per-TU loop (`runner.py:741-906`)
For each unit:
16. **`raise_if_superseded`** (`runner.py:742`).
17. **Slice-4A fast-reuse admission** (`runner.py:763-802`, v2-gated). On a
    committed hit, calls `_handle_unchanged` (BL-1 minimum bookkeeping) and
    `continue`s — skips parse and legacy semantic writes. ONLY runs when
    `world_ctx is not None`.
18. **Staleness check + libclang parse** (no lock) — `_check_and_parse_unit`
    (`runner.py:809-822`). Status ∈ {`unchanged`, `skipped`, `updated`}.
19. **`unchanged`/`skipped` branches** (`runner.py:835-854`) —
    `_handle_unchanged` does legacy file-record + ifdef-content bookkeeping
    only; `continue`.
20. **`updated` branch — DB store inside `write_lock`** (`runner.py:857`):
    - `_process_unit(...)` (`runner.py:858-876`), which in turn:
      - **v2 certify + publish** (flag-gated, exception-isolated,
        `_unit_processor.py:448-459`) — runs BEFORE the legacy write lock.
        This is the v2 SemanticState publication + TUBinding construction
        site.
      - **legacy `store_symbols_for_unit`** inside `transaction(conn)`
        (`_unit_processor.py:479-494`) — the legacy semantic write.

### Phase F — v2 World finalization (flag-gated, exception-isolated, log-only)
21. **`finalize_world` + `record_run_metrics`** (`runner.py:911-937`). Opens
    a separate v2 store connection (`open_v2_store_for_write`). The
    `FinalizeResult` (`worlds.py:672`) is **logged but never checked** —
    `fin.ok` does not gate the return value. Any exception is swallowed at
    `runner.py:935-937` with `log.debug("v2 world finalize failed (legacy
    unaffected): %s", _f_exc)`. **This is the central M4 finding.**

### Phase G — Linker scripts + assembly (legacy)
22. **`_store_linker_scripts`** (`runner.py:942-953`) — between C units and
    assembly; uses legacy `write_lock` + `transaction`.
23. **Assembly store** (`runner.py:962-977`) — legacy `write_lock` +
    `transaction`, calls `indexer.asm.store_units`.

### Phase H — Legacy post-processing (LEGACY success authority)
24. **`_run_postprocess(...)`** (`runner.py:995-1032`) — the 18-step legacy
    pipeline (see Legacy postprocess inventory). This is what stamps
    `manifest_verification` to `'full'`/`'partial'` and stamps the
    `row_format`. **This is the legacy success authority.**

### Phase I — Return
25. **`return config_hash`** (`runner.py:1037`). Always returned unless an
    exception propagates (fatal DB error, `IndexSuperseded`, or an
    `IndexIntegrityError` raised by `_step_verify_integrity`).

**What makes `config_hash` non-None (success).** It is the literal string
computed at Phase A step 5. The function returns it on every non-exception
path. v2 finalization outcome has NO effect on the return value.

---

## Legacy write path

Where the legacy `index.db` is written, and what makes legacy "success":

### Per-TU writes (Phase E step 20)
- `indexer/ops.py:1549 store_symbols_for_unit(...)` — called inside
  `transaction(conn, checkpoint=False)` under `write_lock`
  (`_unit_processor.py:479-494`). Upserts symbols, refs, call_sites,
  inheritance, files, macros, conditionals, embeddings rows into legacy
  tables.
- `_handle_unchanged` file-record updates (`_unit_processor.py:624-669`) —
  mtime, content_hash, ifdef-filtered `files.content`.

### Per-run writes (Phase G-H)
- Linker script symbols (`runner.py:942-953` → `indexer/linker_script.store_scripts`).
- Assembly symbols (`runner.py:962-977` → `indexer.asm.store_units`).
- `_run_postprocess` 18-step pipeline (see below).

### What makes legacy "success"
The `manifest_verification` column on `build_configs` is the legacy success
signal. It transitions:
- **`'indexing'`** — written at Phase B step 9 for a new `config_hash`
  (`runner.py:506-507, 535`). Hidden from MCP queries (`get_active_build`
  treats this as "still indexing").
- **`'full'`** — written by `_step_finalize_manifest`
  (`_postprocess.py:1431-1476`) when `manifest.json` exists and no critical
  step failed.
- **`'partial'`** — same step, when `failed_critical` is non-empty
  (`_postprocess.py:1449-1450`). Still queryable, but features are degraded.
- **`'none'`** — no `manifest.json` written (`_postprocess.py:1451-1452`).

A run that reaches `_step_finalize_manifest` (which always runs, even on the
abort path — `_postprocess.py:1870-1878`) is "successful" from the legacy
perspective as long as `manifest_verification ∈ {'full', 'partial'}`. The CLI
exits 0 when `run()` returns `config_hash` without raising
(`_index.py:988-1016`).

---

## v2 write path

The v2 store (`<db_dir>/<project_id>/index-v2.db`, `store_v2/store.py:62
v2_db_path`) is a physically separate SQLite file. The write path is:

### Per-TU: certification + SemanticState publication + TUBinding
Entry: `store_v2/certify.py:1055 certify_and_publish_for_unit(...)`, called
from `_unit_processor.py:448-459` BEFORE the legacy write lock.

1. **Build certificate** (`certify.py:753 build_certificate`) — never raises;
   failures produce `status="poisoned"`. Captures L0 main source blob, L1
   NCC, c3 forced inputs, c4 closure (rehashed at cert grade), c5 macro
   environment (full `clang -dM -E`), class-C parser identity
   (`certify.py:977 _certify_parser_identity`), and computes
   `CertifiedSemanticInputs` + `TUKeyResult` (the TUSemanticKey).
2. **Record parser identity** (`publish.py:183 record_parser_identity`) —
   diagnostics-only registry row.
3. **Insert certificate row** (`publish.py:197 insert_certificate_row`) —
   writes `tu_certificates` row + Slice-4A directory attestations in one
   transaction.
4. **Extract facts** (`certify.py:1107 extract_state_facts`) — reads the
   existing `parsed` (libclang ExtractionResult); no second parse.
5. **Build cold payload + fact_set_digest** (`certify.py:1111-1112`).
6. **`publish_one_shot`** (`publish.py:268`) — ONE `BEGIN IMMEDIATE`
   transaction on the v2 store:
   - INSERT into `semantic_states` (`publication='published'`) +
     `semantic_symbols_core` + `semantic_edges` +
     `semantic_condition_index` + `state_file_table` +
     `semantic_state_positions` + `semantic_symbol_attrs` +
     `semantic_state_roots` + `slice1_state_cold`.
   - Convergence: same `completeness_sha` ⇒ no-op success.
   - W-1 tripwire: different digest ⇒ `refused_conflict` + integrity event,
     existing row untouched.
7. **Bind TU into World** (`worlds.py:475 bind_tu`) — if publication
   succeeded (`outcome in ('published_new','converged')`,
   `certify.py:1170`). Opens its own `BEGIN IMMEDIATE`:
   - `_ensure_world_rows` (`worlds.py:219`) — INSERT OR IGNORE into
     `repository_namespaces` / `snapshots` / `builds` /
     `snapshot_builds` (world_status=`'unfinalized'`).
   - Gate 1: target state must be `publication='published'`
     (`worlds.py:491`).
   - Gate 2 (A-2): path-spelling-sensitive state refuses cross-spelling
     bindings (`worlds.py:502-517`).
   - Gate 3: idempotence / conflict on `(world_id, tu_context_id)`
     (`worlds.py:519-538`).
   - INSERT `tu_bindings` + `tu_context_details` + `_insert_membership` +
     `_insert_cert_rows` + `_insert_branch_states`.
8. **Poison disposition** — if certification failed OR publication was
   refused (`certify.py:1101-1106, 1177-1178`):
   `worlds.py:587 record_poison` — INSERT `tu_bindings` with
   `binding_status='poisoned'` + `world_poisons` + `tu_private_payloads`
   (NO fake semantic-state key, INV-6).

### Per-run: World finalization
Entry: `runner.py:921 finalize_world(store, world_ctx)` +
`runner.py:922 record_run_metrics(store, world_ctx)`.

- `worlds.py:672 finalize_world` — `BEGIN IMMEDIATE`:
  - Verify world row exists and is `'unfinalized'`.
  - Read all `tu_bindings` for this `world_id`; count `bound` vs `poisoned`.
  - Compute `missing` = TU contexts in `ctx.processed_tu_contexts` not
    present in `tu_bindings`.
  - Verify `run_provenance` is non-empty.
  - **`if missing or not prov_ok:` return `FinalizeResult(False, ...,
    "unfinalized")`** — world STAYS `'unfinalized'`.
  - Otherwise: `UPDATE snapshot_builds SET world_status='active'`
    (`worlds.py:713-715`).
- `worlds.py:732 record_run_metrics` — writes `slice2_run_metrics` rows.

### What makes a World "finalized" / "current" / "queryable"
- **Finalized** = `snapshot_builds.world_status = 'active'`. Only
  `finalize_world` performs the `'unfinalized' → 'active'` transition
  (`worlds.py:713-715`), and only when `missing` is empty AND `prov_ok`.
- **Queryable / current** = `resolve_current_view`
  (`mcp/shared/queryview.py:248`) selects rows with
  `world_status='active'` (`queryview.py:346, 365`). An unfinalized world
  is invisible. Zero active worlds ⇒ `NoActiveWorld` raised
  (`queryview.py:385`).
- The v2 read authority (`mcp/shared/authority.py:234 v2_authority_route`)
  resolves a pinned QueryView for every participating tool call; a missing
  or stale v2 store raises and propagates (M3B behavior,
  `authority.py:307-339`).

### Where v2 facts come from
- The `parsed` libclang `ExtractionResult` is the shared input. `symbols.py`
  (legacy parse) has **zero** references to `store_v2`/`certify`/`publish`/
  `world`. v2 fact capture happens entirely in `store_v2/facts.py` (called
  from `certify.py:1107 extract_state_facts`), reading the same `parsed`
  object the legacy path used.

---

## Success authority

**WHO currently determines index success?** **Legacy.**

Code evidence:
- `runner.run` returns `config_hash` on every non-exception path
  (`runner.py:1037`). v2 finalization outcome is logged but not checked
  (`runner.py:929-932`). v2 finalization exceptions are swallowed
  (`runner.py:935-937`).
- `_step_finalize_manifest` (`_postprocess.py:1431`) is the only place
  that stamps `manifest_verification` to `'full'`/`'partial'`. This runs
  as the last legacy postprocess step. It runs even on the abort path
  (`_postprocess.py:1870-1878`), so the build always ends with an
  authoritative `manifest_verification` value (either `'full'`,
  `'partial'`, or stays `'indexing'` if finalize_manifest itself fails).
- CLI exit code 0 = `run()` returned `config_hash` without raising
  (`_index.py:988-1016`). The only things that make `run()` raise:
  - `IndexSuperseded` (another process took the index, `runner.py:145`).
  - Fatal `sqlite3.Error` (`_unit_processor.py:236-238, 510-512`).
  - `IndexIntegrityError` from `_step_verify_integrity`
    (`_postprocess.py:1423`) — a legacy self-consistency check; not gated
    on v2.
  - `is_fatal(exc)` on a `SAFE_EXCEPT` (`_postprocess.py:1844-1846`).

**WHEN does v2 World become visible/queryable?** Only when
`finalize_world` flips `world_status` from `'unfinalized'` to `'active'`
(`worlds.py:713-715`). The M3B read authority (`authority.py`) then
resolves it via `resolve_current_view` (`queryview.py:248`). An
unfinalized world is invisible to reads.

**WHICH legacy failures propagate to abort indexing?**
- `_step_verify_integrity` raises `IndexIntegrityError` ⇒ aborts
  (`_postprocess.py:1423, 1880-1881`).
- A `SAFE_EXCEPT` that `is_fatal` ⇒ re-raised (`_postprocess.py:1844-1846`).
- Fatal `sqlite3.Error` during parse/store ⇒ re-raised
  (`_unit_processor.py:236-238, 510-512`).
- Critical step failures (FTS5, embeddings) do NOT abort — they go into
  `failed_critical` (`_postprocess.py:1847-1849`) and the build is marked
  `'partial'` but still succeeds.

**WHICH postprocesses remain mandatory for success?** See the inventory
below. At present, the mandatory-for-success (i.e. their failure either
aborts or degrades `manifest_verification`) steps are: `fts5`,
`verify_integrity`, `finalize_manifest`. `finalize_manifest` always runs
(even on abort). `fts5` and `embeddings` are "critical" — failure
degrades to `'partial'` but does not abort.

**WHERE does LEGACY-GATE affect v2?** See the LEGACY-GATE section.

**WHERE is legacy DB directly required for v2 indexing?** `runner.run`
opens the legacy DB unconditionally at `runner.py:498 open_db(db_path)`.
The v2 hooks (certify, publish, bind, finalize) do not need the legacy
connection, but they run inside a process that has already opened it. The
v2 store is opened separately by `open_v2_store_for_write`
(`publish.py:145`). The legacy `db_path` is also passed into
`certify_and_publish_for_unit` (`_unit_processor.py:455`) only to derive
the v2 store path (`certify.py:1085-1086 v2_store_path(db_path)`); the
legacy connection itself is not used by the v2 path.

---

## Legacy postprocess inventory

The `_STEPS` list (`_postprocess.py:1704-1724`) drives the pipeline. Each
step runs in order; conditional steps are skipped when their guard returns
False. Runtime errors are classified:
- `SAFE_EXCEPT` + `is_fatal` ⇒ re-raised (aborts).
- `SAFE_EXCEPT` non-fatal + step in `critical_steps = {"fts5",
  "embeddings"}` ⇒ added to `failed_critical` (degrades to `'partial'`).
- `SAFE_EXCEPT` non-fatal + non-critical ⇒ logged at WARNING, continues.
- Any other `Exception` ⇒ `aborted = e`, break, then `finalize_manifest`
  runs on the abort path, then `raise aborted` (`_postprocess.py:1852-1881`).

| Step | File:line | Guard | V2_REQUIRED / LEGACY_COMPAT / UNKNOWN | Rationale |
|---|---|---|---|---|
| `purge_missing` | `_postprocess.py:573` | always | LEGACY_COMPAT | Removes legacy symbol rows for deleted source files. v2 has its own membership model (`snapshot_files`, `tu_bindings`); v2 does not read legacy `symbols` rows. |
| `fts5` | `_postprocess.py:657` | always (deferred under `--variants`) | LEGACY_COMPAT | Rebuilds legacy FTS5 indexes (`symbols_fts`, `files_fts`, `macros_fts`). v2 has its own `semantic_symbols_core` + FTS-free query path. Marked "critical" (`_postprocess.py:1825`) — failure degrades to `'partial'`. |
| `orphans` | `_postprocess.py:664` | always | LEGACY_COMPAT | Removes dangling legacy rows. v2 has no equivalent orphan concept (immutable states + bindings). |
| `is_project` | `_postprocess.py:676` | always | LEGACY_COMPAT | Sets `is_project` on legacy symbol rows. v2 derives project/vendor via namespace + canon path (`worlds.py:212 _is_project_rel`). |
| `manifest` | `_postprocess.py:730` | always | LEGACY_COMPAT | Writes `manifest.json` for legacy incremental staleness. v2 uses `snapshots` + `tu_certificates` for staleness. |
| `generated_flag` | `_postprocess.py:1652` | always | LEGACY_COMPAT | Reconciles `generated` flag on legacy manifest header records. v2 cert rehashes every closure member regardless (`certify.py:596 _closure_walk`). |
| `coverage_purge` | `_postprocess.py:809` | always | LEGACY_COMPAT | Purges legacy files outside the build. v2 membership is per-TU closure only. |
| `macros` (`_step_expand_macros`) | `_postprocess.py:1143` | `index_macros_expanded and units` | LEGACY_COMPAT (with v2 overlap) | Resolves `#define` expanded values via `clang -dM -E` and writes legacy `macros.expanded_value`. **v2 captures the SAME macro environment** at certification time (`certify.py:883-897 macro_environment_digest`) and stores it as the `macro_env_digest` leg of the SemanticState key. So v2 does NOT depend on this step's legacy writes; the step is legacy-compat only. The expanded `value` text is a legacy presentation field. |
| `dispatch_edges` | `_postprocess.py:915` | `index_refs` | LEGACY_COMPAT | Synthetic call-graph edges from event-loop registrations (`call_every`, `k_work_submit`, etc.) into legacy `call_sites`. v2 captures indirect edges at parse time via `semantic_edges` (`facts.py`); dispatch-bridge synthesis for v2 is a deferred post-index fact (noted in `facts.py:11-13`). |
| `llm_analysis` | `_postprocess.py:1195` | `analyze_symbols and llm_config.enabled` | LEGACY_COMPAT | Writes legacy `llm_analysis` rows. v2 stores no LLM analysis (it's a presentation feature; not part of SemanticState facts). |
| `embeddings` | `_postprocess.py:1163` | `index_embeddings and llm_config.enabled` | LEGACY_COMPAT | Writes legacy `embeddings` + `vec_symbols`. v2 has no embeddings layer. Marked "critical" — failure degrades to `'partial'`. |
| `overrides` | `_postprocess.py:1246` | `analyze_overrides` | LEGACY_COMPAT | Builds legacy `overrides` table from the inheritance graph. v2 has `semantic_edges` with override-class edges at parse time; cross-TU override graph for v2 is deferred (`facts.py:12-13`). |
| `cross_tu_refs` | `_postprocess.py:1261` | `index_refs` | LEGACY_COMPAT | Resolves legacy call_sites whose target was in another TU. v2 edges are per-state (already cross-TU at publication). |
| `variant_switches` | `_postprocess.py:1637` | `index_refs` | LEGACY_COMPAT | Legacy variant-switch edges. v2 has no equivalent. |
| `pagerank_hotspot` | `_postprocess.py:1279` | `index_refs` | LEGACY_COMPAT | Computes PageRank + hotspot cache over the legacy call graph. v2 has no pagerank. |
| `verify_integrity` | `_postprocess.py:1353` | always | LEGACY_COMPAT (but aborts!) | `PRAGMA foreign_key_check` + FTS-vs-content consistency over legacy DB. Raises `IndexIntegrityError` on failure ⇒ ABORTS the run (`_postprocess.py:1423, 1880-1881`). This is a legacy self-consistency check; v2 has its own W-1 tripwire + integrity_events. **Today this legacy check can abort a run that v2 considers complete.** |
| `finalize_manifest` | `_postprocess.py:1431` | always (runs even on abort) | LEGACY_COMPAT (but is the success stamper!) | Stamps `manifest_verification` to `'full'`/`'partial'`/`'none'` and `row_format`. **This is the legacy success authority.** v2 World finalization is a separate event with no effect on this column. |
| `cleanup_old` | `_postprocess.py:1559` | always (deferred under `--variants`) | LEGACY_COMPAT | Deletes older legacy builds. v2 has no GC (deferred to M5). |
| `wal_checkpoint` | `_postprocess.py:1585` | always | LEGACY_COMPAT | Flushes legacy WAL. v2 store has its own `PRAGMA synchronous=FULL` per write connection (`publish.py:155`). |

**Mandatory for success today:** `fts5`, `embeddings` (critical — failure
degrades but does not abort), `verify_integrity` (aborts on failure),
`finalize_manifest` (always runs, stamps the success column). All other
steps are non-fatal.

**For M4:** `verify_integrity` and `finalize_manifest` are the two legacy
steps that today gate the run's success signal. To make v2 the success
authority, v2 finalization (`finalize_world`) must become the success
criterion, and `verify_integrity` + `finalize_manifest` must become
LEGACY_COMPAT-only (their failure must not abort a v2-successful run).

---

## LEGACY-GATE clauses

Where legacy gates v2 today (i.e. legacy controls whether v2 writes happen
or whether v2 success is visible):

1. **Master flag gate — `_v2_shadow_flag_on()` (`_unit_processor.py:69-84`).**
   The flag (`FW_CONTEXT_SHADOW_V2=1` env or `[index] shadow_v2_enabled =
   true` config; default OFF) gates EVERY v2 write. With the flag OFF:
   - `world_ctx = None` (`runner.py:717-729`).
   - No Slice-4A admission (`runner.py:763`).
   - No per-TU `certify_and_publish_for_unit` (`_unit_processor.py:448`).
   - No `finalize_world` (`runner.py:911`).
   - **Production today runs with the flag OFF by default ⇒ zero v2
     writes on a normal run.** v2 is opt-in shadow only.

2. **Legacy DB open unconditionally (`runner.py:498 open_db(db_path)`).**
   The legacy DB is opened and written to before any v2 work. If
   `open_db` fails (e.g. corrupted legacy DB), `run()` raises before the
   per-TU loop, so v2 never gets a chance to certify. **A corrupt legacy
   DB blocks v2 indexing.**

3. **Legacy `upsert_build_config` before any TU work (`runner.py:520-540`).**
   The `manifest_verification='indexing'` row is stamped before the
   per-TU loop. If this write fails, `run()` raises (transaction rolls
   back); v2 never runs.

4. **Legacy `write_lock` around per-TU store (`runner.py:857`).** The v2
   hook runs BEFORE this lock (`_unit_processor.py:448-459`), so the lock
   itself does not gate v2 — but the surrounding `runner.run` control
   flow does: if `_process_unit` raises a fatal DB error inside the lock,
   `run()` aborts and subsequent TUs (and their v2 certifications) never
   run.

5. **Legacy postprocess aborts propagate (`_postprocess.py:1880-1881
   raise aborted`).** Any non-SAFE_EXCEPT exception from a postprocess
   step (most notably `IndexIntegrityError` from `verify_integrity`)
   aborts `run()` AFTER v2 finalization has already run. So v2 may have
   finalized a World successfully, and a legacy postprocess failure then
   raises and the CLI exits non-zero. **A legacy postprocess failure
   masks a successful v2 finalization.**

6. **`reindex_file_impl` bypasses v2 entirely (`maintenance.py:2002`).**
   This is the inverse LEGACY-GATE: a legacy-only write path that does
   not consult v2 at all. It writes legacy symbols via
   `store_symbols_for_unit` (`maintenance.py:1525`) and legacy macros,
   orphans, and manifest — but never calls `certify_and_publish_for_unit`
   or `finalize_world`. A header re-indexed this way leaves the v2 World
   stale. The M3B read authority (which already routes through v2) then
   returns stale results.

7. **v2 finalization outcome is not checked (`runner.py:929-937`).** This
   is the "LEGACY-GATE" in the success sense: even when v2 runs, its
   failure is swallowed and legacy determines success. `fin.ok` is
   logged, not enforced.

---

## Write-mode flags

The only write-mode control that exists today:

| Flag | Location | Default | Effect |
|---|---|---|---|
| `FW_CONTEXT_SHADOW_V2` (env) | `_unit_processor.py:74`, `certify.py:1029`, `mcp/shared/shadow.py:56` | unset (= OFF) | Gates ALL v2 writes (certify, publish, bind, finalize). `=1` enables v2 shadow writes alongside legacy. |
| `[index] shadow_v2_enabled` (config) | `config/settings.py:515` | `False` | Same as above, config form. |
| `[index] shadow_v2_db` (config) | `config/settings.py:516` | `None` (= M-C default `<db_dir>/<project_id>/index-v2.db`) | Override for the v2 store file path. |
| `FW_CONTEXT_V2_READ_AUTHORITY` (env) | `mcp/shared/authority.py:79` | unset (= ON since M3) | Gates v2 READ authority only. Does NOT affect writes. `=0` explicitly disables v2 read authority (forces legacy sole read). |

**No** `v2_write`, `v2_primary`, `dual_write`, `write_authority`,
`v2_authority` (for writes), or `v2_finalization_authority` flag exists.
There is no "V2_PRIMARY_LEGACY_MIRROR" mode flag — the M4 target mode does
not exist as a runtime switch today.

**Detected active write mode today:** `LEGACY_PRIMARY` (with optional v2
shadow writes when `FW_CONTEXT_SHADOW_V2=1`). v2 finalization never gates
success.

**Default write mode today:** `LEGACY_PRIMARY` (flag defaults OFF).

---

## Key findings (what must change for M4)

1. **v2 finalization is NOT the success authority (`runner.py:929-937`).**
   `finalize_world`'s `FinalizeResult.ok` is logged but never checked. A
   failed/incomplete v2 World does not fail the run; a successful v2 World
   does not gate the run's success. **M4 must make `fin.ok` (and the
   underlying `world_status='active'` transition) the authoritative
   success criterion.**

2. **v2 finalization failures are swallowed (`runner.py:935-937`).** The
   `except Exception` logs at DEBUG and continues. This is the
   `V2_FAILURE_MASKED_BY_LEGACY_PATHS` counter source. M4 must propagate
   v2 finalization failure to run failure (CASE C/D of the M4 contract).

3. **Legacy `finalize_manifest` stamps the success column
   (`_postprocess.py:1431`).** `manifest_verification` is the legacy
   success signal and is independent of v2 World status. M4 must either
   decouple `manifest_verification` from run success, or replace it with
   a v2-derived signal.

4. **Legacy `verify_integrity` can abort a v2-successful run
   (`_postprocess.py:1423, 1880-1881`).** A legacy self-consistency check
   raises `IndexIntegrityError` AFTER v2 finalization has run. M4 must
   make legacy integrity checks non-aborting when v2 is the authority
   (CASE B: v2 ok + legacy fail → INDEX SUCCESS +
   LEGACY_COMPATIBILITY_DEGRADED).

5. **`reindex_file_impl` bypasses v2 entirely (`maintenance.py:2002`).**
   This is a normal production indexing entry point that writes legacy
   semantic data without any v2 hook. M4 must either route
   `reindex_file_impl` through `runner.run`, or add a v2
   certify/publish/bind/finalize leg to it. Otherwise the v2 World goes
   stale on every per-file reindex, and the M3B read authority returns
   stale data.

6. **The v2 master flag defaults OFF (`_unit_processor.py:74`,
   `config/settings.py:515`).** Production today runs `LEGACY_PRIMARY`
   by default. M4 must make `V2_PRIMARY_LEGACY_MIRROR` the default (or
   remove the flag gate for the v2 path).

7. **Legacy DB is opened unconditionally (`runner.py:498`).** The v2 path
   does not need the legacy connection, but `run()` opens it before any
   TU work. A corrupt legacy DB blocks v2 indexing. M4 must ensure v2
   indexing can proceed (or fail gracefully) when the legacy DB is
   unavailable, per CASE B.

8. **`store_symbols_for_unit` is on the per-TU success path
   (`_unit_processor.py:479-494`).** Its failure (fatal `sqlite3.Error`)
   aborts `run()` and thus aborts v2 certifications of subsequent TUs.
   M4 must decide whether legacy mirror failure blocks v2 success (CASE
   B requires it NOT to block).

9. **v2 World becomes queryable only at `'active'` (`worlds.py:713-715`,
   `queryview.py:346`).** The World's own state machine is correct: an
   unfinalized world is invisible to reads. However, the scanner's
   `INCOMPLETE_WORLD_EXPOSURE_PATHS=1` reflects a different concern — the
   success PATH does not enforce finalization (v2 finalize is swallowed),
   so a run can "succeed" (return `config_hash`) while the v2 World was
   never finalized. M4 must enforce `fin.ok` on the success path (not
   change the World state machine).

10. **Per-TU expected-TU accounting is already enforced
    (`worlds.py:702-704`).** `finalize_world` computes `missing` = TUs in
    `ctx.processed_tu_contexts` not present in `tu_bindings`, and refuses
    to finalize when `missing` is non-empty. This is the
    EXPECTED_TU_ACCOUNTING check. M4 must preserve it.

11. **The daemon's background reindex does go through `runner.run`**
    (`daemon.py:671-677` spawns `fw-context index --background`). So fixing
    `runner.run` covers the daemon path. The daemon does NOT use
    `reindex_file_impl` for background reindex (despite the stale
    docstring at `_watch.py:5`).

---

## Counter candidates

Mapping the M4 scanner counters (per `IMMUTABLE_M4_GOAL_CONTRACT.md` §6 and
the executable scanner `.dsh-control/gates/scan_write_authority.py`) to the
code evidence above. Values reflect the CURRENT code, before any M4
engineering. The "Scanner value" column is the output of running
`python .dsh-control/gates/scan_write_authority.py` against HEAD `180da59`
(dirty) at the time of writing; the scanner derives counters from current
production code via AST analysis, so these are code-derived truth (the
contract's "current production code + the executable hard gate outrank
narration" rule).

| Counter | Evidence value | Scanner value | Evidence / mapping |
|---|---|---|---|
| `NORMAL_INDEX_ENTRYPOINTS` | 4 | **4** | (1) CLI `cmd_index` single-build (`_index.py:811`); (2) CLI `_run_multi` multi-variant (`_index.py:578`); (3) MCP `reindex_file_impl` (`maintenance.py:2002`); (4) MCP `reindex_file` tool wrapper (`maintenance.py:2351`) — the scanner counts the tool wrapper as a distinct entry point. (The daemon background reindex at `daemon.py:376` reaches `runner.run` via the `--background` subprocess, so it shares entry shape with (1); the scanner's `_enumerate_entry_points` (`scan_write_authority.py:111`) does not list it separately.) |
| `V2_PRIMARY_INDEX_ENTRYPOINTS` | **0** | **0** | No entry point gates run success on v2 finalization. `runner.run` returns `config_hash` regardless of `fin.ok` (`runner.py:929-937, 1037`). `reindex_file_impl` has no v2 path at all. Scanner: `v2_primary_count = normal_count if v2_authority else 0` (`scan_write_authority.py:492`). Target: 4 at M4 completion. |
| `LEGACY_PRIMARY_INDEX_ENTRYPOINTS` | **4** | **4** | Every entry point determines success via legacy `manifest_verification` (`_postprocess.py:1431`) or legacy `store_symbols_for_unit` success (`_unit_processor.py:479-494`). Scanner: `legacy_primary_count = normal_count - v2_primary_count` (`scan_write_authority.py:493`). Target: 0. |
| `LEGACY_WRITE_REQUIRED_FOR_SUCCESS` | **3** (heuristic) | **3** | Scanner heuristic: counts legacy DB write sites on the runner success path (`scan_write_authority.py:253 _find_legacy_db_writes` → `LEGACY_DB_WRITE_ATTRS = {"commit", "executemany", "execute"}` at `scan_write_authority.py:44`). These include `store_symbols_for_unit` (`_unit_processor.py:479-494`), `_store_linker_scripts` (`runner.py:942-953`), and assembly store (`runner.py:962-977`). Target: 0. |
| `LEGACY_POSTPROCESS_REQUIRED_FOR_SUCCESS` | **1** (call-site heuristic) | **1** | Scanner heuristic: counts `_run_postprocess` call sites in the runner (`scan_write_authority.py:45 LEGACY_POSTPROCESS_FN`, `scan_write_authority.py:480 legacy_postprocess_sites`). One call site at `runner.py:995`. Semantically the 18 steps inside `_run_postprocess` all run on the success path (see inventory); the scanner counts the call, not the steps. Target: 0. |
| `LEGACY_DB_REQUIRED_FOR_V2_INDEX` | **3** (same heuristic as above) | **3** | Scanner reuses the same `legacy_db_writes` heuristic (`scan_write_authority.py:484, 501`). Semantically: `runner.run:498 open_db(db_path)` opens legacy DB before any v2 work; v2 path does not use the legacy connection, but `run()` aborts if `open_db` fails. The heuristic counts write sites, not the single open. Target: 0. |
| `V2_FINALIZATION_IS_SUCCESS_AUTHORITY` | **false** | **False** | `runner.py:929-937` logs `fin` but never checks it. `run()` returns `config_hash` on every non-exception path. Scanner: `runner_analysis.get("v2_finalize_is_success_authority", False)` (`scan_write_authority.py:469`). Target: true. |
| `INCOMPLETE_WORLD_EXPOSURE_PATHS` | **1** (scanner heuristic) | **1** | Scanner heuristic: `1 if (v2_finalize_sites and v2_swallowed) else 0` (`scan_write_authority.py:488`). The v2 finalize call site EXISTS (flag-gated) but is exception-swallowed (`runner.py:935-937`), so the scanner counts a risk that an incomplete run is declared successful without finalization gating. **Semantic nuance:** the World's OWN state machine is correct — `finalize_world` only flips to `'active'` when `missing` is empty AND `prov_ok` (`worlds.py:709-715`), and `resolve_current_view` only selects `'active'` worlds (`queryview.py:346`), so an unfinalized World is genuinely invisible to reads. The counter measures the success-PATH enforcement gap, not a read-path exposure bug. Target: 0 (achieved by enforcing `fin.ok` on the success path, not by changing the World state machine). |
| `UNSAFE_PARTIAL_FINALIZATION_PATHS` | **0** (scanner placeholder) | **0** | Scanner hardcoded: `0,  # derived when finalize impl lands` (`scan_write_authority.py:504`). Code reading confirms: no path publishes a partial World as `'active'`; `finalize_world` is all-or-nothing per the `missing`/`prov_ok` gate. Preserve. |
| `EXPECTED_TU_ACCOUNTING_VIOLATIONS` | **0** (scanner placeholder) | **0** | Scanner hardcoded: `0,  # derived when accounting lands` (`scan_write_authority.py:505`). Code reading confirms: `finalize_world` enforces `missing = processed_tu_contexts − present_in_tu_bindings` (`worlds.py:702-704`). Preserve. |
| `V2_FAILURE_MASKED_BY_LEGACY_PATHS` | **1** | **1** | Scanner: `1 if (v2_finalize_sites and v2_swallowed and not v2_authority) else 0` (`scan_write_authority.py:476`). `runner.py:935-937` swallows v2 finalization exceptions. Target: 0. |
| `LEGACY_MIRROR_FAILURE_BLOCKS_V2_SUCCESS_PATHS` | **1** | **1** | Scanner: `1 if legacy_postprocess_required > 0 and not v2_authority else 0` (`scan_write_authority.py:507`). Today legacy postprocess failure aborts the run via `raise aborted` (`_postprocess.py:1880-1881`), most notably `IndexIntegrityError` from `verify_integrity` (`_postprocess.py:1423`). A v2-successful run is thus blocked from success by a legacy mirror failure — this is CASE B. (My earlier draft said 0 "inverse problem" — corrected: the scanner's framing is "does legacy mirror failure block v2 success?", and the answer is YES today.) Target: 0. |
| `CURRENT_WRITE_MODE` | `LEGACY_PRIMARY` | `LEGACY_PRIMARY` | Scanner: `current_mode = "V2_PRIMARY_LEGACY_MIRROR" if v2_write_authority_on else "LEGACY_PRIMARY"` (`scan_write_authority.py:333`). Flag defaults OFF (`config/settings.py:515`); v2 writes are opt-in shadow. Target: `V2_PRIMARY_LEGACY_MIRROR`. |
| `DEFAULT_WRITE_MODE` | `LEGACY_PRIMARY` | `LEGACY_PRIMARY` | Scanner hardcoded default: `"LEGACY_PRIMARY",  # code default absent explicit flag` (`scan_write_authority.py:336`). Target: `V2_PRIMARY_LEGACY_MIRROR`. |
| `ROLLBACK_MODE_AVAILABLE` | true (via flag) | (scanner-reported) | Setting `FW_CONTEXT_SHADOW_V2=0` (or unset) restores pure legacy. This is a "v2 off" mode, not a coherent "v2 primary with legacy mirror" rollback — M4 should preserve the flag as the rollback switch. |
| `M3B_READ_AUTHORITY_REGRESSION` | PASS | **PASS** | Scanner invokes the M3B read-authority scanner (`scan_write_authority.py:404 _m3b_regression` → `scan_authority_state.py`). Read authority remains intact. M4 must not regress this (C23). |
| `UNRESOLVED_NORMAL_USE_V2_WRITE_BUGS` | ≥1 | (scanner-reported, from bug register) | (a) `reindex_file_impl` bypasses v2 (`maintenance.py:2002`); (b) v2 finalize failure swallowed (`runner.py:935-937`); (c) v2 default-off (`config/settings.py:515`). Scanner reads `.dsh-control/state/` bug register (`scan_write_authority.py:437 _read_bug_register`). Target: 0. |

**Live scanner summary (HEAD 180da59, dirty):**
```
[m4-scan] NORMAL_ENTRYPOINTS=4 V2_PRIMARY=0 LEGACY_PRIMARY=4
[m4-scan] V2_FINALIZATION_IS_AUTHORITY=False V2_FAILURE_MASKED=1
[m4-scan] LEGACY_WRITE_REQ=3 LEGACY_POSTPROCESS_REQ=1 LEGACY_DB_REQ=3
[m4-scan] INCOMPLETE_EXPOSURE=1 WRITE_MODE=LEGACY_PRIMARY M3B_REGRESSION=PASS
[m4-scan] numeric_gate=FAIL m3b=PASS fault=FAIL machine_gate=FAIL
```

The scanner's `numeric_gate=FAIL` and `machine_gate=FAIL` confirm the M4
milestone is NOT complete at HEAD — consistent with every finding above.
The `fault=FAIL` is because the fault tests (`V2_SUCCESS_LEGACY_FAILURE`,
`V2_FAILURE_LEGACY_SUCCESS`, `CRASH_BEFORE_FINALIZE`,
`RESTART_IDEMPOTENCE`, `RETRY_IDEMPOTENCE`, `READ_AFTER_FINALIZE`,
`ROLLBACK`) are all `NOT_RUN` — they are to be implemented as part of M4
engineering.

---

## Appendix: call-graph of entry points → write layers

```
CLI: fw-context index [--background]
  └─ cmd_index (_index.py:811)
     ├─ _run_multi (_index.py:578)  [multi-variant]
     │  └─ run(...) per (variant, image)  → runner.run
     │  └─ rebuild_fts + cleanup_old_builds_multi  [legacy, end-of-run]
     └─ run(...)  [single-build]  → runner.run
        └─ (Phase D) build_world_context  [v2, flag-gated]
        └─ (Phase E per TU)
           ├─ attempt_fast_reuse  [v2 Slice-4A, flag-gated]
           ├─ _check_and_parse_unit  [libclang parse, no lock]
           ├─ certify_and_publish_for_unit  [v2, flag-gated, pre-lock]
           │  ├─ build_certificate  [v2]
           │  ├─ publish_one_shot  [v2 SemanticState]
           │  └─ bind_tu / record_poison  [v2 TUBinding]
           └─ store_symbols_for_unit  [LEGACY, in write_lock]
        └─ (Phase F) finalize_world + record_run_metrics  [v2, flag-gated, log-only]
        └─ (Phase G) linker scripts + assembly  [LEGACY]
        └─ (Phase H) _run_postprocess  [LEGACY, 18 steps]
           └─ _step_finalize_manifest  [LEGACY success stamper]

Daemon: file change → _reindex_loop
  └─ _run_index_async → subprocess `fw-context index --background`
     └─ (enters cmd_index above)

MCP: reindex_file tool → reindex_file_impl (maintenance.py:2002)
  └─ _reindex_parse_and_store (maintenance.py:1441)
     ├─ extract_all  [libclang parse]
     └─ store_symbols_for_unit  [LEGACY, in write_lock]  ← NO v2 HOOK
  └─ _update_manifest_after_reindex  [LEGACY manifest]
  └─ _reindex_post_write_phases  [LEGACY LLM/overrides/pagerank/embeddings]
```

---

End of evidence.
