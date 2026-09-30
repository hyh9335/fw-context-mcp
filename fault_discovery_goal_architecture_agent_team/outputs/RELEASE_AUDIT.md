# Release Audit (R8) — Independent Auditor

Auditor: independent, not involved in producing any pack artifact. Read-only; the only file written is this one.
Evidence base: pack files + `.fw-validation/` (incl. `findings.json`, `FINAL_VALIDATION_RESULTS.json`). Product head d8f08a4.

## Checklist

**1. Goal preservation — PASS.**
GOAL_BASELINE.md:4 quotes the goal verbatim: "构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。" — identical to the authoritative statement (ENTRYPOINT_T14.md:11 English equivalent; FACT_LEDGER `goal_hash` frozen at R0). GOAL_BASELINE:15 forbids "goal replacement or silent narrowing (any scope stamp must cite the goal item it serves)". ROADMAP.md:32 ("scope stamp cites goal item 1+2"), :44 ("S0 gate rejected/amended ⇒ all scope stamps re-derived"), and D10 item (8) ("it remains the reference frame") cite rather than redefine. No substitution found in any final artifact.

**2. R1 independence — PASS.**
8 reports show 8 distinct voices: A1 git forensics (R1_A1.md:7-16, 945-commit histogram, 0 "fault" grep); A2 domain research (R1_A2.md:18-21, IRIS/FMEA/MISRA external sources); A3 code-level rule taxonomy with src file:line cites (R1_A3.md:7-14); A4 auditor with C-01 adjudication + blocking criterion (R1_A4.md:18-20); A5 component decomposition (R1_A5.md:21-24); A6 oracle/GT design (R1_A6.md:7-16); A7 lineage/kill-criteria statistics (R1_A7.md:5-7); A8 four alternative architectures (R1_A8.md:3). Receipts `outputs/receipts/R1_A1..A8.json` all exist; R1_A7.json well-formed (inputs/outputs/evidence_paths/claims_fact_ids/uncertainties).

**3. ≥3 real strategies — PASS.**
S1 = repair-first "certify kernel" (STRATEGY_S1.md:3, 11-13) — the strongest steelman of the sunk-cost pull, honestly self-labeled "HIGHEST local-optimum risk" (:29); S2 = Agent-first skeptic, untrusted substrate, repair only on measured misses (STRATEGY_S2.md:3, 21); S3 = dual-track with S-gate/drop-rule and Tier-2 quarantine (STRATEGY_S3.md:3, 7). Genuinely different architectures and sequencing, not strawmen; R4 stress-tested all three (SCENARIO_STRESS_TEST.md:7-16).

**4. Position changes traceable — PASS.**
R3: S1 disqualified on evidence (AG-2, DEBATE_ROUND_1.md:7); S2-vs-S3 "differ in kind" unresolved → measurement (:18). R4: GA-1..GA-4 adjustments registered (SCENARIO_STRESS_TEST.md:27-30) — starvation-loop and guard-coverage gaps force corpus/plant-test changes. R5: guards-vs-contracts self-correction overturns S2's zero-change claim (RED_TEAM_REPORT.md:23); S0 gate added (:20). R6: A2 conditionally withdraws IRQ-first (R6_A2_REVISION.md:11 PC-3), re-tiers E1-E7 (:7 PC-1); A6 demotes commit-GT to ≤80%, adopts blind oracles (R6_A6_REVISION.md:7-11). Movement is amendment, not restatement. Fact spot-checks (3): (a) self-masking F-V1-003 / get_source(variant='B') inactive body → F-R0-06 (FACT_LEDGER:60) ✓ matches FINDINGS.json F-V1-003 evidence; (b) dead-code live-chain misreport, manual.py:237-243 → F-R0-07 (FACT_LEDGER:61) ✓ matches F-V1-005; (c) FR-R1 self-authored-expectation precedent → F-R0-11 (FACT_LEDGER:65) ✓. Bonus: F-R0-03/04 verified directly against FINAL_VALIDATION_RESULTS.json:45-85 (43/7/5/2/1 of 58; the 7 FAIL_PRODUCT cases) — the ledger is truthful to its source.

**5. Real red team — PASS.**
RED_TEAM_REPORT.md:7-17: V1–V9 each with severity, mitigation, revisit trigger. The winner (S2+S3 hybrid) was attacked: V2 hits its guard/export cost (:10), V6 its S+guards gaming surface (:14), V7 its 9-component complexity (:15), V8 its narrow-corpus starvation (:16); A8's guards-vs-contracts correction (:23) is an attack on the hybrid's core mechanism, and O1–O10 remain explicitly unresolved (:31). Flip conditions for the adopted shape pre-registered (:27).

**6. Minority report — PASS.**
MINORITY_REPORT.md preserves 9 positions with triggers. M1 (A2) matches R6_A2_REVISION.md RO-4 (:33: ceiling bounds what source proves, not R-O importance; silent-narrowing risk at S0) + PC-5; re-open trigger = family-#2 / rubric re-weighting — consistent with D-1 (DEBATE_ROUND_2.md:15) and D10 dissenters. M3 (A6) matches R6_A6_REVISION.md RO-2 (:40) + ASK-4 (:50: custody infeasible ⇒ S advisory-only); trigger = custody ruling, matching D-3 and D08's advisory clause. No suppression: dissenters also named in D07/D10.

**7. Roadmap traceability — PARTIAL.**
Every ROADMAP item traces to a decision: S0 gate→D09; contract spec→D06; K1/K2/K3→D09; corpus→D07; measurements→D08; Track-B S-gated queue→D02; non-goals→D10; kill triggers map to D01/D02/D03/D05/D07/D08 revisit triggers. Fact-ID resolution: all F-R0-* IDs exist in FACT_LEDGER.json; C-* exist in CONTRADICTIONS.json; F-V* exist in .fw-validation/findings.json (a documented parallel citation space — R1_A2.md:3). **Defect**: D07 (DECISION_LEDGER.json:101) cites `F-V2-Z3-PASS-SHA`, an ID defined in NO ledger (grep: only occurrence is the citation itself). Its substance is real — V2-Z3-ZEPHYR-WORKQ = PASS with artifacts in FINAL_VALIDATION_RESULTS.json:194-199 — so not a fabricated cite, but a dangling undefined label. One ID in 24 decision citations.

**8. No bug-fix-because-test-failed — PASS.**
EVIDENCE_RULES.md:18: "Never make a fw-context bug roadmap-critical unless its impact on fault/rule discovery is explained." C-04 binding rule (CONTRADICTIONS.json:45); GOAL_BASELINE.md:41 ("C-04: fix-impact rule binds all substrate work"); D01 "substrate cap: 1 wave-equivalent until first e2e case"; D02 Tier-2 re-earn ONLY via S-gated Track-B with guaranteed corpus coverage; ROADMAP.md:21 "Each fix: impact statement + 58-case campaign re-run (per-fix-wave rule) + Level-2 attribution before merge"; ROADMAP.md:26 fixes only "if plant-test fails"; D10 non-goal (2) green tests. Zero mandates exist to fix product code merely to make tests green; the repair-first S1 was explicitly rejected.

**9. Disposition of ALL current findings — PASS.**
`.fw-validation/findings.json` holds 15 IDs (task said 14; F-V1-002/007 are commonly grouped). Each is dispositioned:
- Tier-2 quarantined/S-gated (CURRENT_CAPABILITY_MAP.md:17-22): F-V1-003, F-V1-001, F-V1-007, F-V1-004, F-V1-005, F-V2-001. F-V1-002 is dispositioned through C-02 RESOLVED_BY_AMENDMENT (CONTRADICTIONS.json:16-24; broadened into F-V1-007's single-line class — same G03 defect) and grouped F-V1-002/007 in A4's classification (R1_A4.md:9).
- Wave-2 S-gated repairs: F-V2-001 → F-V1-003 → F-V1-007 → F-V1-005 (ROADMAP.md:21); Track-B queue adds F-V1-001, F-V1-004 (:25).
- Not-scheduled: F-V1-006 (NEUTRAL, caveat-bound, G9 slot probe in wave 1), F-V1-008 (contract spec + G7; fix only if plant-test fails), F-V2-002 (contract spec + G6), F-V4-001 (long-session watch, M8) (ROADMAP.md:26; D05/D06; MINORITY_REPORT M8).
- Environment V0 findings: F-V0-001/002/004 RESOLVED, F-V0-003 OPEN — all accounted as environment constraints via F-R0-14 (FACT_LEDGER:68, source cites "FINDINGS.json F-V0-001..004"); no roadmap action required or claimed.
No unaccounted findings.

**10. Source-only limits — PASS.**
EVIDENCE_RULES.md:18 (green tests ≠ semantic truth without independent oracle) + label regime (:3-16). GOAL_BASELINE R-3 (harness honesty; S advisory until custody ruled) + R-2 (zero silent-error findings). CURRENT_CAPABILITY_MAP.md:32-33 "What the substrate CANNOT provide at any trust tier": runtime queue semantics, linker-script ground truth without the script, multi-variant truth pre-U3, in-band macro values — mapped to Agent-side contract obligations (FR-7 in FAULT_DISCOVERY_REQUIREMENTS.md:30-31: forbidden-overclaims list, "runtime exhaustion cannot be concluded from source").

**11. Decision revisit triggers + dissenters — PASS.**
All D01–D10 carry concrete revisit triggers (D01 KC-2 grep-arm≥substrate-arm / replication refutes FAIL_PRODUCT; D02 plant-test <6/7 or gap-3; D03 U3 gap-2; D04 family-#2 re-earn; D05 any wave-2 vector/dead-code scenario; D06 contract row unimplementable; D07 PFC-1 unbuildable or Δ>20; D08 custody infeasible; D09 S0 rejected / KC-2 / Tier-1 e2e failure; D10 user amends goal / U3 re-opens (4)). Dissenters recorded where they exist: D07 (A2, A8), D10 (A2); all other substantive dissent preserved in MINORITY_REPORT M1–M9 rather than erased.

## Unaccounted findings

None. All 15 finding IDs in `.fw-validation/findings.json` (F-V0-001..004, F-V1-001..008, F-V2-001/002, F-V4-001) are dispositioned per item 9. Two bookkeeping notes, non-blocking: (a) findings.json holds 15 IDs, not the 14 stated in the audit brief (F-V1-002/007 grouping); (b) the undefined citation label F-V2-Z3-PASS-SHA (see item 7).

## Verdict + conditions

**APPROVED_WITH_OPEN_DECISIONS.**

Rationale: no checklist item FAILs on the NO_SIGN_OFF axes (goal preservation, minority suppression, fabricated evidence cites, missing finding disposition). All open items are explicitly gated, not silently assumed: S0 user gate precondition to any wave (D09 step 0); custody ruling before S gates anything (D08, M3); gap-2/3/5/7 measurements pre-registered with invalidation thresholds; UQ-2 N/M deferred to the S0 gate; C-01 downgraded to hypothesis status pending V6-disciplined measurement.

Conditions:
1. **Fix the dangling citation**: replace or define `F-V2-Z3-PASS-SHA` in D07 (DECISION_LEDGER.json:101) — e.g., register it as a FACT_LEDGER fact or cite `FINAL_VALIDATION_RESULTS.json V2-Z3-ZEPHYR-WORKQ PASS` directly. Do this before the pack is archived as decision record.
2. **Carry the 15-vs-14 count forward**: any downstream consumer of the audit brief should reconcile against findings.json (15 IDs; F-V1-002 subsumed by F-V1-007 per C-02).
3. **Open-decision register stands**: the pack's own gates (S0, custody ruling, gap measurements, UQ-2/UQ-3) must complete before any implementation wave; ROADMAP Wave-1 exit conditions are binding, not advisory.
