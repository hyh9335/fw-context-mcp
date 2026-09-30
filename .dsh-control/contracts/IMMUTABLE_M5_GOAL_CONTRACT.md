# IMMUTABLE M5 GOAL CONTRACT — fw-context

> **Milestone:** `M5_V2_LIFECYCLE_AND_LEGACY_AUTHORITY_RETIREMENT_COMPLETE`
>
> This contract is IMMUTABLE. No agent may weaken, shrink, or lower its
> clauses. Changing it requires human escalation. It supersedes the M4
> contract as the ACTIVE immutable milestone. The M3B and M4 contracts
> remain archived as historical accepted facts.

## §1. Goal

Make normal fw-context operation fully v2-native across semantic reads,
indexing/write/finalization, and semantic lifecycle.

Normal/default operation must NOT require, read, write, create,
post-process, or fall back to legacy semantic storage.

v2 must own World retention, pin protection, and safe reachability-based
garbage collection of shared immutable semantic state.

Legacy support may remain ONLY as an explicitly selected compatibility
island that has NO authority over normal v2 behavior.

## §2. Authoritative accepted baselines (historical facts)

- **M3B** `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` — accepted at `180da59`.
  All 29 CORE semantic reads are v2-native; legacy semantic read
  execution = 0.
- **M4** `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE` — accepted at
  `10d1f36`. v2 World finalization is the authoritative success criterion
  for normal indexing. Production default write authority = v2. Legacy
  remains a compatibility mirror.

These are frozen. M5 MUST NOT break M3B read authority or M4 write
authority. Both run as regression clauses of the M5 gate.

## §3. Normal default target

The final normal mode is conceptually `V2_ONLY_NORMAL_MODE`:

```
index
→ v2 identity
→ v2 certification
→ v2 SemanticState publication
→ v2 binding
→ v2 World finalization
→ v2 lifecycle
→ v2 query
```

No legacy semantic engine participates in normal operation.

## §4. Default-mode legacy definition

A legacy dependency exists if normal/default operation does ANY of:

- reads legacy semantic DB;
- writes legacy semantic DB;
- creates `index.db` for semantic reasons;
- invokes a legacy semantic postprocess;
- queries legacy for fallback;
- requires legacy completion;
- requires legacy data for v2 proof;
- requires legacy active-config identity;
- performs a legacy compatibility mirror;
- consults legacy lifecycle state.

Importing an isolated compatibility module that is NEVER executed in
normal mode is NOT by itself semantic authority. **Measure execution
paths, not file names.**

## §5. Explicit compatibility island

An explicit `LEGACY_COMPATIBILITY_MODE` MAY remain if evidence shows it
is useful. If retained, it MUST be:

- default OFF;
- explicitly selected;
- isolated;
- unable to affect normal v2 success;
- unable to affect v2 World selection;
- unable to constrain v2 reuse;
- unable to affect v2 lifecycle/GC;
- unable to become an implicit fallback.

Do not keep legacy code merely because deleting it is uncomfortable.
Do not delete compatibility code merely to improve a "legacy LOC removed"
metric.

## §6. M5 does NOT require physical deletion of every legacy module

The Goal is semantic and operational retirement. Physical deletion of an
explicit, isolated, default-OFF compatibility island is a FUTURE optional
task, NOT an M5 completion criterion (§41 stop boundary).

## §7. V2 lifecycle authority (M5-4)

v2 must own lifecycle semantics. At minimum distinguish:

- BUILDING World;
- COMPLETED World;
- FAILED / ABANDONED indexing attempt;
- RETAINED World;
- PINNED World;
- GC-ELIGIBLE World.

Do NOT infer semantic status from timestamps alone. Do NOT use
newest-created semantics. Add the minimum schema required only if
explicit lifecycle state cannot otherwise be represented safely.

## §8. GC root model

Garbage collection MUST be reachability-based. Root classes include:

- current completed World(s);
- explicitly retained Worlds;
- Worlds protected by active supported pinned QueryViews;
- any explicit historical retention policy.

Document exact root semantics. **No age-only semantic GC.**

## §9. Session pin protection

A supported active PinnedQueryView MUST prevent its referenced World from
being collected. After pin release/expiry, the World may become eligible
if no other roots exist. Do not invent hidden permanent pins.

## §10. Shared SemanticState GC

World deletion must NOT imply immediate state deletion. If W1→State X and
W2→State X, GC of W1 MUST leave State X intact. State X becomes eligible
only after ALL referencing Worlds are unreachable. Same for shared blobs,
position data, certificates, derived immutable content.

Implement mark/reachability or an equivalently safe method.

## §11. GC phases

A safe staged interface:

- **ANALYZE / DRY RUN** — compute candidate Worlds/bindings/States/blobs,
  reclaimed bytes estimate, protection roots. No deletion.
- **APPLY** — transactional deletion per approved reachability set.
- **VERIFY** — integrity checks after mutation.

Default operational policy may prefer explicit/manual GC over aggressive
automatic GC. **Correctness before automation.**

## §12. GC crash safety

Test interruption at: after candidate calculation, during World deletion,
before shared-state sweep, during shared-state cleanup. After restart the
database MUST remain semantically valid. GC should be retryable/idempotent.
A crash must NOT make a still-rooted World unusable.

## §13. Failed / partial Worlds

M4 guarantees incomplete Worlds are not normal query targets. M5 must
define lifecycle handling for them (immediately cleanup-eligible, retained
for diagnostics, or explicitly FAILED). Do not let abandoned partial data
grow without bound. Do not accidentally delete immutable states shared
with a completed World.

## §14. Historical query retention

GC must NOT silently destroy history users reasonably expect to retain.
Define a retention contract. If none exists, choose a conservative default
and document it. Do not create unlimited retention solely to avoid
designing lifecycle. Do not aggressively delete history solely to save disk.

## §15. Default GC policy

Separate GC correctness from automatic GC scheduling. M5 requires a
CORRECT lifecycle/GC mechanism. It does NOT necessarily require aggressive
automatic collection on every run. Choose a conservative production policy
if automatic timing is uncertain. The machine gate tests semantics, not
eagerness.

## §16. M5 final machine gate — required counters (all must hold)

```
NORMAL_MODE_LEGACY_DB_READS == 0
NORMAL_MODE_LEGACY_DB_WRITES == 0
NORMAL_MODE_LEGACY_DB_CREATIONS == 0
NORMAL_MODE_LEGACY_POSTPROCESS_CALLS == 0
NORMAL_MODE_LEGACY_FALLBACKS == 0
NORMAL_MODE_LEGACY_MIRROR_RUNS == 0
NORMAL_MODE_LEGACY_ACTIVE_CONFIG_DEPENDENCIES == 0
NORMAL_MODE_LEGACY_LIFECYCLE_DEPENDENCIES == 0
INCOMPLETE_WORLDS_VISIBLE == 0
PINNED_WORLD_GC_VIOLATIONS == 0
CURRENT_WORLD_GC_VIOLATIONS == 0
SHARED_STATE_REFERENCE_VIOLATIONS == 0
GC_REACHABILITY_ERRORS == 0
PRODUCTION_DEFAULT_TEST_LANE == PASS
CLEAN_PROJECT_V2_ONLY == PASS
LEGACY_UNAVAILABLE_NORMAL_MODE == PASS
UPGRADE_EXISTING_PROJECT == PASS
GC_DRY_RUN == PASS
GC_APPLY == PASS
GC_RESTART_INTEGRITY == PASS
M3B_REGRESSION == PASS
M4_REGRESSION == PASS
UNRESOLVED_NORMAL_USE_V2_BUGS == 0
```

PLUS:

- Fresh Verifier accepted current checkpoint HEAD.
- Fresh Guardian G6 accepted current checkpoint HEAD.
- Durable checkpoint exists.

## §17. Self-tests (adversarial)

S1 forged status, S2 hidden mirror, S3 hidden fallback, S4 legacy
postprocess, S5 shared-state premature deletion, S6 pinned World
deletion, S7 current World deletion, S8 stale verifier/guardian. Each
must be detected and FAIL the gate.

## §18. Production-default test lane

A dedicated lane with shipped defaults (NO fixture forcing
`FW_CONTEXT_V2_WRITE_AUTHORITY=0`). At minimum: fresh index → query →
restart → query → reindex → historical query → lifecycle operation → GC
dry-run. The M5 final gate MUST include this lane.

## §19. M3B / M4 regression

At every major M5 checkpoint, M3B + M4 gates (or equivalent frozen
regression suites) must pass. M5 cannot complete by breaking prior
authority guarantees.

## §20. Stop boundary (§41)

When M5 completes: STOP. Do NOT automatically begin physical deletion of
every legacy compatibility module. Do NOT start a new semantic capability
program. Do NOT add new features. A future optional task may decide
whether the explicit compatibility island should be physically deleted.

## §21. Guardian gates

G0 startup, G1 legacy archaeology complete, G2 default mirror disabled,
G3 migration scaffolding cleanup, G4 lifecycle/GC design, G5 GC
implementation candidate, G6 final completion candidate. ACCEPT_COMPLETION
is FORBIDDEN while the machine gate fails.

## §22. Fresh Verifier attack plan

The final verifier MUST independently attempt: remove legacy DB, make
legacy DB path unwritable, force legacy helper failure, inspect whether
normal indexing creates legacy storage, search for runtime legacy semantic
calls, query all major normal semantic families, run restart, reindex,
historical World query, pin old World and GC, shared-state two-World GC,
interrupted GC, clean-project bootstrap, upgrade from pre-M5 storage,
verify M3B and M4 invariants.

## §23. No weakening

No agent may weaken this contract. No agent may declare the Goal complete
unless the executable hard gate exits 0 against the current durable
checkpoint AND a fresh Verifier + fresh Guardian G6 accepted that exact
HEAD. Ledgers are CACHES, not truth — the code-derived scanner and the
executable hard gate are truth.
