# FAULTNESS_GATE_SPEC.md — deterministic family-scoped Faultness/Consequence Gate

Phase: WAVE-1.1 semantic-correctness repair (PMR-4). Status: implemented,
corpus-exercised, regression-tested.

## 1. Purpose and hard constraints

The Faultness Gate decides whether a **promoted RuleCard candidate is
actually a fault claim** — as opposed to a benign conditional that happens
to compile OUT, or a missing macro that is the CAUSE of its own guard.
It closes PMR-2/PMR-4: before this gate, "compiled out" was promoted as a
fault statement with no consequence semantics, and C6's benign conditional
(RC-I7-B-LIB-GETCWD-107) was accepted as a fault card.

Hard constraints (per the repair directive):

- deterministic, family-scoped, **round-level** — no causal DAG, no LLM;
- **case-agnostic**: role predicates reference structured facts only; no
  case identifiers, symbols, or file names appear in executable code
  (machine-checked by the acceptance gate G10 and test T6);
- fail-closed: a candidate whose structured target cannot be resolved
  (`G0_family_roles_evaluable`) is REJECTED, never passed vacuously;
- demotions are first-class: a demoted candidate becomes a `rejected`
  outcome carrying `{rulecard_id, family, failed_roles, detail}`, appears in
  `ROUND_RECEIPT.json.rejected_candidates`, and is recorded in
  `REFUTATION_RESULTS_V2.json` with `final_state = REJECTED_FAULTNESS_GATE`.

## 2. Placement in the round pipeline

Implemented in `fault_discovery_agent/k2/faultness_gate.py`; wired into
`k3/e2e_runner.solve_case` AFTER the K2 loop promotes candidates and BEFORE
semantic-coherence checking and refutation:

```
K2 loop outcomes ──► run_round_faultness_gate ──► demoted -> rejected outcome
                                    │                          (oc.faultness_rejection)
                                    └► survivors ──► semantic-coherence check
                                                 ──► faultness PASS on card.validation
                                                 ──► RS1..RS4 refutation
                                                 ──► finalize ROUND_RECEIPT.json
```

The gate consumes the round's own evidence: `fact_log.jsonl` rows (with
`raw_artifact_ref` payloads) and the round's structured targets. No new
substrate access, no K1 calls beyond those already made by the loop.

## 3. Family role predicates

A family is evaluable when all of its roles are satisfiable from recorded
facts; each role yields `{satisfied, evidence}` and the card passes only if
every role is satisfied.

### existence_absence (subject S, probe file P)

| Role | Predicate | Evidence requirement |
|---|---|---|
| A1 | `absence_in_pinned_build` | a NEGATIVE fact naming S (raw text contains `name='<S>'`) |
| A2 | `definition_file_located` | the probe target carries a file that EXISTS in the source snapshot (fixture/ prefix normalized) and a coverage fact on that file is recorded |
| A3 | `config_causal_cutoff` | a conditional-region fact on P shows an UNTAKEN branch of a `CONFIG_*` guard AND **S is not itself a guard symbol** of any region on P. A missing `CONFIG_*` macro is the CAUSE of a dead guard, never a harmful consequence — the wave-1 defect that accepted `RC-I7-A-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD`-style macro-absence cards is structurally impossible |

### condition_config_gated (region [A,B] of file F, guard symbol G)

| Role | Predicate | Evidence requirement |
|---|---|---|
| R1 | `pattern_compiled_out` | a conditional-region fact for F shows an UNTAKEN region overlapping [A,B]. Branch taken-state is read per-branch (`branches[i].taken` ∈ {1,0,-1}); a region with ANY taken branch compiles IN and can never be a fault |
| R2 | `config_symbol_guard` | G (the region's target symbols) contains a `CONFIG_*` symbol |
| R3 | `harmful_consequence` | at least one of: **(a) definition_cutoff** — an existence_absence probe on the SAME file proved a DEFINITION-LEVEL symbol absent, where that symbol is NOT any guard symbol of the file's regions, and for probe targets carrying `call_line` the call site lies INSIDE [A,B]; **(b) pairing_cutoff** — a negative call-flow fact names symbol X (no live caller) AND a raw-text grep hit shows `X(` at a line inside [A,B] (grep hits are filtered to call statements — text ending with `;` — so the guarded API's own definition line is never mis-parsed) |

### init_submit_pairing (file F, call symbol X)

| Role | Predicate | Evidence requirement |
|---|---|---|
| P1 | `config_mismatch_condition` | a conditional-region fact for F with a `CONFIG_*` guard |
| P2 | `pairing_definition` | a positive definition-window fact on F |
| P3 | `pairing_call_flow_broken` | a negative call-flow fact naming X |

### direct_flow_presence

UNSATISFIABLE in wave 1 (fail-closed by design; no corpus case uses it). A
candidate in this family is always rejected with
`G0_family_roles_unsatisfiable_wave1`.

## 4. Why each defect can no longer reproduce

| Historical defect | Gate repair | Regression test |
|---|---|---|
| C6: benign conditional [107,110] accepted as fault | R3 requires an independently-proved consequence on the file; none exists on the post-fix twin → demoted | corpus rerun C4C6_B (`zero accepted fault cards`), gate G4 |
| C4 macro-absence card (guard symbol treated as consequence) | A3 excludes guard symbols from definition_cutoff subjects | `test_t2_absent_guard_macro_is_cause_not_consequence` |
| C4 region card accepted with no in-round consequence | R3(a) requires the absent symbol on the SAME file; R3(b) requires the call site inside the region | `test_t2_c6_like_region_candidate_fails_without_consequence`, `test_t3_region_cutoff_requires_call_site_inside_region` |
| Compiled-IN region promoted as fault | R1 reads per-branch taken state | `test_t2_taken_region_never_yields_a_fault_card` |
| Unresolvable target passed vacuously | G0 fail-closed rejection | `test_t3_unresolvable_target_fails_closed` |

## 5. Ground-truth expectations (frozen at revalidation)

- C1: region card RC-C1-ACCEL-TRIGGER-33 DEMOTED (region compiles IN — the
  correct spelling is live); region -47 and the pairing card PASS. The
  demotion is the intended semantic tightening of wave-1's over-promotion.
- C4 (A twin): region [38,138] + getcwd absence PASS (definition_cutoff).
- C6 (B twin): region [107,110] DEMOTED (no in-region consequence) → zero
  accepted fault cards.
- C7: region + API-absence + pairing PASS; the guard-macro EA card DEMOTED (A3).

## 6. Scope

The gate is necessary-but-not-sufficient: a faultness PASS means the
candidate is a coherent, consequence-grounded fault CLAIM for its family.
Terminal acceptance additionally requires semantic-coherence PASS (PMR-3),
evidence-integrity PASS (RS1), and non-refutation under the pinned-scope
protocol (RS2–RS4). The three axes are reported SEPARATELY on every card
(`validation.{evidence_integrity_status, faultness_status, refutation_state}`).
