# R5 — A7 Strategic Red-Team Attack Set

Author: A7 (RED_TEAM), leading R5. Attacks target S1/S2/S3 and the post-R4 synthesis (S2/S3 frame + GA-1..4). Labels per workflow/EVIDENCE_RULES.md. Read inputs: ANTI_ANCHOR, EVIDENCE_RULES, R1_A7, DEBATE_ROUND_1, SCENARIO_STRESS_TEST, STRATEGY_S1/S2/S3, FACT_LEDGER, CONTRADICTIONS. No other R5 files read; no product or pack file modified.

## ATTACK VECTORS

### V1 — SUNK COST
- (a) [INFERENCE] The pack's decision frame is still substrate-shaped, so the "Agent-first inversion" is claimed, not enacted. The R4 scenario table is indexed by the substrate's own FAIL_PRODUCT findings; the UQ list is dominated by substrate questions; S2's Agent decomposition is A5's substrate-MVP decomposition verbatim (DEBATE AG-2 note); and the one axis on which S2 vs S3 "differ in kind" (DEBATE kind-question) is *who repairs the substrate, when* — the debate orbits the substrate even while opposing S1. Sunk cost also hides inside new vocabulary: monthly S-gates can keep Track B alive on unattributed momentum; "hardening" and "export" are substrate waves wearing consumer clothes.
- (b) FACT F-R0-18 (0 Agent code), R1-I1 (∞ substrate:goal ratio), F-R0-02..05 (NOT_READY, 7 FAIL_PRODUCT); FACT C-04 (standing anti-anchor tension); R4 §1 table indexed by F-V* findings.
- (c) Hits S1 directly; hits the post-R4 synthesis structurally (substrate-indexed frame).
- (d) Severity CRITICAL — it is the project's own documented failure mode (A1-F3) replaying at strategy level.
- (e) [PROPOSAL] Mitigation: pre-register KC-2 A/B (DEBATE gap 1) as a decision gate BEFORE any Track-B wave-2 item; auto-strike any roadmap line naming an F-V*-fix that lacks a fault/rule-discovery impact sentence (A0 rule, C-04); cap substrate effort at one wave-equivalent until the first end-to-end Agent case closes.
- (f) Revisit trigger: a KC-2 run where the grep+build+LLM arm ≥ substrate arm; or two consecutive cycles in which every S-attributed miss resolves to a non-substrate cause.

### V2 — UNNECESSARY FW-CONTEXT WORK
- (a) [INFERENCE] Two forms of unnecessary substrate work survive inside S2/S3. First, consumer-side substrate work in disguise: G1–G7 re-implement substrate duties at the Agent layer, unpriced and unexpiring. Second, the raw-evidence export is asserted "highest-leverage" (S2/S3) but never costed against the independent channel it duplicates: macros are stored but unexposed (F-R0-16), and `clang -dM -E` may serve goal item 3 cheaper — no measurement exists (UQ-3, DEBATE gap 7). Worse, GA-1 shows the guard set does not even cover the macro class, so guards+export is double spend on an unmeasured channel. FACT (R4 §2): every substrate fix-wave forces a 58-case campaign re-run — a revalidation cost multiplier no strategy prices.
- (b) FACT F-R0-16; GA-1; R4 §2 revalidation cadence; UQ-3; DEBATE gap 7 (export must close ≥3 findings cheaply or be cut).
- (c) Hits S2/S3 hardest (both commit to export + guards pre-measurement); S1 maximally by definition.
- (d) Severity HIGH — bounded spend, but unmeasured necessity violates the pack's own KC-2 standard.
- (e) [PROPOSAL] Run DEBATE gaps 7 and 3 BEFORE admitting export into any roadmap; give every guard an expiry condition (guard retires when its fenced finding is fixed and revalidated); record the campaign re-run cost inside each Track-B item's impact statement.
- (f) Revisit trigger: measured `clang -dM -E` latency within budget on the corporate host → demote export to optional; a gap-7 result closing ≥3 root classes cheaply → export promoted.

### V3 — MISSING DATAFLOW/STATE REASONING
- (a) [INFERENCE] No strategy owns runtime semantics. The scenario-03 ceiling (R4 §2) is program-wide: no substrate repair yields queue/resource runtime semantics, and GA-3 lowers the ambition to emission-site facts + forbidden-overclaim contracts. There is no bounded-execution/emulation leg anywhere, although the campaign's own QEMU case was never run (V2-F3 NOT_RUN, F-R0-03). The goal demands "causal mechanism and execution/configuration conditions" — the *execution* half is structurally unowned by every strategy; static call-graph + condition facts are being quietly sold as mechanism reconstruction.
- (b) FACT R4 §2 scenario-03 ceiling + GA-3; FACT F-R0-03 (V2-F3 NOT_RUN); FACT F-R0-13 (call graph incl. indirect edges — static only).
- (c) Hits ALL strategies equally; most damaging to rule quality for race/ordering/queue fault classes.
- (d) Severity HIGH — GA-3 contains the overclaim, but the goal slice is silently de-scoped.
- (e) [PROPOSAL] Add a minimal dynamic leg (harness-driven unit execution or QEMU for one RTOS) as an explicitly tiered track item; until it exists, every runtime-class RuleCard carries the GA-3 two-part label; forbid presenting static emission-site facts as mechanism reconstruction.
- (f) Revisit trigger: first corpus round where runtime-class rules fall below the pre-registered precision floor; or any RuleCard whose conditions cannot be falsified statically.

### V4 — SOURCE-ONLY LIMITS
- (a) [INFERENCE] All strategies are source-only, but the goal asks for *operationally useful* rules, and all GT derives from post-hoc fix commits of 5 RTOS repos. [INFERENCE] Commit-derived GT carries survivorship bias: bugs never fixed, fixed without commits, or never noticed by this lineage are invisible — rules learn "what this lineage noticed", not "what faults exist". All evidence comes from one corporate Windows+WSL host with a proxy/TLS-shaped environment (F-R0-14); "firmware/software" in the goal has only ever meant embedded RTOS (R1-A2, I7).
- (b) FACT F-R0-14, F-R0-18; R1-I7/A2; DEBATE UQ-2 (GT provenance unresolved).
- (c) Hits S2/S3 hardest (KC-2 and S are built on commit-derived GT); S1 equally exposed on the one-host campaign.
- (d) Severity MEDIUM — degrades external validity of every downstream claim; measurable.
- (e) [PROPOSAL] Add ≥1 non-commit-derived fault source to the corpus (field log/trace-derived fault, or a bug thread with repro steps); require ≥20% of KC-2 GT rows to be non-commit-derived; stamp host-environment variance from day one.
- (f) Revisit trigger: first held-out run in which a mechanism cannot be verified from source alone; first non-embedded target appears.

### V5 — WEAK GT (GROUND TRUTH)
- (a) [INFERENCE] The entire measurement apparatus stands on self-authored GT: expected.json oracles written by the same lineage that wrote the substrate and fixtures (R1-A3, I8), planted fixtures derived from the substrate's own findings, and KC-2 labels from commit messages. The FR-R1 precedent proves the hazard is real, not hypothetical: the suite encoded a wrong expectation for ~2 release cycles and defended it as "corruption" (F-R0-11). UQ-2 (independent-oracle GT vs seeded-fixture; rubric N/M quantification gating RuleCard freeze) is still open.
- (b) FACT F-R0-11 (FR-R1, TEST_EXPECTATION_WRONG); FACT C-01 (two same-lineage verdicts); R1-A3, I8; UQ-2.
- (c) Hits S2 hardest (S-metric tripwire and FP control stand on fixture oracles); S3 next (S-gate legitimacy); S1 launders via C-01 instead.
- (d) Severity CRITICAL — every kill/success decision inherits GT quality.
- (e) [PROPOSAL] Blind independent oracle for ≥30% of GT rows with oracle author ≠ fixture author; external replication of the 10-case slice (DEBATE gap 6) as the cheapest C-01 input; record oracle provenance on every GT row.
- (f) Revisit trigger: any kill/success decision resting on a same-lineage oracle row; any GT row refuted by external replication.

### V6 — EVALUATION GAMING
- (a) [INFERENCE] S and G1–G7 are the only controls the strategies propose, which makes them the optimization target (Goodhart). Guards encode the findings; dev fixtures plant the same findings; an Agent can pass by pattern-matching plant shapes. GA-1 already caught the guard set under-covering its own motivating class (no macro-observability probe, no slot-classification guard), so "guards pass the plant test" ≠ coverage. The same-lineage harness attack (R4 §2) applies to all three strategies: campaign-derived fixtures + substrate-validated-by-substrate recreate S1's laundering inside S2/S3.
- (b) GA-1; R4 §2 same-lineage harness (inherited by each tester); FACT F-R0-11; FACT F-R0-15 (G13 verifier caveat — even fanout evidence is weaker than headlined).
- (c) Hits S2 hardest (S is its central gate + premature-MVP tripwire); S3 (monthly reallocation by S).
- (d) Severity CRITICAL — the metrics are the single point of control; gaming them silently invalidates every gate.
- (e) [PROPOSAL] Hold-one-out guard validation (effectiveness measured on planted patterns NOT in the deriving set); sealed holdout custodied by an agent/lineage that authored no fixtures; freeze S computation and guard definitions before the first measurement round; any post-measurement metric edit requires re-registration with a written reason.
- (f) Revisit trigger: any change to S, guards, or fixtures after a measurement round.

### V7 — ARCHITECTURE COMPLEXITY
- (a) [INFERENCE] S2 specifies 9 components (C0–C8) + 7 guards + an independent channel + FactGraph + RuleCards before line 1 of Agent code exists (F-R0-18). Every component is a failure surface and a hiding place for substrate-work-in-disguise (V2). S3 doubles coordination cost, and its own kill criterion ("either track idles >1 cycle") is already half-triggered by design: the GA-2 starvation loop shows the narrow first corpus generates no attribution evidence for any quarantined surface → Track B idles by construction. [INFERENCE] The architecture is specified ahead of any measured need — the exact anti-pattern the pack applies to the substrate.
- (b) FACT F-R0-18; GA-2 (starvation loop); S3 kill criteria; DEBATE kind-question (complexity axis unmeasured).
- (c) Hits S3 hardest (two tracks + two-tier trust + gate bookkeeping); S2 next (9 components).
- (d) Severity HIGH — complexity is the main execution risk to the shared "fast first end-to-end case" premise.
- (e) [PROPOSAL] Staged build: harness (C7) + hypothesis engine (C4) + ONE evidence channel first; C3/C5 graph machinery admitted only on a measured miss ("a component needs a measured miss to exist", symmetric to S3's drop rule); S3 drops dual-track unless a week-2 checkpoint shows both tracks non-idle.
- (f) Revisit trigger: two consecutive cycles with an unused built component → cut it; a first case blocked on an unbuilt component → build exactly that one.

### V8 — TOO-NARROW MVP
- (a) [INFERENCE] S3's first corpus {07, 08, PFC-1} touches no macro/config-conditions surface (goal item 3), no variant provenance, no runtime class. [INFERENCE] S measured there is biased upward by construction — the ≥80% gate can pass while the harder goal half is untested — and GA-2 shows this corpus generates no attribution evidence for quarantined surfaces, starving Track B. GA-4 hard-excludes scenario 01 from any MVP corpus, so the one scenario that differentiates S2-vs-S3 (R4 §2) is unmeasured by the very strategy that needs it least and claimed by the one that needs it most.
- (b) GA-2, GA-4; R4 §2 scenario-01 differentiator; DEBATE gap 5 (S stability: narrow corpus vs 12-fixture dev set; Δ>20 invalidates S3's start); UQ-3.
- (c) Hits S3 hardest; S2 partially (12 planted fixtures + 4 FP probes are wider but still macro-free unless GA-1 probe added).
- (d) Severity HIGH — the load-bearing gate measures the easy subset.
- (e) [PROPOSAL] Adopt GA-2 (≥1 planted quarantined-surface case, cheapest: F-V1-007 single-line plant, scenario-06-shaped, in corpus round 2); add ≥1 macro/config-conditions case to round 1 or annotate S as "easy-half-only" in every report; apply the gap-5 Δ>20 stability rule before any S-based reallocation.
- (f) Revisit trigger: gap-5 stability result; first corpus expansion; first S-based kill/realloc decision.

### V9 — PREMATURE GENERALIZATION
- (a) [INFERENCE] Three generalizations outrun the evidence. (1) 5 embedded RTOS repos + 1 host → "firmware/software". (2) 2 real-system variant observations (Z2/N2 PASS, R1-U3) → a blanket policy on ALL multi-variant work: GA-4's ban is either over-broad (foreclosing goal-relevant variant provenance) or under-broad (if non-bare generators also collapse) — nobody knows, because U3 is unmeasured. (3) RuleCard schema, trust tiers, and guard shapes fixed before any real run (UQ-6). [ASSUMPTION] embedded-C findings transfer to non-embedded C — unverified.
- (b) R1-U3, A2, I7; FACT F-R0-14; GA-4; R4 §2 scenario-01 row; UQ-6; DEBATE gap 2 (~20 real multi-variant projects decides it).
- (c) Hits S2/S3 (tiers/rules committed pre-measurement); S1 equally on the one-host 58-case surface.
- (d) Severity MEDIUM — reversible if measured early; costly once baked into schemas and contracts.
- (e) [PROPOSAL] Version-stamp scope claims in v0 outputs ("embedded C, single-host validated"); run DEBATE gap 2 before any un-quarantine or permanent ban; hold RuleCard schema freeze until UQ-2's rubric N/M quantification lands.
- (f) Revisit trigger: first non-embedded corpus case; second host; any U3 result contradicting the blanket quarantine.

## UPDATED ZERO-SUNK-COST POSITION (post-R4)

**Still NO to the current decomposition and fw-context's current role — and the keep-list SHRINKS** under R4's structural findings [INFERENCE from GA-1..4 + scenario-03 ceiling]:

1. [FACT-grounded boundary] The scenario-03 ceiling proves a goal slice (runtime queue/resource semantics) is unreachable by ANY amount of substrate work. From-scratch budget allocation moves that slice to a non-substrate leg (bounded dynamic channel, V3) or explicitly de-scopes it. Substrate waves spent there are definitionally sunk cost.
2. [INFERENCE] GA-4 removes variant-provenance value from any MVP until U3 is measured or F-V1-003 is fixed. The from-scratch substrate core is now: index + per-context ifdef views with evaluated branch states + call graph (direct flows) + measured stdio + raw-evidence export (pending DEBATE gap-7 root-class test). Variant machinery, vector tables (F-V2-001 open, C-03, AG-7), compare/equivalence tools (AG-6): DEFERRED, not cut — they re-enter only via U3 measurement.
3. [INFERENCE] GA-1 upgrades the independent channel from defense-in-depth to MANDATORY for goal item 3 in the MVP: config-conditions truth must flow from `clang -dM -E` (or a gap-7-proven export), not from the substrate's hidden macros (F-R0-16). KC-1 deadline stands.
4. [INFERENCE] GA-2 shows S3's S-gate, unmodified, is self-killing — gating discipline must be paired with a planted quarantined case, or the anti-sunk-cost mechanism becomes its own failure.

**Least-biased shape [PROPOSAL]: the S2/S3 hybrid** — S2's single-track consumer-first build (fastest first end-to-end case; avoids dual-track coordination and the starvation loop) with S3's S-gate + drop rule + impact statement applied to every substrate item, GA-2's planted quarantined case in corpus round 2, GA-1-hardened guards with hold-one-out validation (V6), and KC-2 A/B pre-registered as the FIRST measurement, not optional. Rationale: S1 is disqualified by recorded evidence (AG-2; reenacts completion/sunk-cost/laundering biases, R1-I1/I2/I5/I8); full S3 carries the starvation loop and coordination cost (GA-2, V7); bare S2 lacks a structural anti-sunk-cost lever on substrate work and its guard set is provably incomplete (GA-1).

**Evidence that would flip me** (in order of decision power):
1. KC-2 A/B (gap 1): grep+build+LLM arm ≥ substrate arm on ≥30 real bugs with blind oracles → demote substrate to optional cache; the hybrid collapses to harness + independent channel.
2. U3 (gap 2): planted divergences detectable AND per-variant isolation cheap → variant machinery re-enters the core; hybrid leans more substrate; scenario 01 un-freezes.
3. External 10-case replication (gap 6) substantially refutes the FAIL_PRODUCT set as oracle defects → the substrate is closer to ready than C-01 suggests; gates loosen; an S1-lite becomes arguable.
4. Guard-plant test (gap 4 + GA-1 macro probe) catches ≥6/7 incl. the macro probe → export may be cut; S2 frame suffices with guards.
5. Independent-channel representativeness (gap 3) FAILS in the corporate environment (WSL clang cannot referee MSVC-class builds within budget) → the untrusted-substrate premise weakens; substrate trust must be re-derived per-surface, and no consumer-side break exists.

## UNRESOLVED OBJECTIONS RETAINED BY A7

- O1 [UNCERTAINTY U1]: KC-2 baseline unmeasured — all substrate-necessity AND substrate-overhead claims remain articles of faith in both directions.
- O2 [UQ-2]: no independent-oracle GT; rubric N/M unquantified — RuleCard freeze and S thresholds are premature until set.
- O3 [C-01 / KC-4]: two same-lineage verdicts unadjudicated; "substrate readiness" has no agreed meaning; external replication before any "certified" language stands.
- O4 [DEBATE gap 3]: independent-channel representativeness (WSL clang vs MSVC-class builds) unvalidated within latency budgets — the structural break is asserted, not built.
- O5 [UQ-3 / KC-1]: no representative macro/config-conditions channel in sprint 1 kills S2's current form — F-R0-16 says the data exists; no surface exposes it.
- O6 [R4 §2]: same-lineage harness laundering is structurally unresolved in ALL strategies; GT custody split is proposed here, not built.
- O7: guard effectiveness unmeasured incl. the GA-1 macro probe; G3 root attestation may be self-referential (R4 scenario-05 row).
- O8: the runtime-class goal slice is unowned (scenario-03 ceiling); no strategy has a dynamic leg; GA-3 labels the limitation, nothing closes it.
- O9 [DEBATE gap 5]: S on the narrow corpus is uninformative by construction; the Δ>20 stability rule is unexecuted.
- O10 [V6]: evaluation-gaming window — freeze-before-measure discipline has no owner or enforcement mechanism yet.

## EVIDENCE PATHS

- fault_discovery_goal_architecture_agent_team/outputs/{FACT_LEDGER.json, CONTRADICTIONS.json, R1_A7.md, DEBATE_ROUND_1.md, SCENARIO_STRESS_TEST.md, STRATEGY_S1.md, STRATEGY_S2.md, STRATEGY_S3.md}
- workflow/{ANTI_ANCHOR.md, EVIDENCE_RULES.md}; inputs/GOAL_CONTRACT.md (non-goals, A0 rule)
- .fw-validation/{FINAL_VALIDATION_RESULTS.json, FINDINGS.json, KNOWN_VALIDATION_LIMITS.md}; .fw-final/FINAL_REPAIR_AUDIT.md
