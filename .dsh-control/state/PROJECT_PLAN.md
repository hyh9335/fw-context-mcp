# PROJECT_PLAN — fw-context M3B (mutable)

> Agents MAY change this file. Agents may NOT change
> `.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md`.

## Objective

Complete fw-context M3B Native v2 Read Engine: every CORE_NORMAL_USE_SEMANTIC
tool computes its semantic answer from v2 World-scoped facts, with
`CORE_LEGACY_SEMANTIC_EXECUTION == 0` and `CORE_UNSAFE_FALLBACK == 0`.

## Strategy

Drive the failing hard-gate clauses to green in leverage order, one bounded
Lead work package at a time, with fresh Guardian review at each gate and a
fresh Verifier when engineering clauses go green.

## Work packages (ordered by leverage)

### WP0 — Eliminate the shared unsafe fallback (C7)
- File: `src/fw_context_mcp/mcp/shared/authority.py`
- Site: `v2_authority_route` wrapper, `except QueryViewResolutionError` →
  `return legacy_fn(*args, **kwargs)` (line ~317).
- Fix: route this failure through `route_authority`'s equivalence-gated path,
  OR (M3B-correct) make v2 World resolution robust so this path returns a v2
  error rather than unguarded legacy. The M3B-end-state is that no CORE tool
  ever executes legacy semantics unguarded.
- Acceptance: scanner reports `CORE_UNSAFE_FALLBACK == 0`.

### WP1 — Convert the 24 `V2_ROUTED_LEGACY_EXEC` adapters to v2-native reads
Each adapter in `src/fw_context_mcp/mcp/shared/v2_shadow_adapters.py` whose body
is `raise V2UnsupportedOperation(...)` must instead:
1. resolve a pinned QueryView,
2. open the v2 read connection (`store_v2.query.open_query_connection`),
3. query the v2 World-scoped semantic facts for the tool's operation,
4. format to the legacy result shape,
5. drop the `raise V2UnsupportedOperation`.
Group by family (search / source / callgraph / inheritance / variables-conditionals)
so each Lead package is bounded and independently testable.

### WP2 — Eliminate the 2 PARTIAL deferral branches
- `lookup_symbol` prefix-search path (`exact=False`): implement v2 prefix read
  (or a v2-native equivalent) so the `raise V2UnsupportedOperation` is removed.
- `find_all_callers_recursive` recursive path: implement the recursion over v2
  edges natively so the `raise V2UnsupportedOperation` at line ~430 is removed.

### WP3 — Cross-cutting gates
- cross-family pinned session PASS
- multi-build ambiguity PASS
- historical no-drift PASS
- real production request-path PASS
- regression PASS (focused tests green)
- fresh verifier + fresh guardian accept the checkpointed HEAD

## Loop cadence

Controller round: scanner → reconcile status → fresh Guardian pre-stage →
pick highest-leverage failing clause → bounded Lead package → rerun scanner/
tests → when green: fresh Verifier → fresh Guardian completion → checkpoint
commit → final gate → complete Goal.
