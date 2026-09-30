# K2 INTERFACE SPEC — Hypothesis-and-Rule Loop (Wave-1)

Component K2 of FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md. Governing inputs: MECHANISM_HYPOTHESIS_SCHEMA.json, RULECARD_SCHEMA.json, FACT_RECORD_SCHEMA.json, FAULT_DISCOVERY_REQUIREMENTS.md FR-1/FR-2/FR-5/FR-7, S0_GOAL_ADR.md §8, WAVE1_CORPUS_MATRIX.md §2 (two-part C1 separation). K2 consumes only K1-minted fact_ids (K1_INTERFACE_SPEC.md HARD RULE); it never touches the substrate and never sees oracles.

## 1. Inputs

- QueryBundle (data record): {bundle_id, family, target_scope{repository, commit, build_snapshot_id, lineage}, corpus_case (C1|C4|C5|C6 per WAVE1_CORPUS_MATRIX.md §3), template_ref (family library template id), freeze_id}.
- Evidence request interface to K1 — typed requests mapping 1:1 onto K1 surface calls (K1_INTERFACE_SPEC.md §2):

| Request type | K1 call | Trust path |
|---|---|---|
| SYMBOL_EXISTENCE | query_exact_symbol | T0(c) / T1 (G6) |
| CONDITION_CHAIN | query_condition_context + query_definition_window | T0(a)(d) / T1 |
| CALL_FLOW | query_call_flow_direct | T0(b) / T2 may-targets |
| RAW_TEXT_CROSSCHECK | submit_independent_check(independent_grep \| independent_clang_e) | independent (E3/E4/E5) |
| MANIFEST_DIFF | submit_independent_check(build_manifest_diff) | G1 out-of-band |

Every request returns a fact_id or a REFUSED/ABSENT record. Refusals feed missing_evidence (never retried blindly; bounded loop, §5).

## 2. Mechanism-hypothesis representation

MECHANISM_HYPOTHESIS_SCHEMA.json verbatim: hypothesis_id, fault_class, mechanism_steps[{step_id, description, required_fact_ids, step_kind ∈ condition|definition|call_flow|config|absence}], required_facts (canonical factRef{fact_id, trust_class}), assumptions, missing_evidence[{what, why_needed, channel}], forbidden_overclaims, scope{goal_item_ref, corpus_ref, lineage}, status ∈ candidate|rejected|insufficient_evidence|promoted_to_rule. Only a hypothesis with status=promoted_to_rule may back a RuleCard (RULECARD_SCHEMA.json hypothesis_ref).

## 3. Family library FORMAT (data, not code)

family = {family_id, wave, query_templates, required_fact_kinds, missing_evidence_prompts}
- family_id ∈ the RULECARD_SCHEMA.json fault_class enum: condition_config_gated | init_submit_pairing | existence_absence | direct_flow_presence. Wave-1 set = these four (FR-5 mapping: R3a → condition_config_gated; R6 preconditions/init-submit pairing → init_submit_pairing (C1 class); R4 → existence_absence; R1 → direct_flow_presence).
- query_templates: versioned parameterized K1 request sequences (typed request + parameters + expected fact kinds) — JSON DATA loaded by K2, never compiled logic.
- required_fact_kinds: subset of the MECHANISM_HYPOTHESIS_SCHEMA step_kind enum; drives loop completeness checks.
- missing_evidence_prompts: channel suggestions mapping to missing_evidence.channel values (FACT_RECORD_SCHEMA.json evidence_surface values, or runtime_unavailable).
Storage: versioned data file, hashed into the freeze (FREEZE_MANIFEST_SPEC.md class prompts_config). Adding a family = new data entry + re-freeze; no K2 code change.

## 4. EvidenceGate (FR-2 E1–E7; refuse/degrade outcomes)

| Rule | Check | Outcome |
|---|---|---|
| E1 | claim without a provenance-complete fact chain; substrate output treated as ground truth | REFUSE promotion (T0 included: trusted ≠ ground truth) |
| E2 | T2 ref load-bearing; T1 ref not K1-mediated (provenance.k1_mediated=false); T0 used outside its named scope (context_identity mismatch) | REFUSE (T2 never load-bearing; out-of-scope T0 drops to the stricter class) |
| E3 | single-line/fragment load-bearing claim with <2 surfaces | REFUSE load-bearing; row stays partial_cross_checked until closed |
| E4 | reachability negative without root-set attestation | REFUSE |
| E5 | vector claim without ≥2-surface corroboration + form attestation | REFUSE (no Wave-1 vector case; rule armed) |
| E6 | macro-value evidence value-decisive or load-bearing | DEGRADE (degraded provenance, never load-bearing); value-decisive use → REFUSE |
| E7 | negative claim without enumerated attempts; ambiguous absence used as absence | REFUSE; ambiguous absence adjudicates PRESENT (anti-sunk-cost; REFUTATION_STATE_MODEL.md §3.5) |

Every gate decision is logged with the rule id; a refusal always names the failing rule.

## 5. Missing-evidence loop (bounded)

while hypothesis.status == candidate:
  gaps = required_fact_kinds not satisfied by the fact log
  for each gap: issue the typed request(s) from family.query_templates (skip requests already REFUSED/ABSENT)
  on REFUSED/ABSENT: append {what, why_needed, channel} to missing_evidence; at most ONE re-try per gap with a different template
  BOUND: max_iterations = 3 per hypothesis (fixed constant)
Exit: (a) all required_fact_kinds satisfied → promotion path; (b) bound reached; (c) hard refusal making the load-bearing path impossible (E2/E6) ⇒ status=insufficient_evidence.

## 6. RuleCard generation, termination, abstention

generate_rulecard(hypothesis) → RULECARD per RULECARD_SCHEMA.json: trigger_conditions (deterministic predicates {kind, fact_refs, statement, negatable}); evidence factRefs with trust_class inherited UNCHANGED from the fact log (mismatch = E1 violation); provenance.k1_mediated=true; polarity; forbidden_overclaims non-empty (FR-7; two-part C1 separation: runtime occurrence recorded only as missing_evidence channel runtime_unavailable, never asserted); scope_stamp; refutation_protocol{steps, oracle_requirement}; refutation_result{state=UNEXECUTED}; validation_status=unvalidated.

K2's output is EXACTLY ONE of:
1. candidate RuleCard (hypothesis.status=promoted_to_rule);
2. rejected hypothesis (status=rejected — refuted by its own counter-search or an E-gate refusal on the mechanism);
3. insufficient-evidence/abstain: status=insufficient_evidence + missing_evidence list (≥1 entry, each with a channel).

Abstention is a FIRST-CLASS outcome: K2 MUST NOT pad a weak result into a RuleCard — no gate lowering, no T2 load-bearing rescue, no filler predicates, no scope_stamp widening. C6 path: K2 emits the candidate whose declared correct outcome is its own refutation (REFUTATION_STATE_MODEL.md §3.2); adjudication belongs to K3.

## 7. Non-goals
No refutation adjudication beyond UNEXECUTED (K3 scorer); no oracle access; no substrate calls; no fw-context repair.
