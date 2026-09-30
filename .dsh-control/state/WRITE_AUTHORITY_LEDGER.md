# WRITE_AUTHORITY_LEDGER — fw-context M4 (cache, NOT truth)

> Truth = code-derived scanner (`scan_write_authority.py`).
> This ledger tracks SEPARATE authorities. Never collapse into one word "write".

## Authority domains

### 1. Input/run identity authority
Who determines the Snapshot × Build identity of an indexing run.
**Baseline:** legacy — `indexer/git_context.py`, `indexer/context_identity.py`.
**M4 target:** v2 World identity (RepositoryNamespace + Snapshot + Build).

### 2. SemanticState publication authority
Who writes immutable SemanticStates (per-TU semantic facts).
**Baseline:** v2 shadow flag-gated (`store_v2/publish.py`), exception-isolated.
**M4 target:** v2 authoritative (publication failure ⇒ INDEX FAILURE).

### 3. TUBinding authority
Who binds a TU to its World.
**Baseline:** v2 flag-gated (`store_v2/worlds.py`).
**M4 target:** v2 authoritative.

### 4. World completion authority
Who decides a World is finalized/complete.
**Baseline:** v2 `finalize_world` flag-gated, exception-isolated (failure swallowed).
**M4 target:** v2 authoritative (finalization failure ⇒ INDEX FAILURE; incomplete
World never queryable).

### 5. Command exit-status authority
Who determines the indexing run's success/failure return.
**Baseline:** legacy — `runner.run` returns `config_hash` regardless of v2.
**M4 target:** v2 finalization gates the return.

### 6. Legacy mirror authority
Who controls the legacy compatibility mirror.
**Baseline:** legacy is primary, not a mirror.
**M4 target:** legacy is a compatibility mirror; its failure ⇒ degraded, not abort.

### 7. Postprocess authority
Who runs postprocessing steps (FTS, macros, embeddings).
**Baseline:** `_run_postprocess` on the success path, required for success.
**M4 target:** V2_REQUIRED steps gate success; LEGACY_COMPAT steps are mirror-only.

### 8. Rollback authority
Who provides a coherent legacy-primary rollback.
**Baseline:** legacy-primary is the default (rollback is trivially available).
**M4 target:** `FW_CONTEXT_V2_WRITE_AUTHORITY=0` restores legacy-primary.

## Status per domain (baseline → M4 target)
| Domain | Baseline | M4 target |
|---|---|---|
| identity | legacy | v2 |
| SemanticState pub | v2 (flag-gated, isolated) | v2 (authoritative) |
| TUBinding | v2 (flag-gated) | v2 (authoritative) |
| World completion | v2 (isolated/swallowed) | v2 (authoritative) |
| exit-status | legacy (config_hash) | v2 finalization |
| legacy mirror | n/a (legacy primary) | mirror (degraded-on-fail) |
| postprocess | legacy (required) | split (V2_REQUIRED vs COMPAT) |
| rollback | n/a | legacy-primary coherent |
