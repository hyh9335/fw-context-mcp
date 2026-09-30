# R3 — A7 Critique of Strategies S1/S2/S3

Reviewer: A7 (RED_TEAM). Basis: FACT_LEDGER.json, CONTRADICTIONS.json, R1_A7.md, STRATEGY_S1/S2/S3.md, ANTI_ANCHOR.md. Isolation kept: no other R3_*/roles/debates files read.
META-CAVEAT (FACT): S2 and S3 cite A7's own R1 record as evidence. My critique therefore partially audits my own words — a laundering vector in itself (see Verdicts). Every attack below is re-derived from ledger fact IDs (F-R0-xx, C-xx), not from my record's authority.

## S1 critique

**1. Strongest claim.** (INFERENCE from FACTs F-R0-02, F-R0-06) Only substrate repair removes the defect class a consumer cannot defend against: F-V1-003 is self-masking (the substrate's own consistency tools validate the collapsed data), so no Agent-side guard can bound INDEX_DIVERGENCE from inside. S1 is also the only strategy whose success bar is semantic correctness, and the only one that can retire C-03's absence-ambiguity at the root rather than cross-checking around it.

**2. Strongest evidence FOR (steelman, honest).**
- FACT F-R0-06: get_source(variant='B') served an INACTIVE branch as live with no warning — measured, not hypothetical. Consumer-side defense (S2's G1–G7) is structurally incomplete for exactly this class.
- FACT F-R0-11: the repair wave fixed 5 real product defects (FR-NEW-1..5) in one wave — repair capacity demonstrably exists and is fast in this codebase's hands.
- FACT F-R0-14: Windows lacks clang (WSL only). S2/S3's independent channel is not free here; distrust is only cheap if verification is available in the measured environment.
- FACT C-01/F-R0-02: 7 FAIL_PRODUCT classes incl. CRITICAL INDEX_DIVERGENCE. Feeding an Agent from a source known to lie about variants, then calling its output "auditable," risks the goal contract's core promise.

**3. Sharpest attack.** S1 reenacts Bias 1 and Bias 5 with the evidence still warm: FACT F-R0-18 (0 Agent lines / 945 commits), C-04 OPEN — and S1's own text concedes "Local-optimum risk: HIGHEST of the three." A strategy that self-scores worst on the anti-anchor ledger owes an offsetting gain; its gain sits behind its own two-wave gate. Evidence it ignores: F-R0-18, C-04, A4's classification (only F-V1-003 is BLOCKING), and the A0 rule it violates by mandating all 8 findings while citing EVIDENCE_RULES itself (FACT: F-V1-004/005/008 have no stated fault/rule-discovery impact). Deeper (INFERENCE): S1's gate — "READY under the same verdict rule" — re-trusts the same self-authored verdict machinery that produced two conflicting verdicts (C-01) and defended a wrong test expectation for ~2 release cycles (F-R0-11, FR-R1). Certification here is one lineage grading its own fixtures (I7/I8): passing re-certifies the lineage, not truth. It also defers the first datapoint on the goal's central uncertainty (U1) past all substrate work — maximal time-to-evidence.

**4. Switch condition.** (PROPOSAL) I switch to S1 if: (a) a measured A/B shows the same Agent harness achieves materially higher S on certified substrate than on S2's guarded PASS surfaces, with the gap exceeding the repair cost in weeks; or (b) R4 empirically shows consumer-side cross-checks (per-variant rebuild + clang -E) cannot detect a planted F-V1-003-class divergence — the steelman's structural claim borne out.

**5. Kill criteria (A7-attached).**
- K1-S1: no first line of Agent code after one repair-wave-equivalent of effort → the 945/0 pattern reenacted → kill.
- K2-S1: wave 2 reopens ≥2 previously fixed classes (FR-NEW-1..5) or the revalidation verdict flips again → the certification loop is non-convergent → kill.
- K3-S1: revalidation passes on the unchanged 58 self-authored cases/oracles → the verdict has no external validity → "certified ground" is void and S1 collapses into S2-with-delay.

## S2 critique

**1. Strongest claim.** (INFERENCE from FACTs F-R0-18, F-R0-13) The highest-value next commit is Agent code: goal progress is 0%, the MVP's needed surfaces are measured-PASSING, and only F-V1-003 is BLOCKING — so distrust guards convert 6+ degrading findings into operating rules with zero product changes and near-zero time-to-first-goal-evidence.

**2. Strongest evidence FOR (steelman, honest).**
- FACT F-R0-18 is the cleanest fact in the ledger; any strategy deferring Agent code reenacts I1/I5 (quantified ∞ substrate:goal investment ratio).
- FACT F-R0-13: the PASS set is measured, not asserted (call graph incl. function-pointer edges V2-F1/F2, conditional regions with branch_states, warm ~15 ms stdio).
- Each guard G1–G7 maps 1:1 onto a measured defect class (F-R0-04..F-R0-09) — distrust is evidence-based, and polarity asymmetry answers C-03's absence-ambiguity at the consumer layer.
- The S-metric is measurable now: S2 buys the earliest falsifiable signal on the goal's central question; S1 defers it behind two waves.

**3. Sharpest attack.** The independent verification channel is asserted, never costed or proven: FACT F-R0-14 — clang exists only on WSL, so C2's "macro truth via clang -dM -E" runs under a DIFFERENT toolchain than the MSVC-only corporate builds it must referee; its truth is clang's truth, not the build's (UNCERTAINTY, decision-critical; if unrepresentative, F-V1-001 is unmitigated and config-conditions evidence starves). Second: G1's permanent single-context ban converts F-V1-003 into a permanent capability hole exactly where the goal's variant/configuration mechanisms live; "deferred" has no re-entry path (ASSUMPTION: variants won't matter soon — untested, U3). Third, sharpest: the skeptic harness is written by the same lineage that wrote the substrate, fixtures, and oracles (FACT 938/945 commits, I8) — S2 launders distrust into confidence; "harness green" is a new green suite, and F-R0-11 (FR-R1) proves this lineage's tests can defend wrong semantics for release cycles. Bias 2 reenacted at the Agent layer (INFERENCE).

**4. Switch condition.** (PROPOSAL) I move to S2 outright if my S1 kill criteria fire. I prefer S2 over S3 if R4 shows S on its 12-fixture dev set predicts held-out behavior (no narrow-vs-broad gap) and the independent channel is proven representative on real builds.

**5. Kill criteria (A7-attached).**
- K1-S2: dev-set S < 50% after the first corpus AND the re-scope is adjudicated only by the builder lineage → premature-MVP confirmed → kill or externally adjudicate.
- K2-S2: >10% of sampled substrate negatives prove false in operation (KC-3) and verification overhead exceeds measured substrate value → demote/drop the substrate.
- K3-S2: C-01 unadjudicated at R7 while S measurements rest on self-declared "PASSING surfaces" → every S number is void → rebuild measurement on a two-tier or externally replicated footing.
- K4-S2: no representative macro/condition evidence channel within the first sprint (KC-1, given F-R0-14) → the config-conditions goal item starves → that item must move to a repair track (i.e., become S3).

## S3 critique

**1. Strongest claim.** (INFERENCE) The S-gate is the only structural mechanism on the table that mechanically enforces C-04/A0 (every fix must state fault/rule-discovery impact) and operationalizes the anti-sunk-cost lever (drop rule). Where the bias is structural (I1/I5), a norm fails and an admission gate binds; neither track defines its own success.

**2. Strongest evidence FOR (steelman, honest).**
- C-04 is OPEN and standing: S1 ignores it, S2 applies it only to fixes (weaker), S3 makes it an admission criterion.
- FACT F-R0-06: S3 keeps a standing repair path for the one defect (F-V1-003) that consumer guards provably cannot bound — steel from S1 where it is justified, without S1's full-surface mandate.
- Two-tier trust matches the fact granularity better than either single verdict: some surfaces measured-PASS (F-R0-13), others measured-FAIL (F-R0-04); blanket "untrusted" (S2) and blanket "certify-all" (S1) are both coarser than the evidence.
- A6's scenarios 07/08 usable-as-is give the fastest pipeline-exercising start without touching a defective surface.

**3. Sharpest attack.** Dual-track is the classic back door for Bias 1: Track B gets a mandate, a week-0 spike, and a budget, and this lineage's history is precisely "the mandated track absorbs everything" (FACT F-R0-18: 945/0). The lineage has never run two tracks in parallel (ASSUMPTION it can — untested), and the idle-kill relies on self-reporting. Second (INFERENCE): the S-gate is inert at start — it consumes Track A's S measurements, which do not exist until the corpus and harness land; in that window Track B's spike and export are built on judgment alone, exactly the ungated substrate work C-04 warns about. Third: a 3-case first corpus (07+08+PFC-1) maximizes single-corpus overfitting (I7): author == fixture author == oracle author == product author (FACT 938/945), one host (F-R0-14) — S2's 12+4+sealed holdout is strictly stronger on I7; S3 trades corpus validity for speed. Fourth: "neither track is trusted to define success for itself" names no third party — the arbiter is the same lineage, and two tracks citing each other's outputs enlarge the laundering surface (I8).

**4. Switch condition.** (PROPOSAL) I switch to S3 if: (a) S on {07,08,PFC-1} tracks S on the 12-fixture dev set within ~10 points (narrow corpus is a valid leading indicator); (b) the S-gate demonstrably binds — at least one Track B item rejected for missing Level-2 attribution; (c) R4 shows F-V1-003-class divergence on real build systems, making S2's permanent ban untenable.

**5. Kill criteria (A7-attached).**
- K1-S3: either track idles >1 cycle measured by CALENDAR, not self-report → collapse to the single track with the stronger S evidence, ignoring sunk work.
- K2-S3: any Track B fix lands without a Level-2 miss attribution → the gate is decorative → revert to S2's posture.
- K3-S3: |S(narrow) − S(dev set)| > 20 points → the first corpus is decision-invalid → expand before any Track B admission.

## Cross-strategy verdicts

- **Zero-sunk-cost test.** R1's answer (KEEP concept, INVERT decomposition, commission a ~1/3-surface focused provider) negates S1 and is implemented by both S2 and S3. Between them: from scratch one would not build blanket distrust around a substrate one just chose; one would tier trust by evidence — S3's two-tier model fits the fact shape (F-R0-13 vs F-R0-04) better than S2's blanket-untrusted. VERDICT (INFERENCE): S3 ≳ S2 ≫ S1 on zero-sunk-cost fit.
- **Bias ledger.** Bias 1 completion: S1 (own admission) > S3 (Track B back door) > S2. Bias 2 test-green: all three reenact it — S1 in revalidation, S2/S3 in "harness green"; only S3's S-gate targets it structurally. Bias 5 sunk cost: S3's drop rule strongest, S2's S-gating second, S1 none. Bias 6 premature MVP: S2/S3 armed with S-tripwires; S1 immune by construction but pays maximal time-to-first-evidence. Bias 7 single corpus: S1 worst (same 58 cases/oracles), S3 worst among Agent-first (3 cases), S2 best. Net on the full ledger: S2 is the most per-bias-robust; S3 owns the strongest single structural lever. No clean winner. (INFERENCE, grounded as cited.)
- **Consensus laundering.** S1 is most exposed at the VERDICT level — its deliverable IS a verdict, and same-lineage revalidation of same fixtures is one data point dressed as corroboration (I8; C-01 proves this lineage issues conflicting verdicts). S3 is most exposed at the PROCESS level — the most internal sign-off points (two tracks, gate, arbiter) and cross-citation masquerading as independence. S2 is least exposed structurally (one metric, one channel) but its harness is lineage-written too. FACT: zero external replication exists anywhere (F-R0-14: one host; I8). Forced to name one: S1. (INFERENCE)
- **S2 vs S3 — kind vs degree.** In degree: corpus breadth, substrate posture, sequencing. In KIND they differ on exactly one axis: WHO may repair the substrate and WHEN — S2 makes repair a measured consequence (only S-attributed misses, ever); S3 makes repair a gated mandate (a standing owner on a schedule). Everything else is implementation. (INFERENCE) The R7 choice therefore reduces to one question: does F-V1-003 — the only BLOCKING-class defect, self-masking (F-R0-06), consumer-unbounded — get a standing repair mandate BEFORE measurement? U3 answers it, and the strategies differ in kind exactly where the evidence is missing.

## Decision-critical gaps for R4

1. **U1/KC-2 baseline — is the substrate necessary at all?** Every strategy assumes an unmeasured answer. Experiment: pre-registered A/B on ≥30 real bugs from the 5 RTOS repos' fix histories — arm A = harness + guarded fw-context; arm B = grep + rebuild + clang -E/-dM + LLM; same corpus, blind oracles, pre-registered precision/recall margin on fault mechanisms. Closes GOAL_CONTRACT's "necessary vs optional" and the S1-vs-rest axis.
2. **U3 — is F-V1-003 confined to the bare generator?** Experiment: index ~20 real multi-variant projects (Zephyr, CMake multi-config, PlatformIO) with planted divergent defines; diff per-variant substrate output against per-variant clang -E ground truth. ≥1 real-system hit → S2's ban is insufficient and S3's mandate wins; 0/20 → S2's operating rule suffices. Decides the S2/S3 kind-question.
3. **Independent-channel representativeness on the primary environment.** Experiment: measure wall-clock and success rate of the C2 channel (clang -dM -E under WSL vs MSVC-only builds, per-variant rebuild, grep sweeps) against the 7 FAIL_PRODUCT classes within Agent-latency budgets (FACT F-R0-13 gives the 5000 ms timeout reference). Unrepresentative or too slow → S2/S3 guards degrade → S1 strengthened.
4. **Guard effectiveness — does distrust actually catch the defects?** Experiment: plant the 7 FAIL_PRODUCT defect patterns as adversarial fixtures; run G1–G7; count detections. Misses >2/7 → the S2/S3 core mechanism fails → substrate repair becomes mandatory, not optional.
5. **S-metric stability: narrow vs broad corpus.** Experiment: same harness, S on {07,08,PFC-1} vs S on the 12-fixture dev set. Δ>20 → S3's first corpus is decision-invalid; Δ≤10 → S3's fast start validated.
6. **C-01 adjudication input.** Experiment: external replication of a 10-case slice of the 58-case campaign (fresh lineage, or oracles authored blind to product behavior). FAIL_PRODUCT set substantially confirms → S1's certification premise has legs; substantially oracle-defects → S2/S3's PASS-surface premise confirmed. Cheapest experiment that moves the S1 axis.
7. **Interface-decay dominance (I9) and the export bet.** Experiment: classify all open findings by root class (data-model vs interface-decay vs contract); cost the raw-evidence export (macros + single-line bodies) vs wrapper-guards per finding. Export closes ≥3 findings cheaply → S3's "first candidate" claim and S2's step-5 bet confirmed; otherwise cut the export thread everywhere (anti-completion lever).

— A7, R3. Only this file written; no product source or pack file modified.
