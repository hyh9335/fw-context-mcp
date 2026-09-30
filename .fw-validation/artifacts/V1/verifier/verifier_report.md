# V1_GOLDEN Independent Verifier Report

- Verifier: independent subagent (session-0f41ec5a-33b7-4cf0-aff6-2e2df649686a child)
- Mode: VALIDATION_ONLY. Pack untouched; product sources untouched; expected.json untouched.
- Method: fresh work dirs under `%USERPROFILE%\fwctx-validation-work\V1-VERIFY\<case>\{work,index}`,
  binary fixture copy + pack's own `materialize_compile_commands.py`, real CLI entrypoint
  (`.fw-validation\venv-win\Scripts\python.exe -m fw_context_mcp.cli init --quick` / `index ... --no-analyze --no-embeddings`,
  `FW_CONTEXT_INDEX_DIR` isolated), queries over real stdio MCP (`fw_context_mcp.mcp.server`),
  independent extraction/compare logic written by the verifier (not the controller's recipes).
- Raw rerun evidence: `.fw-validation/artifacts/V1/verifier/raw/<case>/{setup.log,responses.json,facts.json,mcp_transcript.txt}`

## Full V1 record audit (read-only)

### Findings F-V1-001..F-V1-006

| ID | Classification | Evidence paths exist? | Repro consistent? | Classification justified? | Verifier note |
|---|---|---|---|---|---|
| F-V1-001 (G02, SILENT_DATA_LOSS) | macros stored but unobservable on every surface | YES — G02.result.json, G02/raw/responses.json, G02/raw/export.json, probe_g02_results.json, probe_g02b.py all exist | YES — result shows MODE_VALUE_macro_rows=[] & values_1_and_2_present=false while 2 contexts/2 lookup rows/defines_varying=1 hold | **AGREE** | Independently confirmed via read-only sqlite on the recorded index DB (`C:/Users/h00969597/fwctx-validation-work/V1/G02/index/8eb36fccfe8c46d0b87bb7c46b54999b/index.db`): macros has exactly 2 rows (value '1' ctx 1d405fa4c07e76fc, value '2' ctx 9655faf2257c623d, def_source=command_line) — and `expanded_value='2'` for BOTH rows, reproducing the recorded secondary self-inconsistency (value=1 must expand to 1). |
| F-V1-002 (G03, SILENT_DATA_LOSS) | helper empty stored body | YES — G03.result.json, G03/raw/responses.json, inspect_g03b.py | YES — **reproduced by verifier rerun**: search_bodies('helper')→[] while lookup_symbol→2 rows | **AGREE** | See G03 section. All other G03 cells (3 contexts, 10/20/30, condition texts, branch states) verified true. |
| F-V1-003 (G04, INDEX_DIVERGENCE) | bare generator writes ONE shared compile DB; variants collapsed | YES — G04/wsl/G04.result.json, G04/wsl/responses.json, peek_g04_cc.sh, wsl_g04.py | YES — result: transport_send grouped [A,B,C] single equivalence group; A_vs_B edges_equal=true, only_in_a/b empty | **AGREE** | Product-source line claims verified: `src/fw_context_mcp/indexer/builders/manual.py:188-189` writes one `cc_output_path(root)` with no variant dimension; `:237-243` `get_linker_scripts` returns [] (never links). WSL-only run correctly justified (Windows has no gcc/clang). |
| F-V1-004 (G06, SILENT_DATA_LOSS) | registration flow unobservable | YES — G06.result.json, G06/raw/responses.json, peek_g0506.py | YES — **reproduced by verifier rerun**: registered targets = [cb] only, never plus1 | **AGREE** | See G06 section. Positives (plus2/plus1 via initializers) and the opaque-param negative also verified. |
| F-V1-005 (G07, WRONG_ANSWER) | live chain misreported on compile_commands-only builds | YES — G07.result.json, G07/raw/responses.json, oracle_g07b.sh | YES — dead_rows include main/live_root/live_leaf; kept_by_linker not definite-dead; dead_a/dead_b SCC correct | **AGREE** | Minor wording imprecision: raw rows show `main` status="dead" (L1, "no references found") rather than "graph_unreachable" — the finding's substance (plainly live entry chain reported as not-live; no root set possible; manual.py:237-243 returns []) is correct either way. WSL-side oracle artifact (`~/fwctx-v1-work/G07/oracle/oracle_rigor.txt`) not re-verified from Windows (WSL path; oracle script exists locally). |
| F-V1-006 (G10, SILENT_DATA_LOSS) | slot 0 initial-SP unnamed | YES — G10.result.json, G10/raw/responses.json, inspect_g10.py | YES — **reproduced by verifier rerun**: slot 0 absent from get_vector_table; 8 handler slots exact; coverage discloses the gap | **AGREE** | See G10 section. |

All six findings: evidence paths exist on disk, reproduction matches the recorded symptom, classification justified. F-V1-002/004/006 symptoms independently reproduced by the verifier; F-V1-001's DB claim independently confirmed; F-V1-003's product-source line claims verified; F-V1-005's raw rows verified (one wording nit).

### Remaining cases (artifact consistency only)

- **G02** FAIL_PRODUCT — internally consistent (2 compile actions, no collapse, but macro values unobservable); matches F-V1-001.
- **G04** FAIL_PRODUCT (wsl) — internally consistent (single [A,B,C] equivalence group; A_vs_B edges_equal=true with no callee delta); matches F-V1-003. Note: `artifacts/V1/G04/G04.result.json` also holds an earlier Windows-side FAIL_PRODUCT (clang missing) superseded by the wsl run — ledger records the wsl result.
- **G05** PASS — all_match=true (Derived→Base override; Other::run negative; twice<int>/<double>).
- **G07** FAIL_PRODUCT — internally consistent; matches F-V1-005 (see wording nit above).
- **G08** PASS — register evidence via indirect ref from init_work; no invented direct CALL trigger→handler.
- **G09** PASS — spawn/register indirect evidence from setup; no producer→consumer call/possible edge; queue touched by both ends.
- **G11** PASS (wsl) — see dedicated section; Windows-side raw G11.result.json is FAIL_PRODUCT but its cause is the missing clang binary (`.S` preprocessing) — an environment block, superseded by the wsl PASS in ledger/state. Recommend relabeling that raw artifact BLOCKED_ENVIRONMENT for hygiene (non-blocking).
- **G12** PASS — verifier rerun reproduced.

### case_ledger.json + state.json

- Ledger: `planned_required_case_ids` includes G01–G13; case entries exist for G01–G12 with statuses/statuses matching `state.json → controller_progress.V1_GOLDEN` exactly (6 PASS, 6 FAIL_PRODUCT with finding refs). **No G13 case entry yet — consistent with G13 still running.**
- `state.json`: `phases.V1_GOLDEN = CONTROLLER_RUNNING` — CONFIRMED as required.

### G13 status: PENDING

- `G13.result.json` now exists (written while this audit ran) and raw artifacts for all 5 sizes × 4 stages (clean/pre/post_incremental/mutated_clean) exist, but **the background job is still writing** (run_console.log tail shows live progress of a newer pass; `_r2` response files appearing).
- The existing G13.result.json is NOT reliable as a FAIL_PRODUCT record: its own notes admit the comparison was "recomputed offline from saved responses" after a runner key-order bug; `shared_value_bodies=0` at every size (contradicting the product's demonstrated body retrieval in G01 — extraction artifact), `shared_value_rows` capped at 50 for sizes 100/250 (lookup_symbol default limit=50 — harness artifact), `index_elapsed_s=null`. The core invariant `incremental_equals_clean_full` reads **true at all 5 sizes**.
- Per instructions the verifier does NOT judge G13: **PENDING — controller must finish the in-flight run and re-derive the comparison from saved responses before gate close.**

### Oracle independence / canonicalization

- Verifier used its own extraction/compare code (`.fw-validation/artifacts/V1/verifier/verify_cases.py`), not the controller's recipes; comparisons run on raw MCP text (multi-object JSON via raw_decode), not through `canon.py`.
- `canon.py` inspection: strips only `elapsed_s/indexed_at/mtime/db/project_id/config_hash/duration_ms/timestamp` and absolute root prefixes — context identity, symbol identity, values, edges, conditions all preserved. Canonicalization cannot hide semantic differences.

### Environment deviations, non-blocking notes

1. Verifier ran on the same Windows host/venv (fw-context-mcp 0.31.0) as the controller; G04/G11 WSL results were audited from artifacts only (per instructions), not rerun.
2. G11 provenance: recorded wsl comparison dict does not match the current `v1_runner.py` revision (2 vs 3 checks; raw transcript contains a lookup the current code no longer issues) — an earlier runner revision produced it; decisive facts stand in raw data.
3. G11 Windows-side raw artifact labeled FAIL_PRODUCT where BLOCKED_ENVIRONMENT would be more precise (missing clang); ledger/state already carry the correct effective classification.
4. F-V1-005 wording: `main` is reported status="dead" (L1), not "graph_unreachable" — substance unaffected.
5. Controller work dirs for V1 live under `C:\Users\h00969597\fwctx-validation-work\V1\`; verifier used separate `...\V1-VERIFY\` dirs — no interference. Verifier index-dir daemons were killed via watcher.pid before any deletion.
6. Pack untouched throughout (fixtures only read; materialization ran on verifier's own copies).

---

## VERDICT

**VERDICT: V1_GOLDEN PASS_WITH_NOTES** — every controller classification that could be independently rerun (G01, G03, G06, G10, G12 across all 5 semantic families) reproduced exactly; all 3 FAIL_PRODUCT symptoms (G03 helper empty body, G06 registration-flow gap, G10 slot-0 data slot) and both PASS families reproduce; findings F-V1-001..006 all have existing evidence, consistent repros, and justified classifications. Notes are provenance/hygiene items (G13 pending with unreliable first-pass comparison; G11 runner-revision mismatch; two artifact-labeling nits) that change no classification.

Report path: `D:\github\fw-context-mcp\.fw-validation\artifacts\V1\verifier\verifier_report.md`


## Family: contexts — G01_multi_tu_header (rerun)

- Fresh work dir: `C:\Users\h00969597\fwctx-validation-work\V1-VERIFY\G01\{work,index}` (fixture binary-copied from pack, materialized with pack's own generator, indexed via venv CLI `init --quick` + `index <cc.json> --project <work> --no-analyze --no-embeddings`, `FW_CONTEXT_INDEX_DIR` isolated; raw evidence in `.fw-validation/artifacts/V1/verifier/raw/G01/`).
- Queries over real stdio MCP: `search_bodies{role_value}`, `search_bodies{feature_value}`, `lookup_symbol{role_value}`, `lookup_symbol{feature_value}`, `get_active_build{}`.
- Observed (independent extraction, raw_decode over concatenated JSON):
  - `role_value`: 2 ifdef-filtered bodies; one contains `return 11;` (ROLE_MASTER context), other `return 22;` (slave context); no body contains both (no context collapse); 2 lookup rows.
  - `feature_value`: 2 bodies with `return 100;` / `return 200;`, no collapse; 2 lookup rows.
  - `get_active_build`: status=ready, defines_varying=2.
- Expected (golden/README + expected.json): values 11/22 and 100/200 across 2 contexts, 2 TUs — MATCHES.
- Controller recorded: PASS. Verifier: **AGREE — PASS reproduced** (12/12 checks true, elapsed 30s).

## Family: conditionals — G03_conditional_matrix (rerun)

- Fresh work dir: `C:\Users\h00969597\fwctx-validation-work\V1-VERIFY\G03\{work,index}` (same pipeline as G01; raw evidence in `.fw-validation/artifacts/V1/verifier/raw/G03/`).
- Queries: `search_bodies{selected}`, `search_bodies{helper}`, `lookup_symbol{helper}`, `explain_condition{config.c:5}`, `get_active_build{}`.
- Observed:
  - `selected`: 3 per-context bodies with `return 10;` / `return 20;` / `return 30;` — values per context preserved, no collapse.
  - **`helper` symptom REPRODUCES**: `search_bodies("helper")` → `[]` (0 bodies) while `lookup_symbol("helper")` → 2 rows (both named `helper`, kind function). A function defined inside a TAKEN file-level `#if !defined(DISABLE_HELPER)` stores an EMPTY body — invisible to body search although the symbol exists in both contexts where it compiles. Expected.json says helper_present=true for contexts A and C → 2 helper bodies expected, 0 delivered.
  - `explain_condition(config.c:5)`: chain condition texts `defined(FEATURE_A) && LEVEL >= 2` and `defined(FEATURE_B) || LEVEL == 1` present; branch states `if taken=0, elif taken=1, else=-1` (context B evaluation, correct and context-specific).
  - `get_active_build`: defines_varying=4 (FEATURE_A/LEVEL/FEATURE_B/DISABLE_HELPER).
- Controller recorded: FAIL_PRODUCT (F-V1-002: empty stored body for conditionally-wrapped function; all other cells pass). Verifier: **AGREE — FAIL_PRODUCT justified; symptom reproduces exactly** (helper bodies 0 vs 2 expected; 6/7 of my checks true).
- Note: first rerun showed `selected_values_10_20_30=false` due to a verifier-script bug (space-stripped body vs unstripped needle); raw response always contained `return 10/20/30;`. Fixed in script; no product involvement.

## Family: graph/callback — G06_indirect_callbacks (rerun)

- Fresh work dir: `C:\Users\h00969597\fwctx-validation-work\V1-VERIFY\G06\{work,index}` (same pipeline; raw evidence in `.fw-validation/artifacts/V1/verifier/raw/G06/`).
- Queries: `find_indirect_targets{global_cb,fn,registered,cb}`, `find_indirect_call_sites{global_cb,fn,registered}`, `find_callers{plus1,plus2}`.
- Observed:
  - POSITIVE (both resolve): `find_indirect_targets("global_cb")` → `["plus2"]` (variable initializer); `find_indirect_targets("fn")` → `["plus1"]` (designated initializer `ops.fn`); call sites `call_global` / `call_ops` / `invoke_registered` each resolved; `plus1` reachable via indirect refs from `<file scope>` AND `setup`, `plus2` via `<file scope>`.
  - THE GAP (reproduces): `find_indirect_targets("registered")` returns exactly ONE row — `rhs_name="cb"`, `method=assignment`, `assign_line=13` (inside `register_cb`), call site `invoke_registered:14` — and NEVER `plus1`. The registration call argument `register_cb(plus1)` in `setup()` is visible as an indirect ref from `setup` (find_callers shows it) but is not joined through the parameter `cb` into `registered`'s may-target set. expected.json `may_targets.invoke_registered=["plus1"]` with registration-site evidence → unobservable.
  - Negative holds: opaque parameter `cb` is reported only as parameter evidence, never claimed to have a definite plus1/plus2 target.
- Controller recorded: FAIL_PRODUCT (F-V1-004; 8/9 cells pass, `invoke_registered_may_plus1=false`, single-row registered row identical to controller's). Verifier: **AGREE — FAIL_PRODUCT justified; symptom reproduces exactly**.

## Family: linker/vector — G10_cmsis_vector (rerun)

- Fresh work dir: `C:\Users\h00969597\fwctx-validation-work\V1-VERIFY\G10\{work,index}` (same pipeline; raw evidence in `.fw-validation/artifacts/V1/verifier/raw/G10/`).
- Query: `get_vector_table{}` (single call).
- Observed slot map: `1=Reset_Handler, 2=NMI_Handler, 3=HardFault_Handler, 11=SVC_Handler, 14=PendSV_Handler, 15=SysTick_Handler, 16=IRQ0_Handler, 17=IRQ1_Handler` — **exact** match to expected.json for all 8 handler slots; single table `vectors`; reserved zero slots 4-10,12,13 correctly left unnamed; coverage text explicitly discloses "No name in slots 0, 4-10, 12-13".
- The ONLY delta vs expected.json: slot 0 (initial SP, `(pFunc)&__initial_sp`) is unnamed; expected demands `INITIAL_SP_DATA`. Slot 0 absence is disclosed in coverage but the name is not recovered (F-V1-006: refs table holds the initializer ref startup.c:13, so the evidence exists in the index).
- Controller recorded: FAIL_PRODUCT with exactly this scope (8/8 handler slots exact; slot_map_exact=false only due to slot 0). Verifier: **AGREE — FAIL_PRODUCT justified per the oracle** (the mismatch is real, honestly disclosed, and the classification correctly records the product side rather than hiding it; index evidence makes it resolvable, supporting FAIL_PRODUCT over FAIL_ORACLE).

## Family: raw — G12_raw_crlf (rerun)

- Fresh work dir: `C:\Users\h00969597\fwctx-validation-work\V1-VERIFY\G12\{work,index}` (same pipeline; raw evidence in `.fw-validation/artifacts/V1/verifier/raw/G12/`).
- Queries: `read_file{source.c, mode=raw|active|directives}`.
- Observed:
  - `mode=raw`: sha256 of returned content = `c7c3fa2af6ede9e08ba27f377e874b133ab7951a953dd0c0be6bef277087b8cc` = expected; CRLF count = 6.
  - **Byte-exactness cross-checked against the true file**: SHA256 of the on-disk fixture (binary copy of the pack fixture, 117 bytes) = same hash, 6 CRLFs (PowerShell Get-FileHash + byte scan) — the tool's raw view equals the disk truth, not merely self-consistent.
  - `mode=active`: contains `active_line`, does NOT contain `inactive_line` (inactive `#else` branch blanked, line numbers stable).
  - `mode=directives`: exactly `["#define FLAG 1", "#if FLAG", "#else", "#endif"]`.
- Controller recorded: PASS (all 5 checks). Verifier: **AGREE — PASS reproduced**.

## Family: linker/vector — G11_riscv_mtvec (artifact audit only, WSL-only, no rerun per instructions)

- Artifacts present under `.fw-validation/artifacts/V1/G11/wsl/`: `init.log`, `index.log`, `mcp_transcript.txt`, `responses.json`, `G11.result.json` — all exist and are non-empty.
- Internal consistency of PASS against raw `responses.json`:
  - `get_vector_table{}` → single `info` row ("No references indexed…"), **zero pointer-table slots** → negative `no_pointer_table_interpretation` holds (branch table NOT decoded as Cortex-M pointer table).
  - All 5 asm symbols queryable: `irq1_handler`/`irq2_handler`/`irq3_handler` via `lookup_symbol{name:"irq", exact:false}` (lines 12/14/16 of vector.S), `trap_base` (line 4) and `exception_handler` (line 10) via exact lookups. The "PASS: all 5 asm symbols queryable" claim is supported by raw data.
  - `find_references` → "No references indexed" info (asm refs not stored) — disclosed honestly, not asserted by expected.json, consistent with the ledger note.
- Provenance note (non-blocking): the recorded `G11.result.json` comparison contains 2 checks (`no_pointer_table_interpretation`, `branch_handlers_indexed`), but the current `.fw-validation/v1_runner.py` `run_case_g11` computes 3 checks (`branch_refs_present` — which would be False: refs table is empty) and its query list omits the `lookup_symbol{exception_handler, exact:true}` call that IS present in the raw transcript. So the result was produced by an earlier runner revision; the recorded `branch_handlers_indexed=true` is not derivable from the current code against the recorded `irq_symbols` (only 3 names). The decisive facts (0 pointer slots, 5 symbols queryable) are in the raw data and support PASS regardless — classification unaffected, but the runner file no longer matches the G11 artifact it claims to have produced.





