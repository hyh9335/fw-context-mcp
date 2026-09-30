# SCENARIO STRESS TEST — R4 Aggregate (A0 record)

Source files: `stress/R4_S1.md`, `stress/R4_S2.md`, `stress/R4_S3.md`. Each contains 8 scenarios × 10 dimensions (evidence required, tools, fw-context role, reasoning steps, expected rule, GT, blockers, workaround, silent-error risk, falsifier), claims labeled, no winner scores. This aggregate records cross-strategy structure only.

## 1. Strategy × scenario shape (descriptive)

| Scenario | S1 (substrate-first) | S2 (MVP-first + guards) | S3 (dual-track) |
|---|---|---|---|
| 01 variant-specific | Weakest: fix load = self-masking CRITICAL F-V1-003; fix unverifiable without external per-variant oracle; UQ-1 open | G1 = ban without detector; no in-band detection possible; A7 gaps 2–3 unmeasured | Fully Tier-2 (quarantined F-V1-003); workaround itself needs per-variant clang -E |
| 02 callback/timer | Strong-ish: direct flows pass F-R0-08; certainty tiers match rule demand | G2 lower-bound discipline; transitive may-targets under-reported (F-V1-004) | Split: direct Tier-1 / transitive Tier-2; TB-pull F-V1-004 |
| 03 queue/resource | Structural ceiling: no repair yields runtime queue semantics; worst effort-to-payoff | NO guard covers the class (macro/bound extraction); F-R0-16 + F-R0-14 exposure; maximal silent-error risk | Mostly Tier-1 with cheap header-read workaround; TB-pull = raw-evidence export |
| 04 interrupt/startup | Entangled F-V2-001+F-V1-006; C-03 over-correction into false PRESENCE; AG-7 revalidation | Strongest tier: G5 + precisely characterized defect + cheap disk workaround | Split by form: assembly Tier-1+E5 / C-form Tier-2 pre-gated (F-V2-001 before vector corpus) |
| 05 dead-path-vs-unknown | F-V1-005 fix = root-set redesign; revalidation heavy | G3 attestation self-referential unless root enumeration is out-of-band; frozen misreport is a LIVE chain | Defining case is the quarantined one (compile_commands-only roots); most natural S-attribution case |
| 06 one-line helper | F-V1-007 fix = storage change | Strong: G4 shape-based trigger, no product change | Weakest tier: core evidence Tier-2 (F-V1-007); TB-pull candidate |
| 07 exact-lookup | Strongest: contract fix F-V2-002, gate exists | Strong: G6 canonical guard case | Tier-1-with-guards; F-V2-002 likely stays deferred |
| 08 large-fanout | Strong: F-V1-008 LOW fix on proven fanout base F-R0-13 | Strong: G7 deterministic cap→visibility | Tier-1-with-truncation-guard; F-V1-008 deferred; single-line sub-contexts compound with F-V1-007 |

## 2. Cross-cutting structural findings (all three testers, independently convergent)
- **Scenario 03 defines a ceiling shared by every strategy's substrate component**: runtime queue semantics are not reachable by any repair; only emission-site facts + forbidden-overclaim contracts (A6 two-part) operate there. This is a FACT-backed boundary of the whole program, not a strategy differentiator.
- **Scenario 01 is the strategy differentiator**: it exercises exactly the axis where S2/S3 "differ in kind" (who repairs the substrate, when) and where the deciding evidence (U3, A7 gap 2–3) is missing. No strategy can clear scenario 01 today without either the F-V1-003 fix or per-variant external truth.
- **Guard-set coverage gap (S2 tester, FACT-backed)**: the 7 FAIL_PRODUCT patterns include F-V1-001 (macro observability) and G10 (slot-0 data symbol), but G1–G7 contain no macro-observability guard and no slot-classification guard. A7's R4 gap-4 plant test under-covers the scenario-03 class. ADOPTED PROPOSAL: add a planted macro-context probe to the gap-4 experiment.
- **S3 starvation loop (S3 tester)**: Track B defers all fixes pending S-attribution; the narrow first corpus (07+08+PFC-1) generates no attribution evidence for any quarantined surface → Track B idles by design → that idle is S3's own kill trigger. ADOPTED PROPOSAL: add one planted quarantined-surface case (cheapest: F-V1-007 single-line plant, scenario-06-shaped) to corpus round 2.
- **Same-lineage harness attack applies to all three strategies** (A7, inherited by each tester): campaign-derived fixtures + substrate-validated-by-substrate recreate S1's laundering inside S2/S3; the independent channel is the only structural break and its representativeness is itself unvalidated (gap 3).
- **Revalidation cadence (S1 tester)**: every scenario falsifier doubles as a revalidation exit criterion → under any strategy that fixes substrate surfaces, the 58-case campaign must re-run per fix-wave, not once.

## 3. Registered adjustments carried to R5/R6
- GA-1: gap-4 experiment extended with a planted macro-context probe (S2 finding).
- GA-2: S3-or-successor corpus round 2 must include ≥1 planted quarantined-surface case to break the starvation loop (S3 finding).
- GA-3: scenario 03 requires the two-part facts+forbidden-overclaims contract (A6 R1 verdict, confirmed by all three stress passes).
- GA-4: scenario 01 cannot be in any MVP corpus until U3 is measured or F-V1-003 is fixed — registered as a hard sequencing constraint.

No single-winner score was produced, per the R4 spec.
