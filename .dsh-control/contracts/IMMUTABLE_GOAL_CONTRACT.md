# IMMUTABLE GOAL CONTRACT — fw-context M3B Native v2 Read Engine

> **HUMAN-OWNED.** This contract is the single source of truth for the M3B
> milestone. Agents may change PLAN. Agents may NOT change this contract
> without human escalation. PROJECT_STATUS / ledgers are caches, NOT truth.
> Current production code + the executable hard gate outrank narration.

## 1. What M3B means

Every `CORE_NORMAL_USE_SEMANTIC` production tool computes its semantic answer
from **v2 World-scoped semantic facts/primitives**.

Routing through v2 is **insufficient**.

This path counts as **LEGACY SEMANTIC EXECUTION**:

```
QueryView -> v2 routing -> V2UnsupportedOperation -> legacy semantic execution
```

The production request path that M3B requires:

```
MCP request -> handler (v2_authority_route) -> resolve QueryView ->
v2 adapter reads v2 World-scoped facts -> format -> response
```

An adapter that raises `V2UnsupportedOperation` on its primary operation and
defers to `legacy_fn` is performing **legacy semantic execution**, regardless
of whether v2 resolved the World. World resolution is routing, not read.

## 2. Required machine counters (code-derived)

The scanner (`python .dsh-control/gates/scan_authority_state.py`) derives
these from CURRENT production code. They MUST NOT be sourced from
PROJECT_STATUS.md, AUTHORITY_LEDGER.md, or Lead reports.

| Counter | Meaning |
|---|---|
| `CORE_NORMAL_USE_SEMANTIC_TOOLS` | tools wired with `v2_authority_route(...)` in server.py |
| `CORE_V2_ROUTED` | of those, routed through v2 authority (all, by construction) |
| `CORE_V2_NATIVE_READ` | adapters whose body calls a v2 query primitive on a return path (real v2 read) |
| `CORE_LEGACY_SEMANTIC_EXECUTION` | adapters whose body contains `raise V2UnsupportedOperation` (a code path that executes legacy semantics) |
| `CORE_UNSAFE_FALLBACK` | `route_authority` fallback paths not gated by `_can_safely_fallback` (must be 0) |
| `UNRESOLVED_NORMAL_USE_V2_BUGS` | open bugs blocking a CORE tool's v2-native read (tracked, must be 0) |

Classification of each core adapter (mutually informative, not exclusive):

- `V2_NATIVE` — has a v2 read path, no deferral branch.
- `V2_ROUTED_LEGACY_EXEC` — raises `V2UnsupportedOperation`, no v2 read path (pure legacy exec).
- `PARTIAL_NATIVE_LEGACY_DEFER` — has BOTH a v2 read path AND a deferral branch (counts in BOTH `CORE_V2_NATIVE_READ` and `CORE_LEGACY_SEMANTIC_EXECUTION`).
- `LEGACY_SOLE` — not routed through v2 at all (must be 0 among CORE).
- `SPECIALIZED_OPTIONAL` — non-core, evidence-backed (get_vector_table, compare_variants, group_equivalent_variants).
- `OPERATIONAL_NON_SEMANTIC` — maintenance.* tools (10), by design not migrated.
- `UNKNOWN` — scanner could not classify; treated as failing.

## 3. M3B numeric gate (must ALL hold)

- `CORE_NORMAL_USE_SEMANTIC_TOOLS > 0`
- `CORE_V2_ROUTED == CORE_NORMAL_USE_SEMANTIC_TOOLS`
- `CORE_V2_NATIVE_READ == CORE_NORMAL_USE_SEMANTIC_TOOLS`
- `CORE_LEGACY_SEMANTIC_EXECUTION == 0`
- `CORE_UNSAFE_FALLBACK == 0`
- `UNRESOLVED_NORMAL_USE_V2_BUGS == 0`

## 4. Additional gates (must ALL PASS)

- cross-family pinned session PASS
- multi-build ambiguity PASS
- historical no-drift PASS
- real production request-path PASS
- regression PASS (mandatory focused tests green)
- fresh verifier accepted EXACTLY the current tested HEAD
- fresh guardian accepted EXACTLY the current tested HEAD
- durable checkpoint (git commit) exists for the accepted HEAD

## 5. Authority rules for agents

- Agents may change PLAN (`PROJECT_PLAN.md`).
- Agents may NOT change this contract without human escalation.
- No agent may weaken the immutable milestone `M3B_NATIVE_V2_READ_ENGINE_COMPLETE`.
- No agent may declare the project Goal complete unless the project-local
  executable hard gate (`run_stage_gate.py`) exits 0 against the current
  durable checkpoint.
- Same-view legacy fallback is safe compatibility but is **NOT** v2-native read.
- Any agent asked to review completion MUST reconstruct the denominator from
  current code, not from cached ledgers.
- Production code changes OUTSIDE the target Goal require normal engineering
  justification (recorded in `DECISION_LOG.md`).
- This block applies only within this Git project.

## 6. Current immutable milestone

`M3B_NATIVE_V2_READ_ENGINE_COMPLETE`

## 7. Goal text (for the persisted DSH Goal)

> Complete fw-context M3B Native v2 Read Engine according to
> .dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md.
> Run autonomous Controller -> Lead -> Fresh Guardian -> Fresh Verifier loops.
> Do not complete this Goal unless the repository-local executable hard gate
> passes against the current durable checkpoint.
