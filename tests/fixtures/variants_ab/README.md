# tests/fixtures/variants_ab

W6 fixture source (AUD-R5 / accepted design `arch/accepted_design_W6.md`).
One translation unit, three build variants of ONE project (same `project_id`,
distinct `variant` label and `config_hash` per build):

| Variant | Compile args      | `transport_send` behavior |
|---------|-------------------|---------------------------|
| A       | *(none)*          | calls `send_uart()`       |
| B       | `-DIMPL_B=1`      | calls `send_usb()`        |
| C       | `-DUNRELATED=123` | calls `send_uart()` (same as A) |

`C` differs from `A` only by a define that the unit's conditionals never
reference — the T14 SLICE-E shape — so the behavior fingerprint of
`transport_send` must be equal for A/C and different for A/B, while the
`#ifdef IMPL_B` branch states differ only between A and B.

The test copies this file into a throwaway project root (never indexed in
place), writes a `config.toml` + `compile_commands.json` beside it, and runs
the in-process indexer (`fw_context_mcp.indexer.runner.run`), the same way
`test_variant_provenance_slice_e.py` does.
