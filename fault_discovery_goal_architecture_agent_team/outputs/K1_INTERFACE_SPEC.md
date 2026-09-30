# K1 INTERFACE SPEC — Evidence Gateway (Wave-1)

Component K1 of FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md. Governing inputs: CURRENT_CAPABILITY_MAP.md (T0/T1/T2 named scopes), FAULT_DISCOVERY_REQUIREMENTS.md FR-2/FR-3/FR-8, FACT_RECORD_SCHEMA.json (sole output row shape), DETECTOR_ADMISSION_MATRIX.md (G1/G4/G6/G7/G8), S0_GOAL_ADR.md §1 (freeze_id stamping). K1 is substrate-facing only: it generates no hypotheses or verdicts (K2_INTERFACE_SPEC.md) and holds no oracles (K3_INTERFACE_SPEC.md).

## 0. HARD RULE
ALL substrate access goes through K1, including T0. No component — K2, the solving Agent, the scorer, the plant-test runner — may call any fw-context tool directly. K1 is the SOLE FactRecord creator: every fact_id consumed downstream was minted by K1 under one freeze_id. A direct substrate call found in any round log voids the round's evidence provenance (S0_GOAL_ADR.md §1).

## 1. Contract clauses (responsibilities)

- C1 Snapshot identity. Every FactRecord carries source_snapshot_id, build_snapshot_id, repository, commit, context_identity, freeze_id (all FACT_RECORD_SCHEMA.json required fields). K1 refuses any query whose build_snapshot_id is not the round's pinned single-variant build (GA-4 fence; PT-G1a).
- C2 T0 named-scope enforcement. T0 covers ONLY the four named scopes of CURRENT_CAPABILITY_MAP.md §T0: (a) multi-line conditional structure of the active build (read_file active mode, list_conditional_regions); (b) proven direct call/reference edges, include_possible=false, single-variant; (c) exact-name symbol existence + multi-line definition rows (lookup_symbol exact=true); (d) explain_condition CONDITION TEXT ONLY, run-pinned. Out-of-scope use AUTO-DOWNGRADES the claim class: single-line extraction exits (a) → T2; a may-target row exits (b) → T2 lower_bound, never emitted as T0; a fallback/suggestion row exits (c) → rejected per C3; macro-VALUE semantics exit (d) → T2 degraded (G8). Downgrades are recorded in trust_class + detector_checks — never silent.
- C3 T1 fail-closed, per mapped surface (CURRENT_CAPABILITY_MAP.md §T1):
  - Exact-lookup (lookup_symbol/search_code): exact-lookup results are NOT trusted by themselves. K1 verifies returned symbol identity against the query intent (exact name, definition row, pinned context). ANY row carrying `_fallback`/`_did_you_mean`/suggestion provenance is REJECTED. Absence = explicit "no exact match" + enumerated near-miss list labeled NON-EVIDENCE (G6; PT-G6).
  - Enumeration/fanout: K1 inspects every result for limit/truncation signals. Completeness claims are REFUSED on truncated results; the row is emitted as truncated_lower_bound + explicit limit metadata (G7; PT-G7).
  - explain_condition: the claim is pinned to the run's context_id (recorded in context_identity); a re-run mismatch marks it unstable → downgraded, never load-bearing.
  - Source-reachability negatives: root-set attestation required (linker script/ENTRY recorded); polarity is "unreachable-from-attested-roots" with caveat; "dead in final binary" is REFUSED (G3 discipline; E4).
- C4 T2 quarantine. Macro values (F-V1-001), single-line/fragment bodies (F-V1-007), transitive may-targets (F-V1-004), bare-generator cross-variant claims (F-V1-003): REFUSED as load-bearing; served only on explicit request as degraded-provenance, non-load-bearing, labeled rows (G8; PT-G8). Cross-variant claims are REFUSED outright (PT-G1a).
- C5 Independent channels. grep/rg raw-disk, WSL clang -E, build-manifest diff (FR-8): K1 executes them; the full command is recorded verbatim in exact_query; output retained at raw_artifact_ref; channel class in provenance.channel. Output that cannot be retained ⇒ REFUSED.
- C6 Detector execution hooks. G1/G4/G6/G7/G8 run at the fixed points of §3; detector_checks on every row lists applied IDs (G2/G3/G9 measured and reported, non-blocking).
- C7 Completeness/truncation metadata. Every result carries completeness_status ∈ {complete, truncated_lower_bound, partial_cross_checked}; a result without it is an implementation defect, not an answer.
- C8 Query/result recording. Append-only immutable fact log: exact_query verbatim, parameters, raw_artifact_ref, FactRecord, recorded_at, freeze_id. Replayable: re-running exact_query under the same build_snapshot_id must reproduce the row or emit a REFUSED row explaining divergence. The log is never rewritten; corrections are new rows.

## 2. Interface surface (pseudocode signatures; not implementation)

- query_exact_symbol(name, case_ref) → RESULT{exact_definition_rows} | ABSENT{near_miss_list labeled NON-EVIDENCE} | REFUSED{reason}
- query_condition_context(file, line, case_ref) → RESULT{condition_text, macro_inputs.def_source, context_id} | REFUSED{reason}  — condition text only; macro values → T2 path (C4)
- query_call_flow_direct(target, direction ∈ {callers, callees, references}, case_ref) → RESULT{edges certainty="exact"} | LOWER_BOUND{may_target_rows labeled} | REFUSED{reason}
- query_definition_window(file, start_line, end_line, case_ref) → RESULT{ifdef_filtered_window, skeleton} | REFUSED{reason}
- submit_independent_check(channel ∈ {independent_grep, independent_clang_e, build_manifest_diff}, command, case_ref) → RESULT{raw_artifact_ref} | REFUSED{reason}
- build_fact(draft{evidence_surface, exact_query, raw_artifact_ref, intended_claim, completeness_status}) → FactRecord | REFUSED{reason}  — SOLE FactRecord constructor; validates against FACT_RECORD_SCHEMA.json
- detector_hook(detector_id ∈ {G1, G4, G6, G7, G8}, fact_ref) → PASS | FLAG{action ∈ {downgrade, block}, reason} | REFUSED{reason}

Error model — exactly four result kinds, NO silent paths:
- RESULT — normal answer; still provenance-carrying, never ground truth, including T0 (E1).
- REFUSED{reason} — claim not servable (scope exit, custody mismatch, cross-variant, un-retained output); recorded in the fact log as a refusal row.
- DEGRADED{row, label} — T2/quarantine content served with degraded provenance, non-load-bearing (E2/E6).
- LOWER_BOUND{row, completeness_status} — truncated/partial content, admissible only as a lower bound (G7/E7).

## 3. Detector hook points (fixed; DETECTOR_ADMISSION_MATRIX.md rows)

| ID | Hook point | Behavior |
|---|---|---|
| G1 | build_fact (context_identity vs pinned build) + build_manifest_diff instrument | cross-variant claim → REFUSED; manifest diff recorded per round (PT-G1a/b) |
| G4 | build_fact (chain claims) + query_definition_window | cross-file chain fully evidenced or explicitly marked incomplete; single-line body query routed to T2 discipline (≥2-surface, E3); zero silent uses (PT-G4a/b) |
| G6 | query_exact_symbol | `_fallback` rejection; explicit absence + near-miss list labeled NON-EVIDENCE (PT-G6) |
| G7 | every query result | truncation signal → truncated_lower_bound; completeness refused on truncated results (PT-G7) |
| G8 | build_fact (macro rows) | value rows labeled degraded + non-load-bearing; conflicts labeled; condition-text/-D-flag path unaffected (PT-G8) |

## 4. build_fact construction rules (deterministic)

- Single-line/fragment source row ⇒ trust_class=T2, completeness_status=partial_cross_checked until ≥2 surfaces (E3); blocked from load-bearing.
- Truncated enumeration ⇒ polarity=lower_bound, completeness_status=truncated_lower_bound (G7).
- Absence ⇒ polarity=negative + enumerated attempts over ≥2 surfaces (E4/E7); ambiguous absence adjudicates PRESENT downstream (K3 scorer; REFUTATION_STATE_MODEL.md §3.5).
- Macro-value row ⇒ certainty=degraded, detector_checks += G8, never load-bearing (E6).
- Every row: freeze_id + recorded_at stamped before any measurement consumes it.

## 5. Non-goals
No hypothesis generation, RuleCards, or adjudication (K2_INTERFACE_SPEC.md, K3_INTERFACE_SPEC.md); no oracle custody; no fw-context repair — all product fixes remain S-gated Track-B pulled by measured misses (FAULT_DISCOVERY_MVP_ARCHITECTURE_V1.md).
