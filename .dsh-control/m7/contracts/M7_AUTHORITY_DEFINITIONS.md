# M7 Authority Definitions (frozen at HEAD 143642b)

These definitions are normative for the M7 program. They govern what
counts as "M6-native production authority" and what does NOT.

## 1. Production read authority

The engine that ACTUALLY answers production MCP semantic read requests in
default (env-unset, config-default) operation.

At HEAD 143642b: **v2** (the M3B/M4/M5 native engine backed by
`index-v2.db`).
- 29 of 32 core semantic tools wrapped in `v2_authority_route`
  (`server.py:1217-1329`).
- `v2_read_authority_enabled()` returns True by default (`authority.py:83`).
- `authority_mode()` returns `"v2_only"` (`authority.py:101`).
- v2 adapters read `store_v2/query.py` via a PinnedQueryView.

M7.2 migrates this to M6.

## 2. Production write authority

The engine that ACTUALLY owns production indexing success in default
operation.

At HEAD 143642b: **v2**.
- `v2_write_authority` defaults True (`settings.py:517`).
- v2 World finalization is the authoritative success criterion
  (`runner.py:945-991`); failed finalize raises `IndexIntegrityError`.
- Legacy `index.db` opened ONLY in compat mode (`_legacy_compat_on()`
  default False, `runner.py:501-524`).
- All legacy writes gated behind `if _legacy_compat:` (`runner.py:535,
  582, 602, 669, 721, 997, 1020, 1056`).

M7.3 migrates this to M6.

## 3. M6-native (true authority, anti-false-migration)

A tool counts as M6-native ONLY if:

```
production request
→ M6 shared-fact query implementation (m6_store / m6_schema / projection)
→ M6 result returned to caller
```

Does NOT count:
- new router → unsupported → old v2 execution;
- M6 validation → old v2 produces real result;
- M6 route label → legacy implementation;
- M6 module imported but never executed in the normal path.

Runtime counters and source scanners must prove ACTUAL execution.

## 4. M6-native indexing entrypoint

An indexing entrypoint counts as M6-native ONLY if:

```
entrypoint
→ shared-fact materialization/reuse (certificate/equivalence determination)
→ reuse existing canonical semantic facts where safe
→ materialize only changed/new facts
→ attach Build ownership
→ finalize exact Build semantic view
```

It is NOT sufficient to: fully materialize old whole-TU state, then
copy/dedup into M6. That is post-hoc deduplication, not reuse.

## 5. Fallback (normal production)

A code path taken when a primary path is unavailable. Five exist
(`fallback.py`, `shadow.py`, `v2_shadow_adapters.py`). M7 must drive
`UNSAFE_OR_SILENT_FALLBACK = 0` and `NORMAL_OLD_V2_FALLBACKS = 0` for
normal production mode after M7.5.

An explicit emergency rollback switch MAY remain during development, but
normal successful production execution must not silently fall back.

## 6. Legacy (compat island)

The pre-v2 engine backed by `index.db`. Default OFF
(`FW_CONTEXT_LEGACY_COMPATIBILITY_MODE=1` to enable). Isolated, explicit,
unable to affect normal v2/M6 success, selection, reuse, or lifecycle.
May remain after M7.5 as benchmark/rollback island (M5 §6: semantic
retirement, not physical deletion).

## 7. Shadow (M6 current state)

M6 modules exist and are populated during indexing but are NOT consulted
by production queries. Shadow status ends when M7.2 makes M6 the
production read authority.

## 8. Denominator stability

The frozen denominators (`denominator.json`) may not change during M7
without explicit classification. The final audit (§44 of the master
program) re-derives the denominator from final code and compares:
`BOOTSTRAP_DENOMINATOR` vs `FINAL_CODE_DENOMINATOR`. Any difference must
be explicitly classified. No disappearing tool may be hidden.
