# C4 EVIDENCE MAP — REAL PUBLIC FAULT (custodian-held; NOT solver-visible)

Scope stamp: corpus case C4; goal items 1–2; lineage B (NuttX); schema freeze `wave1-design-gate-1`
(RULECARD_SCHEMA.json / FACT_RECORD_SCHEMA.json / REFUTATION_STATE_MODEL.md). This map records the
evidence provenance behind the C4 oracle; it is not a solver-facing task description and must be
excluded from solver context (K3 custody duty, CORPUS_LOCK_PLAN.md §4).

## 1. Evidence priority used
- P1 (primary): fix diff — https://github.com/apache/nuttx/pull/14365.patch (branch commit
  `017266ebee97aa2dbbc2e8208900b66aa4ce57f7`; author Xiang Xiao; 2024-10-16; +8/−8, 4 files).
- P2: PR conversation — https://github.com/apache/nuttx/pull/14365 (merge commit
  `67ed036227b14bbab01b63801bfd7e218dfd6107` into apache:master; no dedicated issue/regression test).
- P3: Apache NuttX 12.8.0 release notes — https://nuttx.apache.org/releases/12.8.0/ (lists #14365;
  documented caveat only).
- P4: source/build trees at pinned SHAs — pre-fix `526ba1ab343a00b2e1801e089392eed64758b820`
  (verified via GitHub API: committed 2024-10-16T15:19:33Z, master tip before the merge) and
  post-fix `67ed0362…`; era-matched nuttx-apps `15b081f02dd374b173b983dfdfeba75f229d2d35`
  (2024-10-16) for consumer context.

## 2. Why the fault is source-discoverable (condition chain, not runtime observation)
All decisive facts sit on T0 surfaces of the pre-fix snapshot:
- (a) `libs/libc/unistd/lib_getcwd.c` is enclosed by a whole-file `#ifndef CONFIG_DISABLE_ENVIRON`
  … `#endif`. `explain_condition` (T0) returns the guard text; `read_file` (T0, active mode) marks
  the ENTIRE FILE inactive (`all_lines_inactive` + warning). This is a MULTI-LINE scope fact —
  explicitly NOT the single-line F-V1-007 hazard class.
- (b) `libs/libc/unistd/Make.defs` and `CMakeLists.txt` list `lib_getcwd.c` only inside the
  `ifneq ($(CONFIG_DISABLE_ENVIRON),y)` / `if(NOT CONFIG_DISABLE_ENVIRON)` block (conditional-only
  pre-fix; the fix moves it to the unconditional list).
- (c) `libs/libc/stdlib/Kconfig`: `LIBC_HOMEDIR` carries `depends on !DISABLE_ENVIRON` (removed by
  the fix).
- Existence/absence channel (R4): exact-name lookup for `getcwd` yields no definition row under the
  pinned config; the absence claim is enumerated over ≥2 surfaces (in-band absence + independent
  grep for the definition text) per E4/E7 — ambiguous absence adjudicates PRESENT, so both probes
  must be clean before RC2 is load-bearing.
- Independent grep channel, cross-file anchors: (1) guard text in lib_getcwd.c; (2) conditional-only
  build listing in Make.defs/CMakeLists.txt; (3) `depends on !DISABLE_ENVIRON` in stdlib/Kconfig.

## 3. Why the root cause is not runtime-dependent
The mechanism is decided entirely by the preprocessor and the build manifest under the pinned
config: no `getcwd` definition exists in the linked image, so ANY linked consumer hits a link-time
undefined reference. No runtime state (env contents, cwd, PWD value, scheduling, memory) takes part;
runtime claims are therefore out of scope and policed as forbidden overclaims (§6).

## 4. Consumer pinning — trail and recorded fallback
- nuttx-apps PR #2732 (fetched): fixes `cd` path logic (`nsh_getdirpath` → `lib_realpath` in
  `nshlib/nsh_fsutils.c`). NOT a getcwd consumer.
- `nshlib/nsh_envcmds.c` at era SHA (fetched): NSH's `nsh_getcwd()` is an internal `getenv("PWD")`
  wrapper; `cmd_pwd`/`cmd_cd`/`nsh_getfullpath` are all `#ifndef CONFIG_DISABLE_ENVIRON`-guarded;
  `cmd_ls` calls the same guarded wrapper ⇒ NSH never links libc `getcwd()` under the fault config.
- PR discussion names no consumer ("Impact: extend getcwd usage"); no dedicated issue exists.
- Code-search channels unavailable this session: unauthenticated GitHub code search (login wall),
  grep.app API HTTP 422, Sourcegraph stream HTTP 422, DuckDuckGo "no results", Bing generic-only,
  git network egress blocked (schannel SEC_E_NO_CREDENTIALS).
- RECORDED FALLBACK (per plan): consumer predicate = "any caller of `getcwd()` at link time". RC4 is
  existential (negatable=false); an accepted RuleCard must NOT name a specific consumer file.

## 5. Acceptance criteria (case level) — one-line evidence each
1. Real public-source fault (rubric 2): merged upstream fix `67ed0362…` in apache/nuttx master (PR page).
2. Second RTOS lineage (rubric 6): lineage B NuttX vs lineage A Zephyr (WAVE1_CORPUS_MATRIX.md §3).
3. Config/conditional participation (matrix §3): fault exists only under CONFIG_DISABLE_ENVIRON=y (guard chain §2).
4. Partial-blind protocol (rubric 7): commit-derived GT + ≥30% blind load-bearing rows held by custodian (FR-6).
5. Zero silent unsupported claims (rubric 8): expected RuleCard carries non-empty forbidden_overclaims; E1–E3 gates apply.
6. Predicate completeness (rubric 10): RC1–RC5 objects match RULECARD_SCHEMA trigger_conditions shapes.
7. Deterministic machine-evaluable predicates (rubric 10): each RC statement is evaluable over recorded FactRecords.
8. Executable refutation (rubric 10): steps replayable under the round freeze_id; expected NOT_REFUTED_WITHIN_SCOPE.

## 6. Forbidden overclaims (scoring-enforced)
chdir-broken; runtime PWD/cwd state; getcwd-wrong-value (incl. "returns HOME instead of cwd");
ENOMEM/allocation behavior. Any accepted RuleCard claiming getcwd-wrong-value or chdir-broken is a
false positive: the case fails.

## 7. Correction of record (research-phase input vs primary evidence)
The pinned research described pre-fix Make.defs/CMakeLists as listing lib_getcwd.c "unconditionally
AND again conditionally (duplicate)". The merged fix diff shows the pre-fix state is
CONDITIONAL-ONLY: the fix MOVES lib_getcwd.c from the conditional block to the unconditional list
(no duplicate exists in either state). Mechanism and predicates are unaffected (no definition under
the config).

## 8. Open item (non-blocking)
Consumer file pinning is the single open item; the fallback predicate above is active and the case
is fully specified without it. Optional next-phase action: clone nuttx-apps `15b081f0…` where
network egress allows a real clone, grep for getcwd callers, and upgrade RC4 to a named consumer.
