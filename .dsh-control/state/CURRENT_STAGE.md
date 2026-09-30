# CURRENT_STAGE — fw-context M3B

> Cache, NOT truth. The scanner + hard gate are truth. Reconcile every round.

**Milestone:** `M3B_NATIVE_V2_READ_ENGINE_COMPLETE`

**Stage:** BOOTSTRAP_COMPLETE → entering autonomous Controller→Lead→Guardian→Verifier loop.

**As-of HEAD:** `ac76feb` (bootstrap; working tree dirty with controller scaffolding).

## Code-derived baseline (from `.dsh-control/evidence/authority_state.json`)

| Counter | Value |
|---|---|
| CORE_NORMAL_USE_SEMANTIC_TOOLS | 29 |
| CORE_V2_ROUTED | 29 |
| CORE_V2_NATIVE_READ | 5 |
| CORE_LEGACY_SEMANTIC_EXECUTION | 26 |
| CORE_UNSAFE_FALLBACK | 1 |
| UNRESOLVED_NORMAL_USE_V2_BUGS | 0 |

## M3B numeric gate status: **FAIL**

Failing clauses: C5 (native != core), C6 (legacy exec != 0), C7 (unsafe fallback != 0).

## Highest-leverage failing clauses (work order)

1. **C7 — CORE_UNSAFE_FALLBACK == 0**: the `v2_authority_route` wrapper returns
   `legacy_fn(...)` unguarded inside `except QueryViewResolutionError`
   (`authority.py:317`). Every CORE tool inherits this unsafe fallback. Fixing
   this once unblocks C7 for all 29 tools.
2. **C6/C5 — the 24 `V2_ROUTED_LEGACY_EXEC` adapters**: each must gain a real
   v2-native read path and drop its `raise V2UnsupportedOperation`. Plus the 2
   `PARTIAL` adapters (`lookup_symbol` prefix path, `find_all_callers_recursive`
   recursive path) must eliminate their deferral branches.

## Already-native (no work): find_callers, find_references, find_callees_recursive

## Partial (eliminate deferral): lookup_symbol, find_all_callers_recursive
