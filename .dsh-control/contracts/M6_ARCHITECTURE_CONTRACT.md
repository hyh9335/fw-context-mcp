# M6 Architecture Contract — FROZEN

**Status**: FROZEN at HEAD `d3e29ee` (post-Arbiter ACCEPT_WITH_MODIFICATIONS).
**Arbiter verdict**: ACCEPT_WITH_MODIFICATIONS (Fresh Arbiter `e0661d69`).
**Modifications incorporated**: M1, M2, M3, M5 (frozen here); M4 (deferred to Phase R re-measurement, see §10).

This document is the **frozen specification** that Phase I (§24, layers I1–I12)
implements against. It MUST NOT be weakened. Phase I layers may add detail but
MUST NOT contradict the principles here.

---

## §0. Architecture in one paragraph

A four-layer **Build-Conditioned Shared Semantic Fact Graph**:

1. **Layer 1 — Clang Concrete-Build Semantic Oracle**: for each Build, Clang is
   the sole semantic authority. Clang parses the TU and yields an AST.
2. **Layer 2 — Canonical Shared Semantic Facts**: facts extracted from the AST
   are canonicalized by *resolved meaning* (USR + signature + field composition
   + init value + ...), NOT by source spelling. Identical facts across Builds
   collapse to a single canonical identity.
3. **Layer 3 — Build Presence / BuildSets**: each fact carries a `BuildSet` —
   the set of Build names in which it is present. Build projection = filter
   facts where `build_name ∈ fact.BuildSet`. O(1) membership test.
4. **Layer 4 — Physical Fact Bundles**: facts are stored grouped by
   `(fact_kind, buildset_id)` for storage efficiency. This is a pure physical
   optimization that cannot change semantics (§8).

Plus **Semantic Dependency Certificates** (§M3) for the materialization
shortcut: when a TU's certificate matches a cached entry, Clang parsing and
fact extraction can be skipped entirely.

The three non-collapsible concepts — **semantic identity** (what a fact
MEANS), **build presence** (which Builds it is valid in), and **dependency
certificate** (why reuse is safe) — are kept separate in the schema and never
collapsed (§2.6).

---

## §1. Required shared structure (from goal contract)

Every item in the goal's "shared structure" list maps to a fact kind with a
canonical identity function. This is the **fact-category matrix** (Arbiter M2):

| §1 item | Fact kind | Canonical identity function | Status |
|---|---|---|---|
| symbols | `Symbol` | `canon_symbol(name, kind, usr)` | ✅ Phase R |
| functions | `Function` | `canon_function(name, usr, signature, attributes, storage_class)` | ✅ Phase R |
| variables | `Variable` | `canon_variable(name, usr, var_type, is_global, init_value, storage_class, bitfield_width)` | ✅ Phase R |
| types | `Type` | `canon_type(name, kind, usr, size, field_sig, pack_state)` | ✅ Phase R |
| fields | `Field` | `canon_field(type_usr, field_name, field_type, bitfield_width)` | ✅ Phase R |
| enum constants | `EnumConstant` | `canon_enum_const(enum_usr, name, value)` | ✅ Phase R |
| **call edges** | `Call` | `canon_call(caller_usr, callee_usr, callee_signature, line, is_indirect)` | ✅ Phase R |
| **type/inheritance relations** | `Inherits` | `canon_inheritance(derived_usr, base_usr, access, is_virtual)` | ✅ Phase R |
| **references** (non-call) | `Reference` | `canon_reference(source_usr, target_usr, ref_kind, location)` | 🔲 Phase I (I5) |
| **conditional-state facts** | `ConditionalState` | `canon_conditional_state(file, line, directive, condition, taken_branch_state)` | 🔲 Phase I (I8) |
| **body facts** | `Body` | `canon_body(symbol_usr, body_text_sha256)` | 🔲 Phase I (I4) |
| **file content facts** | `FileContent` | `canon_file_content(file_path, content_sha256)` | 🔲 Phase I (I4) |

### §1.1 Reference fact kinds (Phase I, I5)

The `Reference` fact kind has 6 sub-kinds, each with its own canonical
identity (modeling the existing v2 `references` table `ref_kind` column):

| ref_kind | Canonical identity | Notes |
|---|---|---|
| `call` | (covered by `Call` fact) | direct function call |
| `ref` | `canon_reference(source_usr, target_usr, "ref", location)` | variable read / type use |
| `member` | `canon_reference(source_usr, target_usr, "member", location)` | struct member access |
| `indirect` | `canon_reference(source_usr, target_usr, "indirect", location)` | function-pointer call/assignment |
| `implicit_construct` | `canon_reference(source_usr, target_usr, "implicit_construct", location)` | C++ global ctor |
| `macro_use` | `canon_reference(source_usr, target_usr, "macro_use", location)` | macro invocation |

Each needs a mutation test (M16–M21) modeled on M10 (which already tests
`callee_usr` for direct calls). Required by gate §3 `CALL_GRAPH_PARITY` and
`VARIABLE_TRACKING_PARITY`.

### §1.2 ConditionalState fact (Phase I, I8)

Captures `#if`/`#ifdef`/`#ifndef` group evaluation per Build:

```
canon_conditional_state(file, line, directive, condition, taken_branch_state)
```

where `taken_branch_state` is the evaluated branch for the active build
(`TAKEN`/`FALSE`/`NOT_EVALUATED`), matching the existing
`explain_condition` tool semantics. Two Builds with the same `#if` directive
but different `#define` flags produce different `taken_branch_state` →
different canonical facts → no false sharing. Required by gate §3
`CONDITIONAL_PARITY`.

A mutation test (M15) drops `taken_branch_state` and verifies it's caught
when a build's `#define` changes the branch outcome.

### §1.3 Body and FileContent facts (Phase I, I4)

- `Body(symbol_usr, body_text_sha256)`: the ifdef-filtered function body
  text, hashed. Two builds with identical source but different `#ifdef`
  branches produce different filtered body text → different hashes → no
  false sharing. The `m6_body_facts` table is pre-defined in `m6_schema.py`.
  A mutation test (M15b) drops the build-filter from body facts.
- `FileContent(file_path, content_sha256)`: the ifdef-filtered full file
  content, hashed. Matches the existing `read_file` semantics. Required for
  `HISTORICAL_PARITY`.

---

## §2. Non-negotiable principles (12, from goal contract)

1. **Preserve exact semantic view** for every explicit Snapshot × Build.
2. **Share unchanged semantic structure** across Builds at finer granularity
   than the whole TU.
3. **Build-specific differences remain isolated** — never merged.
4. **False reuse is forbidden** — proven by 14/14 mutation CAUGHT.
5. **Clang is the sole semantic authority** (§2.11) — no second parser.
6. **Canonical identity by resolved meaning, not source spelling** (§2.4).
7. **Exact per-Build projection** via BuildSet membership (§2.8) — no
   newest-wins, no representative-Build substitution.
8. **Physical bundling is pure optimization** — cannot change semantics (§8).
9. **Three non-collapsible concepts**: identity / presence / certificate.
10. **Reachability-based GC** — no age-only GC (§2.7, §M1).
11. **Three cost classes measured separately** (§2.9, §M4): frontend parse,
    semantic materialization, storage writes.
12. **Shadow-first migration** — Phase I runs in shadow mode until
    `QUERY_PARITY_FAILURES == 0` across all mandatory query families (§3).

---

## §M1. GC model (Arbiter M1 — F4 fix)

The GC model is **World→Builds map + per-build refcounts**, NOT per-World
reference counting on BuildSets.

### §M1.1 Schema additions

Add table `m6_world_builds`:
```sql
CREATE TABLE m6_world_builds (
    world_id  TEXT NOT NULL,
    build_name TEXT NOT NULL,
    PRIMARY KEY (world_id, build_name)
);
```
This is a many-to-many map: a World encompasses one or more Builds (a
sysbuild variant World may span multiple image Builds); a Build may belong
to multiple Worlds.

### §M1.2 Per-build refcount

`build_refcount(build_name) := COUNT(world_id) WHERE build_name IN
m6_world_builds AND world_id is active`.

Active Worlds are the GC roots:
- the current completed World (M5 controller rule),
- retained Worlds (explicitly retained by the operator),
- pinned Worlds (cannot be GC'd),
- Worlds referenced by in-flight queries (grace period, §23).

### §M1.3 Fact reachability

A fact is **reachable** iff `fact.BuildSet ∩ {builds with refcount > 0} ≠ ∅`.

A fact is **unreachable** iff its BuildSet contains only builds whose
refcount is 0.

### §M1.4 Deletion sequence

1. When a Build's refcount → 0 (its last referencing World is deactivated):
2. Remove that build_name from every fact's BuildSet.
3. If any fact's BuildSet becomes empty → the fact is unreachable.
4. Unreachable facts enter a **grace period** (§23) for in-flight queries.
5. After the grace period, unreachable facts are deleted.

### §M1.5 Forbidden operations

- **Per-World removal from BuildSets** is FORBIDDEN (the type error from
  the original §3.1/§3.2 of ARCHITECTURE_NOTES.md). BuildSets contain build
  NAMES, not World IDs. Removing a World from a BuildSet is a category error.
- **Age-only GC** is FORBIDDEN (§2.7). A fact is never deleted merely
  because it is old.
- **Deleting a pinned World** is FORBIDDEN.

### §M1.6 Required GC test suite (I10 acceptance)

Before I10 ships, the GC test suite MUST include:
- (a) World spanning multiple builds;
- (b) build shared between multiple Worlds;
- (c) World deactivation that leaves a build still referenced by another
      World (build's refcount does NOT drop to 0);
- (d) grace-period for in-flight queries;
- (e) pinned World protection (pinned World cannot be GC'd).

The current 2-world shadow-store GC test is insufficient and MUST be
expanded before I10 acceptance.

---

## §M2. Fact-category matrix — see §1 above

The complete matrix is in §1. Phase I layers I4, I5, I8 are responsible for
implementing the 🔲 items (Reference, ConditionalState, Body, FileContent).

---

## §M3. Semantic Dependency Certificate (Arbiter M3 — F5.1 fix)

The certificate determines when fact extraction can be SKIPPED (reuse cached
facts from a prior Build with the same certificate). It is a TU-level fast
path; fact-level reuse (Prototype A) operates independently.

### §M3.1 Certificate legs

The certificate has FIVE legs. Two TUs with the same certificate will produce
IDENTICAL semantic facts.

| Leg | Captures | Computed via |
|---|---|---|
| **L0** | file bytes | SHA256 of source file content |
| **L1** | include closure (ordered) | libclang `tu.get_includes()` (ordered list of included file paths) |
| **L2** | macro state after preprocessing | libclang `MACRO_DEFINITION` cursors (walk PREPROCESSING_DIRECTIVE children) — captures ALL #define values including -D, header-defined macros, builtins |
| **MacroEnv** | stateful-macro expansion results | captured WITHIN L2: `__COUNTER__` final value, `__DATE__`/`__TIME__` string at parse time, `__FILE__` per-TU |
| **L3 / Toolchain** | toolchain flags | SHA256 of `--target`, `-std`, `-fexceptions`/`-fno-exceptions`, `-frtti`/`-fno-rtti` |

### §M3.2 Stateful macros (the hard part)

`__COUNTER__`, `__DATE__`, `__TIME__` are **compiler-internal state, NOT part
of the macro environment**. They MUST be captured:

- **`__COUNTER__`**: appears as a `MACRO_DEFINITION` cursor with its final
  value after the TU is fully parsed. Two TUs with identical source + defines
  but different `__COUNTER__` expansion points (e.g., a `__COUNTER__` at line
  50 vs line 100) produce different final values → different L2 → different
  certificates. This is correct: they produce different facts (e.g., different
  `init_value` for `__COUNTER__` variables — proven by T27 mutation test M3).
- **`__DATE__`/`__TIME__`**: per-invocation non-deterministic. Captured as
  their string value in L2. Two parses of the same TU at different times
  produce different `__DATE__` → different certificates. This is correct: they
  produce different facts (the `__DATE__` string literal differs).
- **`__FILE__`**: produces per-TU anonymous array types. Captured in L2 via
  its macro definition. This is the "fact explosion in logging macros" issue
  the C Preprocessor Critic flagged — the certificate correctly distinguishes
  TUs by `__FILE__`.

### §M3.3 Flag modeling

- **`-U name`** (undefine): entered into L2 by removing the macro from the
  macro state. The certificate reflects the ABSENCE of the macro.
- **`-include file`** (force-include): entered into L1 (include closure) and
  L2 (macros from that file). Treated as if `#include "file"` appeared at the
  top of the source.
- **`-imacros file`** (include macros only): entered into L2 (macros from
  that file) but NOT L1 (no semantic include — only macros are absorbed).
  The certificate distinguishes `-imacros` from `-include` because the latter
  also adds to L1.
- **`-std=c99`/`-std=c11`/`-std=c++17`**: entered into L3 (toolchain). Affects
  AST shape (e.g., `_Static_assert` availability, designated initializer
  rules). Two TUs with different `-std` MUST have different certificates.
- **`-fexceptions`/`-fno-exceptions`**: entered into L3. Changes function types
  (exception specifications) and class layout.
- **`-frtti`/`-fno-rtti`**: entered into L3. Changes class layout (RTTI
  pointer presence).

### §M3.4 Defense-in-depth: fact_set_digest

The certificate is the **fast path**. The **correctness check** is
`fact_set_digest` — a SHA256 of the sorted canonical fact strings, stored
alongside each cached entry.

When a certificate HIT occurs:
1. Reuse the cached facts (fast path).
2. **In shadow mode (I11)**: re-extract facts and compare
   `fact_set_digest`. If they differ → certificate caused FALSE REUSE.
3. If false reuse is ever detected → fall back to full materialization AND
   flag the certificate as unsafe for that TU pattern.

**Phase R evidence** (`prototype_c_results.json`): the safety verification
mode re-extracts facts on every cert hit and compares. Current result:
**`safety_violation: False`, `total_false_reuse: 0`**. The certificate is
proven safe on the scaling corpus.

### §M3.5 Certificate hit-rate honesty

The certificate's hit rate depends on **build overlap**: builds that share the
same TU file + same defines + same includes will hit; builds with different
`-D` flags will miss (correctly — different defines → different macro state
→ different facts).

Phase R scaling corpus has each build with a different feature combination,
so the cert hit rate is 0% (correct — every build's macro env differs).
The certificate's value is for **incremental re-indexing** of the SAME build
when only unrelated files change, and for builds that genuinely share a TU's
preprocessor environment.

---

## §M4. Three cost classes (Arbiter M4 — deferred to Phase R re-measurement)

Phase R evidence (`prototype_c_results.json`) now reports the three cost
classes separately (M4 fix applied):

- **`frontend_parse_s`**: Clang parse time per TU (paid ONLY on cert miss).
- **`semantic_materialization_s`**: AST extraction + canonicalization
  (paid ONLY on cert miss).
- **`storage_writes`**: fact writes to store (the storage savings metric).

Current 20-build numbers:
- frontend_parse_s: 0.982s
- semantic_materialization_s: 0.141s
- cert_compute_s: 1.566s (always paid)

The §10 usefulness gate thresholds will be set AFTER Phase R re-measurement
on a real C corpus (§36, mbedTLS if available), not on the synthetic scaling
corpus. M4 is deferred to that re-measurement; it is NOT a freeze blocker.

---

## §M5. Cross-TU resolution model (Arbiter M5)

### §M5.1 Choice: option (a) — index-all-TUs-together with global symbol table

The architecture picks **option (a)**: index all TUs of a Build together,
building a per-Build global symbol table, and resolve cross-TU references
at indexing time.

**Rationale**:
- Option (b) (lazy resolution at query time) increases query overhead and
  makes query results non-deterministic (depend on which TUs have been
  resolved). The contract §2.9 requires query overhead to be reported
  separately, and lazy resolution would make this unpredictable.
- Option (a) increases indexing time (scales with build size, not TU count)
  but gives deterministic query results and enables the certificate shortcut
  to work correctly (the certificate is per-TU; cross-TU resolution happens
  AFTER certificate check, on cert miss).

### §M5.2 Per-Build global symbol table

For each Build, before canonicalizing `Call` and `Reference` facts, build a
`name → usr` map from all TUs of that Build. This resolves cross-TU callee
USRs (T30_cross_tu_callee tests this case).

The `callee_usr` in `canon_call` comes from `clang.referenced` (already
captured in the GT extractor). For cross-TU calls where the callee is
defined in another TU of the same Build, the global symbol table resolves
the name to the callee's USR.

### §M5.3 Fact storage: global, with BuildSet membership

The `m6_shared_facts` table is **global** (one fact table shared across all
Builds, with BuildSet membership). Cross-TU resolution happens BEFORE
canonicalization — the USR is resolved, then the canonical fact string is
computed. This ensures that two TUs calling the same cross-TU function
produce the same canonical `Call` fact (same `callee_usr`), enabling sharing.

### §M5.4 Multi-TU build acceptance (I9)

Before I9 ships, the test suite MUST include:
- (a) a Build with 2+ TUs where TU-A calls a function defined in TU-B;
- (b) verification that `callee_usr` resolves correctly across TUs;
- (c) verification that two Builds sharing the same cross-TU callee produce
      the same canonical `Call` fact (sharing);
- (d) verification that two Builds with different cross-TU callees (same
      name, different definition) produce different canonical `Call` facts
      (no false sharing — already tested by T30).

---

## §3. Phase I shadow migration gates (§34, §35)

Phase I runs in **shadow mode**: the M6 store is populated alongside the
existing v2 store, but queries still hit the v2 store. Shadow mode exits
when ALL of the following pass:

| Gate | Requirement |
|---|---|
| `QUERY_PARITY_FAILURES == 0` | Every mandatory query family returns identical results from M6 shadow store and v2 store, for every Build. Mandatory families: symbol lookup, function lookup, call graph (callers/callees), variable tracking, type lookup, field lookup, enum constant lookup, inheritance, references (all 6 kinds), conditional state, body lookup, file content. |
| `MUTATION_ALL_CAUGHT` | Each broken variant (M1–M21) is caught in the shadow store. M1–M14 already proven; M15–M21 added for new fact kinds. |
| `GC_SAFETY` | The §M1.6 GC test suite passes. |
| `CERT_SAFETY` | `safety_violation == False` on the real C corpus (§36). |
| `COST_CLASS_SEPARATION` | `frontend_parse_s`, `semantic_materialization_s`, `storage_writes` reported separately on the real C corpus. |

**Production authority transition** (I12) does NOT happen until every gate
passes. The existing v2 store remains Ground Truth throughout Phase I.

---

## §4. Open items (Phase I responsibility)

These are OPEN and assigned to specific Phase I layers. They MUST be closed
before the corresponding layer's acceptance.

| Open item | Owner layer | Acceptance criteria |
|---|---|---|
| F4 GC redesign | I10 | §M1.6 test suite passes |
| F5.1 certificate (real corpus) | I11 | `CERT_SAFETY` gate on real C corpus |
| F1/F5 fact-category matrix | I4, I5, I8 | All 🔲 items in §1 implemented + mutation tested |
| F7 concurrency | I3 | SQLite transaction isolation documented |
| F8 body/source facts | I4 | `Body` + `FileContent` facts + M15b mutation |
| F11 cross-TU parity (scaling) | I9 | §M5.4 test suite passes |
| Corpus expansion (~9 patterns) | Phase R ongoing | C Preprocessor Critic patterns covered |

---

## §5. Phase R evidence (frozen at this contract)

- **Prototype A**: 21/21 cases PASS, `false_shared=0`, `parity_failures=0`.
- **Mutation testing**: 14/14 CAUGHT (M1–M14).
- **Shadow store**: in-memory projection matches DB projection; GC
  reachability correct for the simple 2-world case.
- **Prototype C (certificate)**: `safety_violation=False`,
  `total_false_reuse=0`. M4 cost classes reported separately.
- **Scaling**: 5→20 builds, 4.0× build growth → 1.05× fact growth. Storage
  savings 83%→91.5%.

---

## §6. Amendment policy

This contract is FROZEN. Amendments require:
1. A fresh adversarial review (critic + arbiter),
2. Operator approval,
3. Re-freeze at a new HEAD.

Phase I layers MAY add detail (e.g., specific SQL DDL, specific algorithm
choices) but MUST NOT contradict the principles and modifications here.
