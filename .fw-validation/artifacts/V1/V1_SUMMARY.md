# V1_GOLDEN Summary

- Phase: V1_GOLDEN (13 cases, G01–G13)
- Mode: VALIDATION_ONLY — no product sources modified; fixtures copied to disposable work dirs; `expected.json` never edited; product queried only through real entrypoints (`fw-context init/index` CLI + stdio MCP server).
- Product under test: fw-context-mcp 0.31.0 @ d8f08a41d3fb9df6de255a187720f6332ed0f1c9

## Verdict contribution

| Case | Family | Status | Finding |
|---|---|---|---|
| G01 multi_tu_header | per-context bodies | **PASS** | — |
| G02 same_source_multi_command | per-action macro values | **FAIL_PRODUCT** | F-V1-001 (macro values in DB, unobservable on every surface; expanded_value self-inconsistent) |
| G03 conditional_matrix | conditional chain | **FAIL_PRODUCT** | F-V1-002 (helper stored body empty; broadened by F-V1-007) |
| G04 variant_semantic | build variants | **FAIL_PRODUCT** (wsl) | F-V1-003 (shared compile DB, variant collapse, false `edges_equal=true`) |
| G05 cpp_override_template | C++ overrides/templates | **PASS** | — |
| G06 indirect_callbacks | function-pointer flows | **FAIL_PRODUCT** | F-V1-004 (registration-site flow unobservable; direct initializer flows OK) |
| G07 dead_keep | dead code vs linker truth | **FAIL_PRODUCT** | F-V1-005 (live chain misreported; no root set on compile_commands-only builds; WSL linker oracle confirms KEEP semantics) |
| G08 zephyr_async_stub | async registration negatives | **PASS** | no invented CALL edges; REGISTER evidence visible |
| G09 freertos_async_stub | spawn/queue negatives | **PASS** | no invented producer→consumer edges; queue object evidence on both ends |
| G10 cmsis_vector | vector table | **FAIL_PRODUCT** | F-V1-006 (slot 0 initial-SP unresolved; refs table holds the evidence) |
| G11 riscv_mtvec | RISC-V negative | **PASS** (wsl; Windows side BLOCKED_ENVIRONMENT — no clang for .S) | branch table not misread as pointer table; all 5 asm symbols queryable |
| G12 raw_crlf | raw byte-exact contract | **PASS** | sha256 + 6 CRLFs byte-exact; active/directives exact |
| G13 fanout_scale | scale + mutation invariant | **PASS** | invariant `incremental == clean_full` holds at 1/10/50/100/250; observations F-V1-007, F-V1-008 |

**Case tally: 6 PASS, 6 FAIL_PRODUCT, 1 PASS with Windows-side BLOCKED_ENVIRONMENT note.**

## Findings recorded (F-V1-001..008)

1. **F-V1-001** (G02) SILENT_DATA_LOSS — per-command macro VALUES stored in `macros` table but invisible on every query surface; `expanded_value` self-inconsistent (value=1, expanded=2).
2. **F-V1-002** (G03) SILENT_DATA_LOSS — conditionally-wrapped function stored body empty (`symbols.source=''`); trigger analysis broadened by F-V1-007.
3. **F-V1-003** (G04) INDEX_DIVERGENCE — bare generator writes ONE shared compile_commands.json (last variant wins); `compare_variants(A,B)` claims `edges_equal=true` for semantically differing variants; `get_source(variant=B)` served inactive body from disk.
4. **F-V1-004** (G06) SILENT_DATA_LOSS — transitive function-pointer registration flow (setup→register_cb(plus1)→param cb→registered) unobservable; direct initializer flows resolve.
5. **F-V1-005** (G07) WRONG_ANSWER — live entry chain (main/live_root/live_leaf) reported as not-live on compile_commands-only builds (manual backend never links; no config key for linker scripts); WSL oracle proves the linker keeps `kept_by_linker`.
6. **F-V1-006** (G10) SILENT_DATA_LOSS — vector-table slot 0 (initial SP, named data symbol) unresolved although the refs table holds the initializer reference.
7. **F-V1-007** (G13 + cross-case) SILENT_DATA_LOSS — **single-line function definitions (end_line == line) store empty bodies**; search_bodies blind to every one-liner; general root cause of F-V1-002's symptom (G01/G03/G12/G13 DB evidence).
8. **F-V1-008** (G13) SILENT_DATA_LOSS — query surfaces cap at 100 rows with no pagination; >100 same-name symbols cannot be enumerated (G13 n=250; `file_count==2n` remains the uncapped context evidence).

## Independent verifier

- Report: `artifacts/V1/verifier/verifier_report.md` (raw rerun evidence under `artifacts/V1/verifier/raw/<case>/`).
- Method: fresh work dirs (`%USERPROFILE%\fwctx-validation-work\V1-VERIFY\…`), own extraction code, real CLI + stdio MCP, one independently rerun case per semantic family (G01 contexts, G03 conditionals, G06 graph/callback, G10 vector, G12 raw) + full read-only audit of all artifacts and findings.
- Verdict: **PASS_WITH_NOTES** — every controller classification reproduced independently (F-V1-001 DB claim re-confirmed via sqlite; F-V1-003 product-source line claims verified at manual.py:188-189/237-243); G13 flagged PENDING at audit time.
- G13 closure after the audit: re-derived with daemon-disciplined requeries + exact-phrase value probes (v5). All checks pass at every size; consistent with the invariant the verifier observed. Two new findings (F-V1-007/008) postdate the verifier audit — their evidence is DB-level and reproducible; carried to V5 synthesis for release-notes review.
- Non-blocking verifier notes adopted: Windows-side raw G11 result relabeled BLOCKED_ENVIRONMENT in the ledger (wsl PASS is canonical); F-V1-005 wording nit (`main` status='dead' L1 rather than graph_unreachable — substance unchanged).

## Harness notes (controller-side, not product findings)

- G04/G11 ran on WSL Ubuntu-22.04 (Windows lacks gcc/clang binaries); G13's first-pass FAIL_PRODUCT was a harness artifact (runner key-order mismatch, default limit, OR-widened probe) — superseded by the v5 derivation with full provenance in `artifacts/V1/G13/`.
- Watcher daemon discipline: every index dir's daemon must be killed (`watcher.pid`) before work-tree mutation or directory removal; an alive daemon auto-reindexes stale indexes against mutated trees and contaminates evidence.
- `search_content` value probes must use exact phrases (`'"return 7"'`); bare two-word queries are OR-widened and match every file containing `return`.
