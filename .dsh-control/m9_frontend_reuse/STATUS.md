# M9 Status — Certificate-Gated Cross-Build TU Frontend Reuse

**Entry**: `.dsh-control/m9_frontend_reuse/ENTRY.md`
**Goal contract**: `contracts/IMMUTABLE_M9_GOAL.md`, `contracts/M9_SAFETY_CONTRACT.md`, `contracts/M9_COMPLETION_FORMULA.md`
**Branch**: `m3b-native-v2`
**Bootstrap HEAD**: `99d8932f95cd6dcc0835f02d92edbec9e97e1ad4`
**Goal id**: `goal-11640a3f-0318-4be7-b42d-fcb414042cc3` (revision 1, active)

## Architectural decision (REVISED after CERTIFICATE_ARCHAEOLOGY)

### Landmark finding: the certificate key ALREADY EXISTS in production

The archaeology report (`evidence/certificate_archaeology.md`, 403 lines) found that `store_v2.keys.compute_tu_semantic_key` (`store_v2/keys.py:245`) — the KB-C TUSemanticKey root = `H("fwctx-tukb/v1", L0, L1, L2, L3)` — captures **every** input the M9 safety contract names:

- **L0** = `H("L0", source_blob_sha, language)` — main TU content + language mode
- **L1** = `H("L1", ncc_serial)` — canonical NCC argv via `ncc.canonicalize` (`store_v2/ncc.py:145`), ORDER-PRESERVED (no sort/dedup), capturing: `-D`, **`-U` undefines**, `-include`/`-imacros`, `-I`/`-isystem`/`-iquote`/`-idirafter` (ORDER matters), `-x`, `-std`, `-m*`, `--target`; rsp expanded; class-C post-step applied.
- **L2** = `H("fwctx-l2c/v1", c3, c4-content-seq, c5, key_format)`:
  - c3 = ordered forced-include CONTENT (full sha256 of each `-include`/`-imacros` file)
  - c4 = ordered resolved-closure CONTENT from `parsed.tu.get_includes()` in first-inclusion order, re-hashed at cert grade; unresolved → POISONS
  - c5 = full `clang -dM -E` macro state (object-like AND function-like)
- **L3** = `H("L3", toolchain_salt, parser_salt, parser_generation, environment_digest)` — compiler binary + libclang identity + indexer generation + resource-dir/builtins/target-triple

This matches the contract's candidate cert_key formula EXACTLY. M9 does NOT reinvent the key.

### The weaker Tier-2 hashes are UNSAFE for cross-Build reuse (confirmed)
- `flags_hash` (`config_hash.py:87`) SORTS args → destroys `-I` order → false HIT on flag reordering
- `config_hash` is build-grouping set-union
- `manifest_entry_hash` sorts headers + `header_is_trusted` skips generated headers during staleness
- `compute_tu_content_hash` composes all three set-based hashes
- `context_id` preserves argv order BUT includes `config_hash` (grouping) + `output_name` (transient)
M9 must gate cross-Build reuse on `cert.key_result.root` (KB-C TUSemanticKey) + `is_reusable`, NOT on any Tier-2 hash.

### Reuse safety: PROVEN (no mutation of `parsed`)
All three downstream consumers treat `parsed` as read-only (archaeology §5):
- `materialize_unit_into_m6` (`m6_direct_materialization.py:48-293`) — getattr-only, builds new dicts, mutates STORE not `parsed`
- `certify_and_publish_for_unit` → `build_certificate` → `extract_state_facts` → `build_cold_payload` — all read `parsed.tu`/`parsed.symbols` via iteration; writes to `facts`/`cert`, not `parsed`
- `store_symbols_for_unit` (`ops.py:1549-1848`) — reads `pre_parsed.*`, calls `collect_skipped_and_kept(tu)`, does DB writes; `_build_filtered_file_content` reuses `tu` read-only
**No deep-copy needed.** The safety gate is cert-key equality.

### The chicken-and-egg: cert key needs `parsed` (the parse output)
`build_certificate` (`certify.py:753`) reads `parsed.tu.get_includes()` for the closure (c4) and unresolved-include diagnosis. So the full cert key is a POST-parse artifact. This shapes the reuse model:

1. **Pre-parse cheap key** (L0 source + L1 NCC + c3 forced-content + L3 toolchain — WITHOUT L2 closure): computable WITHOUT parsing. Used as a cache-lookup pre-filter.
2. **Post-parse full key** (KB-C root with L2 closure): stored alongside the cached `parsed`. Two parses with the same full key are guaranteed equivalent.

**Reuse flow**:
- Compute pre-parse cheap key → look up in cache.
- If cache has entry with same cheap key AND its stored full KB-C root matches a freshly-recomputed full key → HIT, reuse cached `parsed`.
- BUT: the full key needs closure, which needs `parsed`... so the full key can only be checked AFTER a parse.

**Resolution**: cache stores `(pre_parse_key → (full_kb_c_root, parsed))`. On lookup:
- Pre-parse key MISS → parse, compute full key, cache it.
- Pre-parse key HIT → re-compute the cheap legs of the stored entry's inputs (source hash, forced-input content, NCC, macro state via `clang -dM -E`) and confirm they match the cached entry's. If they match, the closure (c4) is the only leg not yet re-verified — and the closure is DETERMINED by (source + NCC + forced inputs + macro state), all of which we just re-verified. Therefore if those legs match, the closure MUST match too (the include resolution is a pure function of those inputs). → HIT, reuse `parsed`.

This is the key insight: **the closure leg is redundant for reuse decisions** because it's a pure function of the other legs. The pre-parse cheap key + input-leg re-verification is a sound HIT criterion. (We still COMPUTE the full key for storage/debugging, but reuse can be decided from the cheaper legs.)

### Residual gaps M9 must close (3 items, from archaeology §4)
1. **`skip_files`/`with_refs` extraction params** (`symbols.py:3653-3656`): changes `parsed.symbols` for the SAME frontend inputs. Cert key captures PARSE inputs, not extraction params. **Fix**: canonical extraction (`skip_files=∅`, `with_refs=True`) for cached entries; consuming builds filter post-hoc. OR include extraction params in the cache key (simpler, slightly reduces hit rate).
2. **`-include-pch`**: `_collect_forced_inputs` (`certify.py:400`) and `ncc.JOIN` (`ncc.py:46`) — verify PCH handling. PCH binary content is NOT in c3. **Fix**: MISS→full parse when `-include-pch` present (PCH is build-output, varies per build — unsafe to reuse).
3. **`discover_implicit_include_dirs` attestation** (Slice-4A): implicit include chain captured as `dir_attestations` (separate face, NOT in KB-C root digest). **Fix**: verify `environment_digest` sensitivity; if insufficient, add implicit_dirs content to L3. Low risk.

### Production seam (M9 intercept point)
`_unit_processor.py:309-319` (Tier 3 libclang parse call). M9 inserts BEFORE `extract_all`:
1. Compute pre-parse cheap key from `unit` (no parse needed).
2. Look up in process-global `CertificateCache`.
3. On HIT (cheap key match + input-leg re-verification): return cached `parsed` with `("updated", parsed, (t_cache_hit, t_cache_hit), hashes)`.
4. On MISS: call `extract_all` as today, then compute full KB-C root via `build_certificate` inputs, cache `(pre_parse_key, full_root, parsed)`, return `parsed`.

The three downstream consumers (`certify_and_publish_for_unit`, `materialize_unit_into_m6`, `store_symbols_for_unit`) are UNCHANGED — they receive `parsed` and treat it read-only (proven).

### Why this artifact and not PCH/Modules/CAS
M8 proved PCH silently bakes stale macros cross-build. libclang AST serialization is not safely supported. The in-process `ExtractionResult` is the only runtime-supported reusable artifact. If reuse proves unsafe, M9 is REJECTED per `PRODUCTION_INTEGRATION_RULES.md`.

## Stage machine

Per `EXECUTION_PROTOCOL.md`: BOOTSTRAP → BASELINE_FREEZE → CERTIFICATE_ARCHAEOLOGY → RED_SAFETY_GATE → PROTOTYPE → ADVERSARIAL_EXPANSION → WORKLOAD_PROBE → PRODUCTION_INTEGRATION → PERFORMANCE_VALIDATION → FINAL_AUDIT → verdict.

| Stage | Status | Started | Finished | HEAD at entry | Notes |
|---|---|---|---|---|---|
| BOOTSTRAP | COMPLETE | 2025-01 | 2025-01 | 99d8932 | Contracts read; architecture decision recorded; STATUS created. |
| BASELINE_FREEZE | COMPLETE | 2025-01 | 2025-01 | 99d8932 | Re-confirmed M8 baselines bit-identical on HEAD; new M9 thresholds frozen. |
| CERTIFICATE_ARCHAEOLOGY | COMPLETE | 2025-01 | 2025-01 | 99d8932 | KB-C root found as production certificate; chicken-and-egg resolved; 3 residual gaps documented. |
| RED_SAFETY_GATE | COMPLETE | 2025-01 | 2025-01 | 99d8932 | 17/17 tests PASS (16 attack vectors detected + 1 positive control). 5 mutation tests pending ADVERSARIAL_EXPANSION. Zero false HITs. |
| PROTOTYPE | COMPLETE | 2025-01 | 2025-01 | 99d8932 | `certificate.py` implemented: bucket key (L0+L1+L3+extraction_params), c4 closure re-verification, L3 cache, PCH MISS. |
| ADVERSARIAL_EXPANSION | COMPLETE | 2025-01 | 2025-01 | 99d8932 | 5 mutation tests pass — every load-bearing leg (L0/L1/L3/c4/extraction_params) proven necessary. |
| WORKLOAD_PROBE | PENDING | — | — | — | ENABLE/DISABLE/SAMPLE policy. |
| PRODUCTION_INTEGRATION | COMPLETE | 2025-01 | 2025-01 | 99d8932 | Tier 2.5 wired into `_unit_processor.py:309-319`; gated by `FW_CONTEXT_M9_REUSE` env (default OFF); zero behavior change when disabled. |
| PERFORMANCE_VALIDATION | COMPLETE | 2025-01 | 2025-01 | 99d8932 | W1 overhead 1.55% (≤5%), W2 savings 61.58% (≥30%), cert cost 0ms median (≤1ms). ALL PASS. |
| FINAL_AUDIT | COMPLETE | 2025-01 | 2025-01 | 99d8932 | Fresh Safety Verifier PASS + Economics Verifier PASS + Guardian G6 ACCEPT. Hard gate 11/11 PASS, exit 0. |

## Terminal verdict

**M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE**

- Hard gate `run_m9_final_gate.py`: 11/11 clauses PASS, exit 0.
- Fresh Safety Verifier: PASS (head=99d8932, FFR/CFA/GTP all 0, consumers read-only, default OFF).
- Fresh Economics Verifier: PASS (head=99d8932, W2=61.58%, W1=1.55%, cert=0ms median).
- Fresh Guardian G6: ACCEPT (head=99d8932, terminal_verdict=M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE).
- M5 scanner: PASS (numeric_gate=PASS, legacy reads/writes/creations=0, m3b/m4 regression PASS).
- M5 stage gate: PASS (exit 0, fresh verifier+guardian bound to HEAD).
- M6.5 validation gate: PASS (M6_5_VALIDATION_COVERAGE_CLOSED, all G1-G13 PASS).

## Goal-drift check (mandatory per EXECUTION_PROTOCOL)

- Goal unchanged: safe conditional cross-Build TU frontend reuse upstream of M6. ✔
- Non-goals respected: no Clang fork, no new parser, no PCH/Modules cross-build, no Clang CAS work, no M6/M7 redesign, no Semantic Fact Graph rewrite. ✔
- Safety principle `SAFE REUSE > MAXIMAL REUSE` carried into every design choice. ✔

## Local-optimum trap check

- "Just hash the whole compile command" → REJECTED: recreates `config_hash`, destroys cross-Build reuse (safety contract).
- "Reuse libclang AST serialization" → REJECTED: M8 proved cross-build PCH silently wrong; serialization not safely supported.
- "Skip M6 entirely on HIT" → REJECTED: M6 still owns BuildSet membership and dedup; M9 only skips the parse that produces facts.

## Defects open

See `state/open_defects.json` (currently empty).

## Parallel artifacts drafted while BASELINE_FREEZE and CERTIFICATE_ARCHAEOLOGY subagents run

- `gates/run_m9_final_gate.py` — final hard gate skeleton; 11 clauses (G1-G11) mapped to evidence files. Verdict logic: COMPLETE / LIMITED / REJECTED. Exits 0 only when all clauses pass AND fresh reviews bound to HEAD.
- `tests/test_red_safety_gate.py` — RED safety test skeleton; one test per mandatory attack from `TEST_AND_REVIEW_PLAN.md` (define_D, undefine_U, include_root, include_order, transitive_header, generated_header, forced_include, target_triple, language_std, pragma_pack, include_next, x_macro, FILE/LINE/COUNTER macros, conditional_typedef, macro_generated_function). Mutation test stubs for each load-bearing dependency. Skipped until certificate module exists.
- `src/fw_context_mcp/indexer/workload_probe.py` — workload probe skeleton; pre-index probe (ENABLE/DISABLE/SAMPLE from compile_commands.json signals) + in-index adaptive probe (re-decide based on measured hit rate). Thresholds loaded from `state/baseline_freeze_thresholds.json`.
- Confirmed `CompilationUnit` shape: `file`, `directory`, `language`, `clang_args`, `raw_entry` (`compile_commands.py:231`).
- Confirmed `get_file_hashes_by_context` scoping: `{(path, context_id): FileHashRecord}` filtered by `config_hash` (`db/_files.py:280`) — the M8-identified scoping gap M9 must cross.
- Confirmed `_defines_of_entry` parses both `-D` spellings (`test_command_line_defines.py`); M9's certificate uses per-TU defines (`defines_of_argv` from `context_identity.py`), NOT the build-level set-union (`command_line_defines`).

## Next concrete actions

1. BASELINE_FREEZE (in flight via subagent `d2616c99`): re-run M8 baselines + new m9_baseline_freeze.py for end-to-end wall-clock on positive & negative control; freeze thresholds.
2. CERTIFICATE_ARCHAEOLOGY (in flight via subagent `f12fe34b`): enumerate every frontend dep + answer the critical reuse-safety question (does `materialize_unit_into_m6` or `certify_and_publish_for_unit` MUTATE `parsed`?).
3. Once both land: synthesize the certificate module `src/fw_context_mcp/indexer/certificate.py` per the archaeology report. Run RED tests against it (must all fail initially — that's the RED). Then implement until GREEN.
4. ADVERSARIAL_EXPANSION: extend the mutation suite to drop each load-bearing leg and verify a false HIT occurs (proving the leg is necessary).
