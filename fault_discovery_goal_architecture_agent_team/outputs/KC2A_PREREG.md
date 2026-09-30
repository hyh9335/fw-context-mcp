# KC2A PREREG — Small bounded substrate-value diagnostic (design-only in this task)

Status: PRE-REGISTERED DESIGN (this document is the registration). Execution is a bounded diagnostic — ≤ a few days — and starts ONLY after the first E2E RuleCard path works (WAVE1_IMPLEMENTATION_PLAN.md step 9). Per S0_GOAL_ADR.md §3 and FAULT_DISCOVERY_REQUIREMENTS.md FR-6: diagnostic, NOT an architecture verdict; explicitly NOT an early infrastructure project — it reuses the Wave-1 harness (K3 scorer, freeze, custody) and adds no new evaluation infrastructure.

## 1. Question
Does K1-mediated substrate assistance catastrophically dominate a simpler evidence path (or vice versa)? KC-2A detects only CATASTROPHIC dominance; every finer question defers to KC2B_PROTOCOL.md.

## 2. Arms
- Arm S (substrate-assisted): K1-mediated fw-context surfaces (all T0/T1 per K1_INTERFACE_SPEC.md) + independent channels (grep/rg raw-disk, WSL clang -E, build-manifest diff), full K2 loop.
- Arm N (simpler): grep/rebuild + clang/LLM reading ONLY. No fw-context, no K1 fact log, no substrate. Minimum provenance parity: every claim cites a command or file+lines; same RuleCard schema output; the same E1-style provenance discipline enforced by the scorer.
Both arms: same pool, same freeze, same blind scoring, same custody (S0_GOAL_ADR.md §4).

## 3. Sample
N=10 real bugs: 5 per arm drawn from the SAME pool, stratified by family (the four wave-1 family_ids of K2_INTERFACE_SPEC.md §3; ≥1 per family per arm where the pool allows).
Rationale (pre-registered): diagnostic power ONLY. N=10 can detect total failure or clear dominance of one arm; it is UNDERPOWERED for fine comparisons (effect sizes, per-family differences, time-to-evidence distributions). Every KC-2A report MUST state this underpowered status.

## 4. Metrics (blind-scored by the K3 scorer; keys custodian-held)
1. correct-mechanism-ID cases (blind adjudication vs oracle; primary).
2. time-to-evidence (wall-clock to the first claim-complete evidence chain; run-log recorded; secondary).
3. silent-error count (unflagged wrong-trust-tier / substituted-symbol / truncation-hidden / macro-value-decisive errors found by scorer audit replay; secondary).

## 5. Stopping rule
Stop at the EARLIER of: (a) a 3-of-5 dominance gap is visible in blind scoring on one arm (e.g. one arm has ≥3 of its 5 correct while the other has ≤2), or (b) the execution budget is spent (≤ a few days; hard stop). No extension, no mid-run arm changes, no prompt tuning between arms (S0-Q1 discipline applies to both arms).

## 6. Interpretation boundaries (pre-registered)
- The ONLY trigger for a substrate-value review: CATASTROPHIC dominance — a ≥8/10 vs ≤2/10 class gap in correct-mechanism-ID across arms, OR systemic silent errors concentrated in one arm (e.g. ≥3 of 5 cases with unflagged trust-tier violations).
- Anything finer — 2–3 case differences, per-family wins, time-to-evidence differences alone — is explicitly OUT OF SCOPE and defers to KC2B_PROTOCOL.md. No architecture change, no substrate repair wave, and no K1 redesign may be justified from KC-2A alone.
- KC-2A never gates the first E2E RuleCard and never gates Wave-1 acceptance (FR-6; S0_GOAL_ADR.md §3).
