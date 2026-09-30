# S0 Release Audit — Independent S0 Verifier

Verifier: independent, not involved in producing pack artifacts. Read-only audit; only file written is this one.
Scope: `fault_discovery_goal_architecture_agent_team/` (markdown/JSON governance pack) + `.fw-validation/FINDINGS.json` + repo git state. Product = fw-context-mcp (Python); C/C++ fw-context rules not applicable.
Method: full reads of all S0 governing docs; targeted greps (N/M, 6/7, two-tier, goal string, dangling label); git status; SHA-256 corroboration attempt of the goal string.

## Checks

**1. No product code modified — PASS.**
`git status --porcelain` on main @ `d8f08a41d3fb9df6de255a187720f6332ed0f1c9`: zero modified/staged tracked files; untracked only: `.fw-closeout/`, `.fw-final/`, `.fw-validation/`, `CROSS_PLATFORM_FINAL_AUDIT.md`, root `RELEASE_AUDIT.md` (a separate pre-pack validation audit dated 2026-09-18), the pack dir, `fw_context_mcp_cross_platform_closeout/`, `fwctx_validation_benchmark_pack/`, `packaging/`. `state.json:29` product_head equals live HEAD. Tracked product sources untouched. (Untracked set holds prior validation material beyond the pack dir — pre-existing, not product modification.)

**2. Authoritative goal unchanged and verbatim — PASS.**
Goal string "构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。" appears byte-identical in `GOAL_BASELINE.md:4` and `S0_GOAL_ADR.md:5`, and identical to the R7 original `archive_r7/GOAL_BASELINE.md:4` (pack-wide grep: 4 occurrences, all identical). Note: `goal_hash 950744D8…` (`state.json:44`, `FACT_LEDGER.json:6`) was NOT reproducible by plain SHA-256 over the Chinese or English goal text and its recipe is undocumented; verbatim textual identity is the verified evidence (non-blocking).

**3. False prior user-acceptance record corrected — PASS.**
`state.json:57` `s0_correction`: prior record of R8 APPROVED_WITH_OPEN_DECISIONS as user-accepted + phase DONE declared INVALID; legal pre-S0 state `R8_APPROVED_WITH_OPEN_DECISIONS -> AWAITING_USER_S0`; final target `S0_APPROVED -> READY_TO_DESIGN_WAVE1_IMPLEMENTATION`, NOT DONE. Corroborated: `S0_RECONCILIATION_REPORT.md §1` (lines 5–11), `S0_GOAL_ADR.md §7` (line 71), `DECISION_LEDGER.json:157` (s0_amendments.process_correction). Project not labeled DONE: `state.json:55` phase = `S0_RECONCILIATION`; `ROADMAP.md:3` "the project is NOT DONE"; report §5. Minor residue: the superseded `r8_user_acceptance` field (`state.json:56`) is retained verbatim and neutralized only via `s0_correction` (no inline annotation).

**4. N/M removed or fully defined in governing docs — PASS.**
In the nine governing documents, N/M survives only inside retirement/definition statements: `S0_GOAL_ADR.md:20,22` ("RETIRED everywhere"; 10-item rubric), `GOAL_BASELINE.md:20-31`, `DECISION_LEDGER.json:184` (D09R), `ROADMAP.md:7`, `MINORITY_REPORT.md:26` (O2 resolution note). Zero live undefined uses in S0_GOAL_ADR / GOAL_BASELINE / CURRENT_CAPABILITY_MAP / FAULT_DISCOVERY_REQUIREMENTS / DECISION_LEDGER s0_amendments / ROADMAP / WAVE1_CORPUS_MATRIX / DETECTOR_ADMISSION_MATRIX. MINORITY O2 keeps the objection text with an additive resolution note (lines 25–26) — sufficient. Historical round records retain mentions as point-in-time history: `DEBATE_ROUND_2.md:3` carries an additive header annotation; unannotated mentions in `DEBATE_ROUND_1.md:22`, R3 debates, `RED_TEAM_REPORT.md:31`, `revisions/`, `RELEASE_AUDIT.md:54` are point-in-time records whose resolution is stated in `S0_RECONCILIATION_REPORT.md §3` (line 33) — judged sufficient. `stress/R4_S1.md:97,100` / `R4_S3.md:100` use "N/M contexts" as coverage accounting — a different usage, correctly out of scope.

**5. T0/T1/T2 trust classes internally consistent — PASS.**
Definitions match across `S0_GOAL_ADR.md §5` (lines 59–63), `CURRENT_CAPABILITY_MAP.md:5-9`, `DECISION_LEDGER.json` D02R (line 162), `FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md:32-33`. T0 = 4 named scopes (CAPMAP table lines 11–17), each explicitly carving known defects out of scope. T1 rows are all K1-mediated with a deterministic check + fail behavior (CAPMAP lines 19–26); FR-2 E2 "T1 claims flow ONLY through K1"; ADR §5 same. Detector-matrix tier assignments (G1 F-V1-003 T2, G2 F-V1-004 T2, G3 F-V1-005 T1-attested/T2, G6 F-V2-002 T1, G7 F-V1-008 T1, G8 F-V1-001 T2, G9 F-V1-006/F-V2-001 boundary) match the CAPMAP T1/T2 tables. Two-tier language remains only in preserved D01–D10 originals (superseded by D02R), `FINAL_ARCHITECTURE.md` (S0 SUPERSEDED header, line 3), and historical round records.

**6. No defective raw surface directly trusted — PASS.**
F-V1-001 → T2 (CAPMAP:32, G8); F-V1-002 → T2 subsumed by F-V1-007 (:33,40); F-V1-003 → T2 banned (:31, GA-4); F-V1-004 → T2 (:34); F-V1-005 → T2 for final-binary truth / T1 only for attested-unreachable with a deterministic root-attestation gate (:26,35); F-V1-007 → T2 (:33); F-V2-001 → T2 (:36). Expected T1s confirmed: F-V2-002 → T1 via deterministic `_fallback` rejection (:22, G6); F-V1-008 → T1 via truncation detection (:23, G7). Two additional narrowed T1 forms exist beyond the expected pair — F-V1-005 (attested-unreachable; unsafe condition = missing linker script/ENTRY, deterministically detectable) and F-V1-006 (presence-only claims, non-load-bearing Wave 1, G9 form attestation + E5 corroboration; absence claims stay T2) — both satisfy the ratified promotion rule (deterministic detectability for the narrowed claim; CAPMAP:9) and neither trusts a defective raw surface directly. T0 rows exclude the defect classes from their scopes (CAPMAP:14-17); ARCH V1:33 "raw defective surfaces are never directly trusted".

**7. No 6/7 aggregate safety gate remains — PASS.**
Original D02 revisit_trigger in the current ledger carries the marker: `DECISION_LEDGER.json:38` "[SUPERSEDED by s0_amendments.D02R: per-detector MUST-PASS, no aggregate] guard plant-test <6/7…". D02R (line 164): "the former >=6/7 aggregate gate is RETIRED everywhere"; new trigger = any load-bearing detector failing its plant test. No load-bearing 6/7 gate in ROADMAP (kill trigger line 35), FR-4 (`FAULT_DISCOVERY_REQUIREMENTS.md:21`), DETECTOR_ADMISSION_MATRIX:3, GOAL_BASELINE:30, S0_GOAL_ADR:63. Remaining "6/7" occurrences are archive_r7 originals and point-in-time historical records (`RED_TEAM_REPORT.md:27`, `RELEASE_AUDIT.md:44`, redteam R5 files) — acceptable.

**8. Per-detector MUST-PASS admission matrix — PASS.**
`DETECTOR_ADMISSION_MATRIX.md` lists G1–G9 (G5 a marked RETIRED historical row) with every required field: ID, protected claim/surface, Wave-1 load-bearing Y/N, mandatory plant tests, pass condition, fail condition, fail behavior, expiry/revalidation (lines 5–16). Load-bearing set G1, G4, G6, G7, G8 MUST-PASS 100% (line 17; FR-4:21; ADR §5:63; ROADMAP:19; D02R:164); one failing load-bearing detector blocks Wave 1 (line 3). Non-load-bearing G2/G3/G9 measured + reported, non-blocking (line 17).

**9. 4-case rubric satisfiable OR gaps registered — PASS.**
`WAVE1_CORPUS_MATRIX.md` maps all 10 rubric items (satisfaction table, lines 16–29) with honest design-time language: "No results are claimed — cases are DESIGNED, not yet run" (line 3). Contract tests C2/C3 marked "**NO** as-is" for the 4-case count (lines 8,10); reshaped C2r/C3r count only if built (lines 9,11). Satisfiable set: C1 + C4 + C5 + (C2r or C3r) + C6 = 5 candidates (line 20). Gaps registered, not invented: GAP-1 (C4 real public-source case unpinned), GAP-2 (C6 negative control missing — rubric item 5), GAP-3 (contract-test reshape required) (lines 31–35); "the corpus is NOT declared satisfied today" (line 35). Consistent with D07R (ledger:168-172).

**10. KC-2B not a prerequisite to first E2E RuleCard — PASS.**
Consistent across: `S0_GOAL_ADR.md §3` (line 39: KC-2 "NOT the gate for producing the first E2E RuleCard"; KC-2A diagnostic after the first E2E path works; KC-2B governs large substrate investment), `ROADMAP.md` (KC-2A measurement (c) post-first-E2E-path, line 20; KC-2B in Wave 2, line 24), FR-6 (`FAULT_DISCOVERY_REQUIREMENTS.md:31` "NEITHER gates the first E2E RuleCard"), D08R (`DECISION_LEDGER.json:177`). D08's original "KC-2 as FIRST measurement" is a preserved original superseded by D08R under the declared amendment pattern (ledger:156).

**11. Minority positions M1–M9 preserved — PASS.**
`MINORITY_REPORT.md` contains M1–M9 in full. M3 clarification is additive and preserves the dissent (line 13: ratification "does not declare A6's objection wrong"); M7 O1–O10 preserved with an additive S0 status note (lines 25–26; O2 objection text "retained as history"). M1/M9 additionally carried into ARCH V1 §Minority preservation (lines 51–52). Nothing erased.

**12. All 15 validation findings dispositioned — PASS.**
`.fw-validation/FINDINGS.json` holds exactly 15 IDs: F-V0-001..004, F-V1-001..008, F-V2-001..002, F-V4-001 (counted). `CURRENT_CAPABILITY_MAP.md §Disposition` (lines 38–42) accounts for all 15: V0 environment via F-R0-14 constraints; F-V1-001..008 individually tiered; F-V2-001/002 tiered; F-V4-001 watch item M8 — with F-V1-002 subsumed/amended by the F-V1-007 single-line class where applicable. Corroborated by `ROADMAP.md:29` (Track-B vs not-scheduled split, "All 15 finding IDs dispositioned") and the R8 erratum (`outputs/RELEASE_AUDIT.md:33,48`).

**13. No hidden fw-context repair wave — PASS.**
`S0_RECONCILIATION_REPORT.md §5` (line 42): no product code modified, no hidden repair wave, all repairs S-gated Track-B pulled by measured misses. ROADMAP Wave-1 IMPLEMENTATION "explicitly NOT started" (line 16) and contains no substrate repair; substrate fixes confined to Wave-2 Track-B queue / deferred list (lines 25, 28–29). FR-9 (line 42): "No substrate wave before the first E2E case (D01 cap)". CAPMAP:45 and ARCH V1:46 repeat the no-hidden-repair-wave rule; D01 substrate cap (ledger:18); C-04 binding. Git tree clean (check 1) confirms zero product changes.

**14. Roadmap targets the Fault Discovery Agent — PASS.**
`ROADMAP.md`: Wave-1 DESIGN (corpus gap closure, RuleCard schema freeze, plant-test fixtures for G1/G4/G6/G7/G8, KC-2A/B pre-registration) + Wave-1 IMPLEMENTATION = build K1/K2/K3 per FAULT_DISCOVERY_MVP_ARCHITECTURE_V1, acceptance-gated by the 10-item rubric + per-detector MUST-PASS (lines 10–20). Substrate completion is an explicit non-goal (line 32; GOAL_BASELINE:17-18; D10); substrate work only S-gated Track-B pulled by measured misses. State target: `S0_APPROVED → READY_TO_DESIGN_WAVE1_IMPLEMENTATION` (line 3).

## Gaps

Only the explicitly registered Wave-1 design gaps and recorded measurement limitations — nothing else found:

1. **GAP-1** (`WAVE1_CORPUS_MATRIX.md:32`): the real/public-source fault case (C4) is not yet pinned to a concrete upstream fix + fixture; rubric item 2 unmet until frozen.
2. **GAP-2** (:33): the negative/no-fault control case (C6) is not yet designed; rubric item 5 unmet until added.
3. **GAP-3** (:34): at least one contract test must be reshaped into a full E2E case (or an equivalent planted case added) for the 4-case minimum; rubric item 1 unmet until then.
4. **Recorded measurement limitations** (registered, not silent): pragmatic custody with mandatory downgrade-and-record if Harness separation is impossible (ADR §4:53; FR-6:28; ROADMAP:44); S metric advisory until custody separation is demonstrated (FR-6:32); channel representativeness gap-3 open (FR-8:39; ROADMAP:39); U3/gap-2 multi-variant unmeasured (D03); KC-2A/KC-2B unmeasured by design (post-first-E2E / Wave 2); detector plant tests not yet run (M4 / O7 standing).

Minor non-blocking observations: (a) `state.json:56` retains the superseded `r8_user_acceptance` text, neutralized only via `s0_correction`; (b) the `goal_hash` recipe is undocumented and was not reproducible via plain SHA-256 over the goal strings — verbatim identity was verified textually instead.

## Verdict + conditions

**S0_APPROVED_WITH_EXPLICIT_GAPS.**

Rationale: all 14 checks PASS; no NO_SIGN_OFF axis fires (checks 1–3, 6, 7, 13 all clean; N/M fully retired from every governing document; no defective raw surface directly trusted). The only open items are exactly the registered Wave-1 design gaps (GAP-1/2/3) plus recorded measurement limitations — precisely the defined condition for this verdict. The S0 reconciliation faithfully implements the user's eleven decisions (a)–(k).

Conditions:
1. Record the state transition `S0_APPROVED → READY_TO_DESIGN_WAVE1_IMPLEMENTATION`; the project is never DONE.
2. Close GAP-1/2/3 in Wave-1 DESIGN before any measurement or acceptance claim; the corpus is not satisfied today.
3. Custody downgrades must be recorded, never faked, wherever Harness separation fails; S stays advisory until separation is demonstrated.
4. S0-Q1 freeze discipline binds absolutely once measurement begins (no case/oracle/Agent/fw-context modification to make a case pass; changes ⇒ new freeze/hash + new round).
5. Keep all fw-context repairs S-gated Track-B pulled by measured misses; no substrate wave before the first E2E case.
6. Bookkeeping (optional): annotate the superseded `r8_user_acceptance` field inline in `state.json`; document the `goal_hash` recipe.
