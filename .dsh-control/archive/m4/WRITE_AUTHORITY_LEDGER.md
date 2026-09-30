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
| Domain | Baseline | M4 target | M4 state (HEAD 9a45b2d) |
|---|---|---|---|
| identity | legacy | v2 | v2 (flag-on) |
| SemanticState pub | v2 (flag-gated, isolated) | v2 (authoritative) | v2 (flag-on, not yet default) |
| TUBinding | v2 (flag-gated) | v2 (authoritative) | v2 (flag-on) |
| World completion | v2 (isolated/swallowed) | v2 (authoritative) | v2 (raises IndexIntegrityError on fin.ok==False) ✓ |
| exit-status | legacy (config_hash) | v2 finalization | v2 (IndexIntegrityError propagates) ✓ |
| legacy mirror | n/a (legacy primary) | mirror (degraded-on-fail) | mirror (CASE B degraded) ✓ |
| postprocess | legacy (required) | split (V2_REQUIRED vs COMPAT) | CASE B degraded (raise aborted → log.warning) ✓ |
| rollback | n/a | legacy-primary coherent | FW_CONTEXT_V2_WRITE_AUTHORITY=0 → legacy-primary ✓ |
| reindex v2 leg | bypassed (BUG-002) | v2 certify+bind+finalize | Phase 2.5 added ✓ |

## M4 work packages status (HEAD 9a45b2d)
- M4-1 contract freeze: DONE (committed d0b90a0)
- M4-2 enforce fin.ok (CASE C/D): DONE (runner.py raises IndexIntegrityError)
- M4-3 migrate entry points: N/A (entry points already route through runner)
- M4-4 reindex v2 leg (BUG-002): DONE (maintenance.py Phase 2.5)
- M4-5 postprocess degradation (CASE B): DONE (_postprocess.py guard)
- M4-6 Legacy-GATE reassessment: PENDING (after G2)
- M4-7 flip flag default: PENDING (after G2 approval; resolves M4-BUG-003/C14)
- M4-8 fault-injection tests: DONE (7/7 PASS)
