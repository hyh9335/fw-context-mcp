# IMMUTABLE M7 MASTER GOAL CONTRACT — fw-context

> **Program:** `M7_SHARED_FACT_GRAPH_PRODUCTION_AUTHORITY` + `ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSURE`
>
> This contract is IMMUTABLE. No agent may weaken, shrink, or lower its
> clauses. Changing it requires human escalation. It supersedes the M5
> contract as the ACTIVE immutable milestone for the M7 program. The M3B,
> M4, M5, M6, M6.5 contracts remain archived as historical accepted facts.

## §1. The ORIGINAL immutable product Goal (the real Goal)

The Goal is NOT merely "route MCP tools through a new store."

The Goal is:

> fw-context must index and query many source versions, commits, branches
> and Build/configuration variants while safely reusing semantic work and
> storage wherever semantics are actually identical.
>
> Every explicit Snapshot × Build must reconstruct its exact semantic view.
>
> Calls, variables, references, types, conditional compilation,
> source/body and historical queries must remain correct.
>
> Different Builds must never leak semantic facts into each other.
>
> Identical semantic structure should be represented/materialized once
> whenever safely provable.
>
> Incremental indexing/storage cost should increasingly scale with actual
> semantic changes instead of total version/Build count.
>
> SAFE REUSE is always more important than maximal reuse.

This is the Goal M7 exists to close. M7 production migration is the means;
the original multi-version reuse Goal is the end.

## §2. Authority of truth (priority order, highest first)

```
1. Immutable Goal Contracts (this file + frozen M5/M6 contracts)
2. Executable Hard Gates
3. Current production code/runtime behavior
4. Independent Ground Truth / compiler oracle evidence
5. Fresh Verifier evidence
6. Fresh Guardian decisions
7. Machine-readable state/evidence
8. Human-readable status reports
9. Lead narration
```

A markdown status file can NEVER override code/runtime evidence.
A Lead claim can NEVER override a failing gate.

## §3. Accepted baseline (frozen at HEAD 143642b)

These milestones are COMPLETE and archived (history not rewritten):

- **M3B** `M3B_NATIVE_V2_READ_ENGINE_COMPLETE` — accepted at `180da59`.
  29 v2-native semantic reads; legacy read execution = 0.
- **M4** `M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE` — accepted at `10d1f36`.
  v2 World finalization is the authoritative success criterion.
- **M5** `M5_V2_LIFECYCLE_AND_LEGACY_AUTHORITY_RETIREMENT_COMPLETE`.
  v2-only default normal mode; legacy is an opt-in compat island;
  reachability-based GC with pin protection.
- **M6** `M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE` (Phase I) — accepted
  at `c102073`. Shadow-only shared fact graph; 13/13 hard gate PASS;
  certificate safety; cross-TU resolution; GC lifecycle.
- **M6.5** `M6_5_VALIDATION_COVERAGE_CLOSED` — accepted at `143642b`.
  Adversarial validation closure; 14/14 hard gate PASS.
- **M7.0A** corpus provisioning — FreeRTOS + Mbed TLS at pinned commits.
- **M7.0B** FreeRTOS canary — 16 builds, 18/18 gate PASS, M6 SHADOW ONLY.

These are frozen. M7 MUST NOT break any of them. All run as regression
clauses of the M7 master gate.

## §4. The accepted M6 architecture (DO NOT redesign without evidence)

```
Clang Concrete Build Oracle
        ↓
Canonical Shared Semantic Facts
        ↓
Build Presence / BuildSets
        ↓
Physical Bundles + Dependency Certificates
        ↓
Exact Build-specific semantic projection
```

Do NOT redesign this architecture without evidence that it cannot satisfy
the immutable Goal. If it appears unable, follow the
architecture-deviation protocol (§58), not silent redesign.

## §5. M6 is currently SHADOW ONLY

At HEAD 143642b:
- production read authority = v2 (the M3B/M4/M5 native engine);
- production write authority = v2;
- M6 shared fact graph = SHADOW ONLY (zero production-path imports).

This is the starting point of M7. M7 migrates production authority from
v2 to the M6 shared fact graph, stage by stage, behind hard gates.

## §6. Frozen denominators (code-derived at HEAD 143642b)

From `.dsh-control/m7/state/denominator.json`:

```
CORE_SEMANTIC_TOOLS          = 32
  (29 v2_authority_route-wrapped + 3 non-migrated:
   get_vector_table, compare_variants, group_equivalent_variants)
V2_MIGRATED_SEMANTIC_TOOLS  = 29
M6_FACT_KINDS                = 12
NORMAL_INDEX_ENTRYPOINTS     = 8
NORMAL_LIFECYCLE_ENTRYPOINTS = 13
NORMAL_FALLBACK_PATHS         = 5
```

The historical "29 tools / 12 fact kinds" was investigated: the 29 count
was the v2-migrated subset only; the true core-semantic-tools denominator
is 32. The 3 non-migrated tools are production semantic tools returning
facts about code and belong in the denominator. This was NOT a silent
shrink.

These denominators are FROZEN. M7 stages may not change them without
explicit classification and human-visible evidence. The final audit
(§44) re-derives the denominator from final code and compares.

## §7. Stage transition rule (no skipping)

```
BOOTSTRAP
    ↓ gate PASS
M7.1 DUAL READ              (production-equivalent dual read, v2 authoritative)
    ↓ gate PASS + reviews
M7.2 READ AUTHORITY         (M6 becomes production read authority)
    ↓ gate PASS + reviews
M7.3 WRITE AUTHORITY        (M6 becomes production write/materialization authority)
    ↓ gate PASS + reviews
M7.4 LIFECYCLE AUTHORITY    (M6 owns World/GC/restart lifecycle)
    ↓ gate PASS + reviews
M7.5 OLD V2 RETIREMENT      (whole-TU v2 no longer required for normal operation)
    ↓ gate PASS + reviews
ECONOMIC CLOSURE            (final economic validation)
    ↓ gate PASS / classified frontend blocker
ORIGINAL GOAL FINAL AUDIT   (fresh, independent)
    ↓ final verifier + guardian
COMPLETE
```

A stage may never be skipped because a Lead believes later tests subsume it.
Controller transitions stages. Lead cannot directly mark COMPLETE.

## §8. True authority definition (anti-false-migration)

A tool counts as M6-native ONLY if:

```
production request
→ M6 shared-fact query implementation
→ M6 result returned
```

This does NOT count:
- new router → unsupported → old v2 execution;
- M6 validation → old v2 produces real result;
- M6 route label → legacy implementation.

Runtime counters and source scanners must prove actual execution. This
protects against repeating the historical M3 routing mistake.

## §9. No silent fallback

During development, an explicit emergency rollback switch may remain.
But normal successful production execution must not silently fall back.

M7.2 final gate requires:
```
CORE_NATIVE_M6_READ             = CORE_DENOMINATOR
OLD_V2_READ_AUTHORITY            = 0
UNSAFE_OR_SILENT_FALLBACK        = 0
```

If M6 fails in normal authority mode: surface the correct failure. Do not
silently return old semantics while claiming M6 authority.

## §10. False sharing is forbidden

```
FALSE_SHARED_SEMANTIC_FACTS = 0
```

always, at every stage. Certificate safety (M6/M6.5) is retained:
C macro differences, include roots, generated headers, pragma state,
typedef/layout propagation, static/local identity, cross-TU closure.

## §11. Safe reuse > maximal reuse

SAFE REUSE is always more important than maximal reuse. A false-sharing
zero requirement may never be weakened to improve a reuse metric.

## §12. Economic closure (separate from M7 production migration)

After M7.5, run the FINAL economic validation. Use FreeRTOS subsets
(2/5/10/full) and Mbed TLS. Compare production shared fact graph against
an explicit benchmark/reference execution of the previous whole-TU v2
approach (the old engine may run in BENCHMARK MODE even though retired
from normal production).

Measure separately (never one combined "reuse %" metric):
frontend_preprocess_parse, semantic_materialization, cross_tu_resolution,
storage_write, finalization, total_wall_time, semantic_storage_bytes,
unique_facts, shared_facts, build_private_facts, distinct_buildsets,
query_p50/p95/p99.

## §13. Frontend bottleneck rule

If final measurements show: shared fact graph production migration is
correct and materially reduces semantic work/storage, BUT Clang frontend
parsing dominates total wall time so strongly that the ORIGINAL indexing-
cost Goal is still not meaningfully achieved, then:

- M7 production migration MAY be declared complete;
- BUT `ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED` must NOT be declared;
- Return `ORIGINAL_GOAL_REMAINING_FRONTEND_BOTTLENECK` with exact
  measurements;
- Do NOT automatically start a custom variability-aware parser project
  (that is a new product/architecture decision requiring human input).

## §14. Performance baseline freeze

Before major authority changes, capture production baseline distributions
(P50/P95/P99 for representative queries; frontend/semantic/persistence/
finalization/total for indexing). Freeze baseline and performance
acceptance formula BEFORE seeing final migrated measurements. Do not move
thresholds later merely to obtain PASS. The old "query < 5s" research
threshold is NOT acceptable for production — use relative baselines.

## §15. FreeRTOS as primary production canary

The frozen M7.0B FreeRTOS pack (16 builds) is the primary long-running
canary. Do not redesign it during M7 except to fix a proven corpus bug.
Fast lane: 4 representative builds on frequent iterations. Full lane:
full 16-build matrix at every major authority boundary and final gate.

## §16. Mbed TLS as second independent corpus

Prevents overfitting migration logic to FreeRTOS-specific structure. Do
not tune production logic specifically to make FreeRTOS pass.

## §17. Fresh review policy

Fresh reviews (independent of Lead reasoning) are mandatory at:
M7.1, M7.2, M7.3, M7.4, M7.5 completion; Economic Closure; Original Goal
Final Audit. Reviewer artifacts must record exact HEAD, dirty-state
identity, gate version/hash, corpus manifest hash, timestamp. A PASS from
another HEAD is invalid.

## §18. Defect policy

`.dsh-control/m7/state/unresolved_defects.json` tracks every defect:
id, stage, severity, description, reproducer, root_cause, status,
fix_head, verification. Final gate requires
`UNRESOLVED_BLOCKING_DEFECTS = 0`. Do not close defects because they are
inconvenient. Every real bug produces a permanent regression test.

## §19. Checkpoint rule

At every accepted stage: record a durable checkpoint. If the Git working
tree permits a safe project-owned commit (only M7-owned files), create
it. Never stage unrelated user files. Never reset or clean unrelated work.
If unrelated modifications prevent a safe commit, record HEAD, exact
diff hash for M7-owned files, gate evidence, timestamp, and continue
without destructive Git actions.

## §20. Human escalation is rare

Do NOT ask the human for: test failures, compiler errors, parity failures,
schema bugs, performance regressions, race conditions, GC bugs, corpus
issues, failed reviewer attempts. Solve autonomously. Escalate ONLY when:
(a) product semantics decision (two reasonable behaviors differ, contracts
/code don't define which); (b) immutable architecture conflict (M6 proven
incapable without changing its core semantic contract); (c) safety/
correctness trade-off (continuing requires knowingly accepting incorrect
/unsafe reuse); (d) final frontend decision (M7 succeeds but original
economic Goal blocked by compiler frontend cost, requiring a new project).

## §21. No weakening

No agent may weaken this contract. No agent may declare the Goal complete
unless the executable master hard gate exits 0 against the current durable
checkpoint AND a fresh Verifier + fresh Guardian accepted that exact HEAD.
Ledgers and status files are CACHES, not truth — the code-derived
scanners and the executable hard gates are truth.

## §22. Completion formula

The original Goal is complete only if:
```
semantic correctness          = PASS
multi-Snapshot correctness    = PASS
multi-Build correctness       = PASS
historical correctness        = PASS
fine-grained cross-Build reuse = ACTIVE IN PRODUCTION
production read authority     = Shared Fact Graph
production indexing authority = Shared Fact Graph
production lifecycle authority = Shared Fact Graph
false reuse                   = 0
whole-TU v2 normal dependency = 0
storage economics             = PASS
semantic materialization economics = PASS
query performance             = PASS
end-to-end indexing economics = PASS
  OR explicitly classified as remaining frontend bottleneck
```

If end-to-end economics remain materially blocked by frontend parsing:
M7 may complete, but ORIGINAL Goal remains open as
`ORIGINAL_GOAL_REMAINING_FRONTEND_BOTTLENECK`. Do not lie.
