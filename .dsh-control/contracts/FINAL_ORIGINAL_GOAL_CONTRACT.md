# FINAL ORIGINAL GOAL CONTRACT — fw-context multi-version semantic indexing

> **Immutable.** This contract freezes the ORIGINAL project Goal that the
> fw-context architecture-migration program (M3B → M4 → M5) was built to
> achieve. The final closure program independently verifies that this Goal
> is genuinely closed end-to-end in the final architecture.

## §1. The Original Goal

fw-context must support many source versions, commits, branches, builds,
and variants while safely deduplicating semantic indexing across versions.

Common semantic content should be indexed/stored once when reuse is proven
safe.

Each explicit RepositoryNamespace × Snapshot × Build must retain a
deterministic, historically correct semantic view.

Indexing a new version should approach work proportional to actual semantic
TU changes rather than reprocessing the entire repository.

Normal production operation should have one authoritative semantic system.

## §2. Non-Negotiable Principles

1. **False semantic reuse is unacceptable.** A SemanticState may be shared
   only when proven safe — same bytes do NOT imply same semantics.
2. **False non-reuse is acceptable.** Over-conservative reuse is tolerable;
   false reuse is not.
3. **Same file bytes do not imply same semantics.** Macro/config/include-chain
   differences can make identical source text produce different semantic facts.
4. **Explicit Build identity matters.** BuildIdentity (toolchain + flags +
   macros + environment) is part of the semantic identity, not just the source.
5. **Historical queries must not depend on live filesystem state.** A World's
   semantic view is immutable once finalized; modifying/deleting live source
   files must not change historical query results.
6. **No newest-wins semantic selection.** The "current" World is selected by
   deterministic policy, not by insertion order or recency.
7. **No hidden legacy semantic authority.** Normal production operation has
   exactly one semantic authority (v2). Legacy may exist only as an explicit,
   isolated, default-OFF compatibility island.
8. **Shared immutable state must survive as long as any reachable World
   requires it.** GC is reachability-based; a SemanticState referenced by any
   active/retained/pinned World must not be collected.

## §3. Final Verification Requirements

The final closure program must independently verify, from CURRENT code:

### 3.1 Semantic Identity
- TU-level semantic identity (L0..L3 digest) correctly distinguishes TUs that
  differ by source, macros, include-chain, toolchain, or environment.
- Same bytes + different Build context → different SemanticState (no false reuse).

### 3.2 Multi-Version Isolation
- Snapshot A, B, C each retain distinct, deterministic semantic views.
- A→B→A reconstructs the same semantic view for A on both passes.

### 3.3 Multi-Build Isolation
- Same Snapshot, Build X vs Build Y (different flags/macros) → distinct
  BuildIdentity → distinct Worlds → no cross-Build contamination.
- Ambiguous implicit World selection fails closed.

### 3.4 Reuse Safety
- Unchanged semantic TUs safely reuse immutable SemanticStates across versions.
- Changed semantic TUs do NOT incorrectly reuse.
- false_accept_count == 0 on adversarial attack cases.

### 3.5 Reuse Economics
- parse_fraction ≈ semantic_change_fraction (within a defensible gap).
- Indexing a new version approaches work proportional to actual semantic change.

### 3.6 Native Read Authority
- All normal-use semantic reads go through v2 (29/29 CORE tools v2-native).

### 3.7 Native Write/Finalization Authority
- v2 World finalization is the authoritative success criterion.
- v2 certification/publication/binding is the sole semantic write path in default mode.

### 3.8 Lifecycle Authority
- v2 owns World lifecycle states (unfinalized, active, superseded, retired,
  failed, retained).
- Failed/incomplete Worlds are not queryable.
- GC is reachability-based (roots = active + retained + pinned).

### 3.9 GC Safety
- Pinned Worlds cannot be collected.
- Current completed World cannot be collected.
- Shared SemanticState survives until ALL referencing Worlds are unreachable.
- GC is staged (ANALYZE → APPLY → VERIFY), crash-safe, idempotent.

### 3.10 Historical Immutability
- After indexing W1, modifying/deleting live source files does not change
  W1's query results. Restart preserves this.

### 3.11 Session Pinning
- A pinned session stays on its World across new index runs.
- GC does not collect a pinned World.

### 3.12 Legacy Independence
- Normal/default operation (compat OFF) is fully functional without legacy
  semantic DB, legacy postprocess, or legacy lifecycle.
- Legacy compatibility mode is default OFF, isolated, cannot affect v2 authority.

## §4. Final Machine Verdict

Allowed verdicts:
- `ORIGINAL_PROJECT_GOAL_COMPLETE`
- `ORIGINAL_PROJECT_GOAL_COMPLETE_WITH_MINOR_MAINTENANCE_GAPS`
- `ORIGINAL_PROJECT_GOAL_NOT_COMPLETE`

## §5. Stop Boundary

If the Goal is complete:
- STOP architecture migration.
- Transition to MAINTENANCE_MODE.
- Do NOT create M6/M7 or new architecture stages.
- Future issues classified as BUG / PERFORMANCE_REGRESSION / FEATURE_REQUEST /
  COMPATIBILITY / MAINTENANCE_CLEANUP.

## §6. Accepted Prior Milestones (Supporting Evidence Only)

- M3B `180da59` — native v2 read engine (29/29 CORE tools).
- M4 `10d1f36` — v2 write/finalization authority.
- M5 `cd24d21` — v2 lifecycle + legacy authority retirement.

These are supporting evidence. Current code and executable behavior are truth.
