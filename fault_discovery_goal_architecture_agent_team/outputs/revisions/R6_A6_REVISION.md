# R6 Revision — A6 Evaluation Scientist (Convergence)

Inputs read: `outputs/R1_A6.md`, `outputs/DEBATE_ROUND_1.md` (incl. P3 reconciliation), `outputs/SCENARIO_STRESS_TEST.md` (GA-1..4), `outputs/RED_TEAM_REPORT.md` (V1..V9, O1..O10), `outputs/FACT_LEDGER.json`, `outputs/CONTRADICTIONS.json`. No other R6 files read.

## POSITION CHANGES

**PC-1 — GT protocol: commit-derived GT demoted from primary natural-fault source to minority source (V4). [PROPOSAL]**
What: I-A6-04b made public bug-fix diffs the natural-fault GT layer. Revised: ≥20% of GT rows must be non-commit-derived (built directly from oracle rules: clang -E truth, linker/ELF, fixture structure) and ≥1 case must be field-log-derived. Why: a fix diff encodes what a maintainer chose to change, not the full fault-mechanism space (survivorship bias). Feasibility: my R1 table I-A6-02 already constructs most rule types from non-commit oracles [FACT: F-A6-01..04], so this is a corpus-composition change, not a new oracle invention [INFERENCE]. Evidence: RED_TEAM §1 V4; revisit trigger = corpus authoring [FACT].

**PC-2 — GT trust: anti-tautology alone is insufficient; blind independent oracle + external replication adopted (V5). [PROPOSAL]**
What: I-A6-04c only extended ORACLE_POLICY:12 ("no self-derived GT"). Revised: ≥30% of GT rows must be authored blind (author sees no fw-context output, no Agent behavior) and a 10-case slice externally replicated (R3 gap 6). Why: lineage-authored GT recreates the same-lineage laundering failure; FR-R1 is the in-pack precedent of a self-authored expectation that was wrong [FACT: F-R0-11, RED_TEAM §1 V5]. V5's trigger binds me directly: before any S measurement counts [FACT].

**PC-3 — S metric: definition unchanged, protocol hardened (V6). [PROPOSAL]**
What: S = substrate-sufficiency with 3-bucket attribution (I-A6-06) stays verbatim. Added: (a) holdout custody split — fixture author ≠ holdout custodian ≠ scorer; (b) freeze-before-measure — corpus, seeds, rubric, ε thresholds frozen and hash-logged before any Agent run feeding an S-scored decision; (c) hold-one-out validation extended from guards (V6) to the S-audit rubric itself: drop one metric/bucket at a time and confirm the verdict does not flip. Why: S is the optimization target of the S3 gate; V6 rates gaming CRITICAL, and my metric set is exactly what a rater would Goodhart [INFERENCE].

**PC-4 — Level-1 gate redesigned after A4's UQ-1 answer: no G1-sufficient in-band per-variant provenance signal exists at 0.31.0. [FACT per round brief; consistent with F-V1-003 self-masking, F-R0-06]**
What: I-A6-04d's caveat-branch design implicitly assumed the substrate could help identify which variant's data a surface served. With no in-band provenance signal, scenario-01-class per-context claims cannot be caveat-scored in-band at all [INFERENCE]. Revised: the Level-1 gate remains an external golden-pack re-run at the scored HEAD [PROPOSAL], but per-context caveat adjudication must come from the external oracle (per-variant clang -E) or the surface is excluded from scoring. Combined with GA-4 [FACT: SCENARIO_STRESS_TEST §3], scenario-01-class fixtures (my R1 G03/G04-derived pair) leave every wave-1 corpus.

**PC-5 — Corpus sequencing adopts S-narrow + GA-2 + V8; wave-1 metric subset shrinks. [PROPOSAL]**
Wave 1 = {07, 08, PFC-1} + ≥1 macro-class case (V8; F-V1-001 macro observability has no guard, GA-1 [FACT: F-R0-16, RED_TEAM §2]) + config-class R2 rules first (ALT-S-narrow as sequencing constraint). Round 2 adds ≥1 planted quarantined-surface case (GA-2, scenario-06/F-V1-007-shaped) to break the S3 starvation loop. Wave-1 metric subset: hypothesis P/R (1), evidence anchors (2), FP control (5), unsupported-claim FAIL (6), reproducibility (8), mechanism class on PFC-1 only. Honest loss recorded: rule-predicate truth-table scoring (4) binds only on config-class rules; causal-chain scoring (3) has no wave-1 home — the call-graph differentiator is untested in wave 1 [FACT: RED_TEAM §2 ALT-S-narrow].

**PC-6 — Absence adjudication gets a named owner and procedure inside the S-audit (P3/UQ-6). [PROPOSAL]**
Every "substrate-fact-missing" verdict in an S audit requires a logged retrieval transcript (surface, query, params, response) and adjudication by a role distinct from fixture author and Agent developer. Default rule: ambiguous absence counts as PRESENT for S. Direction: inflating substrate-missing inflates the substrate-fix mandate — the sunk-cost pull of C-04 — so the ambiguity default must favor the Agent, not the substrate [INFERENCE from C-04]. This mirrors C-03: an absence claim is a claim, not a neutral observation [FACT: CONTRADICTIONS.json C-03].

## POSITION UNCHANGED

**PU-1 — Two-level evaluation, strictly separated (I-A6-01). [UNCHANGED]** Reinforced: R4's program-wide finding that scenario-03's runtime ceiling bounds every strategy [FACT: SCENARIO_STRESS_TEST §2], and C-01's scope split between the two readiness verdicts [FACT: F-R0-19].

**PU-2 — S as the decision instrument with pre-registered thresholds (I-A6-06). [UNCHANGED]** Only its protocol changed (PC-3/PC-6). Local-optimum criteria stand: dominant bucket = agent-missed-despite-facts-present AND marginal substrate gain < ε.

**PU-3 — Scenario verdicts 07/08 usable-as-is; scenario 03 two-part contract. [UNCHANGED]** GA-3 confirmed the facts+forbidden-overclaims contract by all three stress passes [FACT: SCENARIO_STRESS_TEST §3]; the narrow corpus is consistent with my R1 verdicts.

**PU-4 — Planted mutations as MVP method of record; PFC-1 as first public e2e case. [UNCHANGED]** PFC-1's rule predicate is source-only line ordering, its substrate surface passed (V2-Z3 [FACT: F-A6-07]), and GA-3 does not displace it because its evidence chain needs no runtime truth. V4 changes the natural-fault layer's composition, not the planted layer's role.

**PU-5 — Metric 6 (unsupported definite claim ⇒ scenario FAIL) and the reproducibility stance (score only deterministic Level-1 surfaces; record the exclusion). [UNCHANGED]** UQ-1's answer strengthens the exclusion: provenance is not retrievable in-band at all.

## REMAINING OBJECTIONS

**RO-1 (O9) [UNCERTAINTY]** S stability unmeasured: narrow-corpus S vs 12-fixture dev-set S; Δ>20 invalidates S3's start (R3 gap 5). Until measured, S numbers from either corpus are not comparable and gate nothing.
**RO-2 [ASSUMPTION at risk]** A-A6-03 (holdout can be sealed in a single repo) is weakened by V6's custody-split requirement: no external custodian exists in this pack. If custody cannot be split, S is advisory-only.
**RO-3 [UNCERTAINTY]** Authoring capacity for the V4/V5 additions (≥20% non-commit rows, ≥1 field-log case, ≥30% blind rows, 10-case external replication) is unbudgeted; blind rows exceed the 1–2 dev-days/fixture family I assumed (A-A6-02).
**RO-4 [UNCERTAINTY]** Mechanism-class taxonomy unification (C-05 convergence path; UQ-6 per-rule-type metric binding + missing GT row) is unadjudicated; metric 3 and PFC-1 mechanism-class scoring cannot freeze before it lands.
**RO-5 [INFERENCE]** V9 applies to my own claims: 12-fixture results generalize only to the planted/synthetic distribution with scope stamps per surface; no "firmware/software" generalization survives wave 1.

## ASKS TO CONTROLLER

**ASK-1 (O9) [PROPOSAL]** Commission the S-stability run before any S-gated decision: same protocol, narrow corpus {07,08,PFC-1,+macro case} vs 12-fixture dev set; Δ>20 pre-registered as the S3-start invalidator.
**ASK-2 (O10) [PROPOSAL]** Appoint the gaming-freeze owner with three separated roles (author / custodian / scorer). If no third party exists, custodian = A0 with a written custody log; freeze = hash-logged corpus+seeds+rubric before the first S-scored run.
**ASK-3 [PROPOSAL]** Authorize blind-oracle resourcing: ≥30% blind GT rows + the 10-case external replication executed outside the fixture-authoring lineage. Without it, per V5's trigger, no S measurement counts.
**ASK-4 [PROPOSAL]** Rule on holdout-custody feasibility in-repo. If a split is infeasible, declare S advisory-only until an external custodian exists — S must not gate substrate repair mandates in the interim.
**ASK-5 [PROPOSAL]** Ratify the absence-adjudication owner (PC-6) and its ambiguity-default rule before the first S audit; schedule the C-05 mechanism-class adjudication before PFC-1 scoring freezes.
