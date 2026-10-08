# IMMUTABLE M6 GOAL CONTRACT — fw-context Cross-Build Fine-Grained Semantic Reuse

> **Immutable.** This contract freezes the M6 Goal: `M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE`.
> No agent may weaken this milestone. The gate clauses are frozen; changing
> them requires human escalation. This contract is project-local and governs
> only this Git repository.

## §1. The M6 Goal

Preserve an exact semantic view for every explicit Snapshot × Build while
sharing unchanged semantic structure across Builds at finer granularity than
the whole TU.

Shared structure must include, where safely equivalent:

- symbols;
- functions;
- variables;
- types;
- references;
- call edges;
- type/inheritance relations;
- conditional-state facts;
- other semantic facts required by normal MCP queries.

Build-specific differences must remain isolated.

False reuse is forbidden.

Semantic storage and semantic-materialization work should scale primarily
with actual semantic differences rather than number of Builds.

## §2. Non-Negotiable Principles

1. **False semantic reuse is unacceptable.** A semantic fact may be shared
   across Builds only when proven safe — resolved meaning must be identical,
   not merely source spelling. `FALSE_SHARED_SEMANTIC_FACTS == 0` is a hard
   requirement.
2. **False non-reuse is acceptable.** Over-conservative reuse is tolerable;
   false reuse is not.
3. **Semantic identity ≠ Build presence ≠ dependency certificate.** These
   are three distinct concepts and must NOT be collapsed.
4. **Macro source text is not semantic identity.** The reusable fact is
   based on the resolved meaning (e.g. `Calls(F, api_read)`), not the
   original spelling (`CALL(read)`). Macro provenance must not
   automatically poison semantic identity.
5. **Changed source region is not changed semantic region.** A typedef
   change can affect many downstream facts. M6 must model semantic
   dependency closure. Never assume `changed lines == changed semantic facts`.
6. **Every explicit Build reconstructs exactly the same semantic view as
   independent full indexing.** `QUERY_PARITY_FAILURES == 0` against
   `FULL_BUILD_GT`.
7. **Shared semantic facts are lifecycle/GC safe.** A shared fact remains
   alive while any reachable World/Build view requires it.
8. **No newest-wins. No representative-Build substitution.** Query
   projection is exact per-Build via BuildSet membership.
9. **Three cost classes are reported separately:** frontend (parse),
   semantic materialization, storage. Never combine into a single
   "reuse percentage."
10. **Frontend parsing reuse is NOT claimed unless directly proven.** It is
    acceptable if each distinct concrete Build still requires Clang
    preprocessing/parsing. If so, state this explicitly.
11. **Clang remains the truth source for each concrete Build.** Do NOT
    replace Clang with TypeChef, SuperC, or a second parser as production
    semantic authority.
12. **M3B/M4/M5/final-project regressions must remain green throughout.**
    M6 may not sacrifice existing correctness for reuse.

## §3. Hard Requirements (Final Gate)

The final M6 Hard Gate (`run_m6_stage_gate.py`) must exit 0 with:

- Research Gate PASS (`run_m6_research_gate.py`);
- Architecture frozen (`M6_ARCHITECTURE_CONTRACT.md`);
- `FALSE_SHARED_SEMANTIC_FACTS == 0`;
- `QUERY_PARITY_FAILURES == 0`;
- `C_MACRO_TORTURE == PASS`;
- `COMBINED_MACRO_ATTACKS == PASS`;
- `CALL_GRAPH_PARITY == PASS`;
- `VARIABLE_TRACKING_PARITY == PASS`;
- `TYPE_LAYOUT_PARITY == PASS`;
- `CONDITIONAL_PARITY == PASS`;
- `HISTORICAL_PARITY == PASS`;
- `SHARED_FACT_GC == PASS`;
- `CRASH_RETRY == PASS`;
- M3B PASS;
- M4 PASS;
- M5 PASS;
- final-project regression PASS;
- storage reuse threshold PASS (evidence-backed, frozen after RED baseline);
- semantic-materialization reuse threshold PASS;
- query overhead threshold PASS;
- frontend parse cost separately reported;
- Fresh Verifier PASS (mission: prove M6 unsafe/fake/incomplete);
- Fresh Guardian ACCEPT_COMPLETION (14 questions, §44);
- durable checkpoint.

No warning-pass.

## §4. Two Phases

### PHASE R — Research / Prototype Gate

Validate the candidate architecture (`Build-Conditioned Shared Semantic Fact
Graph`) before touching production authority. Build Prototype A (fact +
BuildSet), C Macro Torture Corpus, Prototype C (materialization shortcut).
Run Research Gate. If it rejects, run a fresh adversarial redesign round
before escalation.

### PHASE I — Production Implementation

Enter automatically only if Research Gate passes and architecture is frozen.
Shadow store first: new shared-fact path and accepted v2 whole-TU path both
available. Old v2 representation remains Ground Truth during migration.
Implementation layers I1–I12 (§24).

## §5. Adversarial Control

At each major checkpoint: fresh short debate (`Reuse Advocate` vs
`Semantic Safety Skeptic`), both inspect CURRENT code, Guardian receives
both findings. Fresh critics before Research Gate acceptance: Safety Critic,
C Preprocessor Critic, Query Critic, Performance Critic, Storage/Lifecycle
Critic. Fresh Arbiter decides ACCEPT / ACCEPT_WITH_MODIFICATIONS /
REJECT_AND_REDESIGN.

## §6. Stop Boundary

When M6 completes: STOP. Do NOT automatically begin: custom variability-aware
C parser; Clang replacement; generic incremental compiler frontend;
distributed indexing; unrelated performance work; physical legacy deletion.
Frontend incremental parsing is a separate future decision only if benchmark
evidence shows it dominates remaining cost.

Return only when `M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE_COMPLETE` or a
genuine immutable architectural/product decision cannot be resolved
autonomously. Do not return after intermediate milestones.

## §7. C Preprocessor is a First-Class Problem

Do not reduce C variability to `#ifdef` blocks. The corpus must adversarially
test: object/function-like/nested/variadic macros, token pasting `##`,
stringification `#`, macro-generated names/definitions/params/types/fields/
enum values/initializers, X-Macros, repeated inclusion under different macro
environments, conditional includes, generated headers, macros that change
`static`/`extern`/`inline`, attributes, calling convention, alignment,
`#pragma pack`, `__FILE__`, `__LINE__`, `__COUNTER__`, compiler builtins,
GNU `typeof`, statement expressions, target/compiler-dependent extensions.

## §8. Relations are First-Class Semantic Facts

Do not make a whole function the minimum reusable semantic unit. Represent
logically independent relationships separately where useful: `Function(F)`,
`Variable(V)`, `Type(T)`, `HasType(V,T)`, `References(F,V)`, `Calls(F,G)`,
`Declares(F,V)`, `Inherits(T1,T2)`. Each logical fact may have its own
BuildSet. Physical bundling is allowed as an optimization but must not
change semantic identity or Build visibility.

## §9. Truth Source Hierarchy

- Production code + executable hard gate outrank narration/caches.
- Fresh Verifier + Fresh Guardian must accept the exact durable checkpoint
  HEAD before completion is declared.
- `FULL_BUILD_GT` (independent Clang index per Build) is the ground truth
  for parity checks.
- The existing accepted v2 whole-TU representation remains Ground Truth
  during Phase I shadow migration.

## §10. Usefulness Gate

M6 cannot pass by sharing almost nothing. After measuring RED baseline,
freeze evidence-backed minimum requirements for: reduction in avoidable
semantic fact duplication; reduction in semantic materialization work;
acceptable query overhead. Threshold selection reviewed by Fresh Guardian
based on baseline evidence. Do not move thresholds merely to pass.

---

**Frozen at:** HEAD `cccccb7` (M5 completion checkpoint).
**Baseline gates:** M3B PASS, M4 PASS, M5 PASS, final-project 24/24 PASS.
