# V4_ADVERSARIAL — Phase Summary

Status: SIGN_OFF (two independent verifiers: e56ff54d — 4 cases materially verified vs raw logs; f1d17599 — 6/6 checklist; MINOR gaps remediated post-audit: A4 mid-reindex logged in raw/reindex_delete.log, A2 re-run with materialize step, A3 helper-probe qualification noted, daemon AssertionError recorded as F-V4-001)
Product: fw-context-mcp 0.31.0 @ HEAD d8f08a41 — Windows (venv-win) leg
Spec source: workflow/STATE_MACHINE.md mutation classes (8 classes, A1–A8 labels are
controller shorthand for the classes listed at STATE_MACHINE.md:47-57, not pack ids)
Fixtures: small golden copies (G01_multi_tu_header, G03_conditional_matrix) — seconds-fast,
per design note; every case runs the REAL entrypoints (init → index → stdio MCP queries)
with `FW_CONTEXT_INDEX_DIR` isolation and watcher daemons killed before every mutation.

## Results — 8/8 PASS

| Class (STATE_MACHINE.md) | Case | Result | Oracle evidence |
|---|---|---|---|
| Shared header change | A1_shared_header_change | PASS | common.h 11→77; `get_symbol_context(role_value)` re-derives `return 77;` (pre: `return 11;`); symbol count stable 6→6; DB integrity ok |
| Macro / config change | A2_macro_config_change | PASS | ROLE_MASTER→ROLE_SLAVE flag flip: active bodies re-derive to slave branch (`return 22;`), master branch (11) gone from active text |
| Build variant switch | A3_build_variant_switch | PASS | TU a loses FEATURE_A in separate index dir: `selected()` re-derives 10→30; b keeps 20; helper untouched; original index untouched |
| Source deletion / addition | A4_source_delete_add | PASS | delete slave.c: 6→3 syms; add master2.c: re-derives `master2_entry` as exact row, **zero fallback rows**; DB integrity ok |
| Stale DB / no-op reindex | A5_stale_noop_reindex | PASS | unchanged-tree reindex: symbol count identical, DB intact |
| Repeated reindex ×3 | A6_reindex_x3_idempotence | PASS | 3 consecutive reindexes: constant symbol count [6,6,6,6], integrity ok after each |
| Partial / unsupported arch query | A7_unsupported_arch_query | PASS | `get_vector_table` on plain host fixture → honest empty (no fabricated table); `explain_condition` on unconditional file → empty chain; no isError |
| Malformed / missing optional env | A8_malformed_env | PASS | malformed CC → non-zero exit with error; missing CC → non-zero exit; no fabricated success, DB state recorded |

## Honesty observations (recorded, not classified as defects)

- **A7**: arch-specific tools on a non-MCU fixture return honest empties — no fabricated
  vector table (contrast: C1/F-V2-001 where a REAL CMSIS6 table was missed; both facts hold:
  honest-empty on no-table fixtures, missed-detection on real-table fixtures).
- **A4 mid-run episodes (runner-side, disclosed)**: three early runner attempts built a
  malformed second TU entry (mixed path separators left `-c` pointing at master.c). The
  product handled each honestly: `WARNING skip TU master2.c: Error parsing translation unit`,
  `1 skipped`, DB integrity ok, no partial rows. These episodes exercised the skip path and
  are preserved in the run logs of the final artifact set.
- **A6/A5**: repeated and no-op reindexes are idempotent on the small fixture — consistent
  with the N3 nuttx-scale no-op probe (delta=0).

## Notes for V5

- V4 exercises classes on 2-TU golden fixtures only; the nuttx/zephyr-scale mutation
  coverage lives in V2 (N1/N2/N3, Z2/Z4) and is cross-referenced there.
- F-V2-002 (exact=True silent fallback) did NOT re-appear on the clean A4 run
  (`added_rows=[master2_entry]`, no fallback) — but it reproduced in V2/Z2 and remains open.
- No daemon interference: kill_daemon before every index/mutation step (verified by stable counts).
