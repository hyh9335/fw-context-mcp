# keep_only fixture (W4 / AUD-R4)

Minimal project whose linker script is **ENTRY + KEEP only** — no `MEMORY`
block, no symbol assignment.  This is the exact shape that produced the
AUD-R4 false positive: `find_dead_code` reported `keep_fn` as `dead` (L1)
although `linker.ld` line 13 (`KEEP(*(.keep_sec))`) is the linker's promise
that the section survives `--gc-sections`.

| file | role |
|---|---|
| `main.c` | `keep_fn` (zero refs, external linkage, `section(".keep_sec")`, no `used` attr), `dead_fn` (unprotected control), `ctor_fn` (constructor root), `entry_live_fn`/`main` (call + ENTRY roots). External linkage on keep_fn/dead_fn is the A07 rule: an unreferenced static may be elided by the COMPILER, and the binary oracle needs the LINKER to perform the removal |
| `linker.ld` | `ENTRY(main)` + `KEEP(*(.keep_sec))` — the O8 oracle lines |
| `compile_commands.json` | portable single-TU compile DB (relative paths; the test rewrites `directory`/`file` to the materialized copy) |

At index time an ENTRY + KEEP-only script registers **neither** a
`memory_regions` row **nor** an `ld:` symbol — pre-fix, the dead-code
handler's script discovery (which consulted exactly those two surfaces)
saw no script and dropped every `KEEP()` root.  The fix adds the `files`
table (where `store_scripts` upserts every backend-discovered script) as
the third discovery surface, and honors `/DISCARD/` honestly: a `KEEP()`
inside `/DISCARD/` is a removal promise, not a reachability root.

Root cause evidence: `.fw-closeout/artifacts/W0/R4_dead_code.log|json`.
Accepted design: `.fw-closeout/artifacts/arch/accepted_design_W4.md`.
Test module: `tests/test_dead_code_keep_roots.py`.
