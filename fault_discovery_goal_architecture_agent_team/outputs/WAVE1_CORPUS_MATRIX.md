# WAVE1 CORPUS MATRIX (Wave-1 Design Gate revision) — supersedes the S0 version

Correction of record (Wave-1 design gate, user-directed): the S0-era matrix contained a counting inconsistency — it registered a "reshape a contract test" gap although C1+C4+C5+C6 already satisfy the ≥4 E2E minimum once C4/C6 are designed. Adjudication below. Governing rubric: S0_GOAL_ADR §2.

## 0. GAP NAMESPACE REGISTRY (canonical; no unqualified "gap-N" in governing artifacts)
| Canonical ID | Meaning | Status |
|---|---|---|
| CORPUS-GAP-1 | Pin the real public-source fault case (C4) with credible independent oracle | **CLOSED (design)** — C4 pinned: Apache NuttX getcwd/CONFIG_DISABLE_ENVIRON, PR #14365; package materialized (freeze at implementation) |
| CORPUS-GAP-2 | Negative/no-fault E2E case (C6) | **CLOSED (design)** — C6 = paired post-fix snapshot of C4, presented unlabeled; package materialized |
| CORPUS-GAP-3 | (old) reshape a contract test into an extra E2E case | **RETIRED_AS_NON_REQUIRED** — C1+C4+C5+C6 satisfy the ≥4 minimum; C2r/C3r re-classified OPTIONAL_STRETCH_CASE |
| MEASURE-GAP-U3 | Variant-divergence detectability/isolation measurement (multi-variant re-earn) | open, post-wave |
| MEASURE-GAP-CHANREP | Independent-channel (grep/WSL clang -E) representativeness | open, Wave-1 measurement |
| MEASURE-GAP-PLANT | Guard/detector effectiveness (superseded in form by per-detector MUST-PASS plant tests) | closed-in-form by DETECTOR_ADMISSION_MATRIX; execution at implementation |
| MEASURE-GAP-SSTAB | S-stability Δ>20 invalidation run | open, pre-any-S-gated-decision |
| SUBSTRATE-GAP-EXPORT | Raw-evidence export costing vs clang -dM -E before Track-B admission | open, Track-B gate |

Historical records (R3–R6 aggregates, redteam/, revisions/, RELEASE_AUDIT.md, S0_RELEASE_AUDIT.md, archive_r7/) retain original unqualified forms as point-in-time text; this registry maps them.

## 1. Corrected case accounting

The four E2E cases satisfying rubric item 1 are **C1, C4, C5, C6** (C4/C6 currently in design by D1; counted as E2E only once frozen per CORPUS_LOCK_PLAN). C2/C3 are and remain **contract/detector tests** (PT-G6 / PT-G7) and do NOT count toward the E2E corpus. No synthetic fault is invented to fill a count.

## 2. C1 REDESIGN (source-deterministic; supersedes the "queue-full" framing)

The prior C1 wording ("work submission lost under a queue/queue-full condition") implied runtime resource exhaustion, which Wave-1 source-only rules forbid concluding. **C1 is redesigned as a SOURCE-DETERMINISTIC workqueue fault mechanism** in real Zephyr code structure, planted before freeze:

- Mechanism class (chosen shape): **initialization/submission pairing becomes inconsistent under a deterministic source/config condition** — a Kconfig-gated/`#if`-guarded branch omits the required `k_work_submit` (or submits the wrong work item, or an error path deterministically prevents the intended submit), while the module still reports readiness. Exact plant is fixed in `corpus/C1_PFC1_SOURCE_DETERMINISTIC/` by D1.
- Discovery requires condition-chain evidence across files (Kconfig default + conditional + call-graph reachability), not a trivial grep.
- The RuleCard describes the SOURCE mechanism only. **Two-part separation is mandatory**: (a) source-level fault precondition/mechanism — provable from T0/T1 surfaces; (b) runtime occurrence evidence — explicitly recorded as "required but unavailable" (e.g., whether the work item is ever actually queued at runtime is NOT a source-derived fact).
- Forbidden overclaim (hard): "queue full occurred" / "submission was lost at runtime" / any resource-exhaustion claim. The correct RuleCard asserts the source-level unreachable-submit / pairing-inconsistency mechanism.
- C1 oracle updated accordingly (expected RuleCard predicates + expected refutation + expected forbidden-overclaim refusal).

## 3. Case matrix (design-gate revision)

| # | Case | Real/planted/negative | Repo lineage | Fault mechanism | RuleCard E2E? | Blind oracle? | Quarantined surface? | Counts toward 4-case acceptance? |
|---|---|---|---|---|---|---|---|---|
| C1 | PFC-1 source-deterministic workqueue pairing fault (planted pre-freeze in real Zephyr structure) | planted | Zephyr (lineage A) | config/condition-gated init-submit pairing inconsistency (R3a/R1 class); runtime occurrence explicitly out of scope | **YES** | **YES** (planted; oracle custodian-held) | No (T0/T1 surfaces) | **YES** (first plumbing E2E; not alone establishing usefulness) |
| C2 | Exact-lookup ambiguity — CONTRACT TEST | negative-discipline | synthetic fixture (lineage C) | none — PT-G6 validation | No | n/a | No (T1) | **NO** (contract test) |
| C2r | Exact-lookup reshaped E2E | OPTIONAL_STRETCH_CASE | synthetic | symbol-resolution-conditioned fault | YES if built | YES | No | only if independently justified by D1 on mechanism value, not corpus count |
| C3 | Large-fanout truncation — CONTRACT TEST | negative-discipline | synthetic fixture (lineage C) | none — PT-G7 validation | No | n/a | No | **NO** (contract test) |
| C3r | Large-fanout reshaped E2E | OPTIONAL_STRETCH_CASE | synthetic | context-scoped fault under fanout | YES if built | YES | No | only if independently justified by D1 on mechanism value, not corpus count |
| C4 | REAL public-source fault (pinned upstream bug, second RTOS lineage, pre-fix snapshot) | real | lineage B (NuttX/RIOT/FreeRTOS — pinned by D1) | per pinned upstream mechanism (config/conditional participation required) | **YES** | partial-blind: commit-derived GT + ≥30% blind rows (item 7) | boundary exercise where applicable | **YES** (pending CORPUS-GAP-1 closure) |
| C5 | T2-touching tripwire: single-line/conditional-body planted fault (F-V1-007 class); decisive evidence via T0/T1; T2 surface present and tempting | planted | lineage B/C | fragment-conditioned fault | **YES** (T2 row as load-bearing = automatic fail) | YES (planted) | **YES** (T2) | **YES** |
| C6 | NEGATIVE CONTROL: paired POST-FIX snapshot of the C4 bug (presented unlabeled alongside C4) | negative (real corrected code) | lineage B (same repo as C4) | none — correct output = reject hypothesis / refute candidate RuleCard, zero false-positive RuleCards accepted | **YES** (refutation execution) | YES (paired-snapshot custody) | No | **YES** (pending CORPUS-GAP-2 closure; preferred over synthetic no-fault) |

## 4. Rubric satisfaction (design-gate assessment)

| Item | Status |
|---|---|
| 1. ≥4 E2E cases | **SATISFIED BY DESIGN**: C1+C4+C5+C6 (once frozen) — no C2r/C3r needed |
| 2. real/public-source fault | C4 (CORPUS-GAP-1 closure by D1) |
| 3. planted blind case | C1, C5 (C4 partially blind via item-7 rows) |
| 4. T2-touching case | C5 (+C4 boundary) |
| 5. negative/no-fault case | C6 paired post-fix design (CORPUS-GAP-2 closure) |
| 6. ≥2 lineages | A (Zephyr) + B (second RTOS) + C (fixtures) |
| 7. ≥30% blind rows | protocol (FR-6) |
| 8. zero silent unsupported | E1–E7 + C5 tripwire |
| 9. per-detector MUST-PASS | DETECTOR_ADMISSION_MATRIX (G1/G4/G6/G7/G8) |
| 10. RuleCard completeness | FR-1 + D2 schema freeze |

## 5. C2/C3 disposition (task §6, adopted)
C2 stays PT-G6 (exact-lookup, `_fallback` rejection); C3 stays PT-G7 (truncation honesty). They are NOT counted as E2E. D1 may register C2r/C3r as OPTIONAL_STRETCH_CASE only with an independent mechanism-value justification recorded in CORPUS_LOCK_PLAN.md — corpus-count pressure is explicitly rejected as a justification.
