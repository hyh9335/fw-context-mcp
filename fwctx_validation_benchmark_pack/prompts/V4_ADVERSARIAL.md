# V4 — adversarial/metamorphic validation

Use copies of corpus only.

Required:
A1 G01 shared-header mutation -> affected contexts exact; incremental == clean full
A2 G02 add third compile action -> no context collapse
A3 G03 flip macro branch -> condition chain updates, inactive semantics disappear only from active view
A4 G04 A->B->C variant sequence -> no provenance leak/stale rows
A5 delete and re-add a TU -> no stale observation
A6 repeated no-op reindex x3 -> canonical snapshot stable
A7 G07 remove KEEP -> linker truth changes and fw-context follows appropriately
A8 G12 convert CRLF->LF -> raw hash changes, semantic active result stable
A9 G13 fanout 1/10/50/100/250 -> affected coverage exact and scaling recorded
A10 unsupported architecture/vector query -> explicit unsupported/partial, never fabricated answer

Public:
A11 NuttX or Zephyr disposable shared-header mutation -> incremental == full
A12 RIOT board/module switch -> regenerated compile DB does not retain stale prior-board facts

Output mutation ledger with before/after snapshots.
