# CURRENT CAPABILITY MAP — fw-context v0.31.0 at HEAD d8f08a4 (R7, A0)

Product: Python MCP server; frozen HEAD d8f08a4 (v0.31.0-94), tracked tree clean. Verdicts below cite the frozen evidence (FACT_LEDGER, .fw-validation, .fw-final).

## Tier-1 surfaces (measured-passing, trusted WITH named detectors)
| Surface | Passes | Use in MVP | Detector |
|---|---|---|---|
| ifdef-filtered read_file / list_conditional_regions / explain_condition (condition text + macro_inputs.def_source) | F-R0-13 corpus | per-context evidence for config/condition family (wave-1 primary) | G4 shape-trigger; context pinning per D06 |
| Direct call graph + proven function-pointer edges (find_callers/find_call_path exact tier) | F-R0-08 (direct flows) | registration→invocation lower bounds (R2 polarity) | G2 lower-bound discipline |
| lookup_symbol exact / search_bodies FTS5 (multi-line) | F-R0-13 | symbol/definition retrieval | G6 exact-no-fallback plant test |
| Deterministic truncation behavior | F-R0-13 base | fanout honesty (scenario 08) | G7 explicit-truncation |
| stdio recording + reproducibility harness | F-R0-13 | audit trail for K3 | custody split (D08) |

## Tier-2 surfaces (quarantined — defect-class; S-gated Track-B re-earn)
| Surface | Defect | MVP treatment | Re-earn trigger |
|---|---|---|---|
| compare_variants / group_equivalent_variants with bare generator | F-V1-003 self-masking INDEX_DIVERGENCE (CRITICAL) | banned (GA-4); single-context per index | U3 measured (gap-2) |
| Macro-VALUE queries (expanded_value; DB/export read) | F-V1-001 silent wrong-value class | labeled DEGRADED-PROVENANCE instrument + G8; never RuleCard load-bearing; condition text + build-file -D flags are the in-band/out-of-band wave-1 channels | Track-B raw-evidence export (named pull event, gap-7 costing) |
| get_source / search_bodies single-line body search | F-V1-007 silent data loss | sub-context single-line claims cross-checked ≥2 surfaces | round-2 planted case (GA-2) |
| Transitive callback may-targets | F-V1-004 | uncertainty-labeled lower bound only | family-#2 |
| find_dead_code on compile_commands-only roots | F-V1-005 misreports live chain | G3 attestation caveat emission only | Track-B root redesign |
| get_vector_table C-form (CMSIS6-style) | F-V2-001 false-missing (HIGH) | E5 ≥2-surface cross-check on ANY vector claim; G9 slot-classification | Track-B fix (top S-gated candidate) |

## Product-side facts bounding the map
- Only F-V1-003 is BLOCKING_FOR_AGENT_MVP (A4 R1, upheld in R6 with re-scoped wrapper claim); F-V1-003 + F-V2-001 are the roadmap-critical product repairs (both Track-B S-gated).
- Zero product fixes are MVP-blocking under the tiered-trust plan (A4 PC-4) — repairs are pulled by measured misses, not pushed.
- C-01: bounded-trial readiness is a HYPOTHESIS (same-lineage evidence; V6-disciplined measurement event required to convert).
- No in-band per-variant provenance signal exists at 0.31.0 (A4 UQ-1); config_hash is identity, not integrity → G1 must be an out-of-band build-manifest diff (K1 requirement).
- explain_condition nondeterminism (F-R0-15) strikes the wave-1 config family directly → context pinning or documented waiver mandatory (D06).
- Substrate cap (D01): 1 wave-equivalent of substrate work until the first end-to-end Agent case exists.

## What the substrate CANNOT provide at any trust tier
Runtime queue semantics (scenario-03 ceiling, program-wide); linker-script ground truth without the script (host-blocked clang channel, F-R0-14); multi-variant truth pre-U3; macro values in-band. These are Agent-side contract obligations (GA-3 two-part labels), not product gaps.
