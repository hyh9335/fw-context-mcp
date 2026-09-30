# CURRENT CAPABILITY MAP (S0-revised: T0/T1/T2) — supersedes archive_r7/CURRENT_CAPABILITY_MAP.md

Product: fw-context-mcp v0.31.0 at HEAD d8f08a4 (Python MCP server; frozen tree). Trust classes ratified by S0:

- **T0 — directly trusted WITHIN A NAMED SCOPE**: frozen validation evidence demonstrates correctness for the exact claimed semantics; no known defect contradicts the claim; scope explicitly stated. T0 still carries provenance — trusted never means ground truth.
- **T1 — gateway-safe / fail-closed**: raw surface has a known defect/incompleteness, but K1 can deterministically detect the unsafe condition and refuse, repair via independent evidence, or explicitly mark incompleteness. T1 claims used ONLY through K1, never directly from the raw substrate.
- **T2 — quarantined**: known defects cannot be safely compensated by a deterministic gateway wrapper for the intended claim.

Promotion rule (S0): no surface is promoted merely because a detector exists — T1 requires the unsafe condition to be DETERMINISTICALLY detectable by K1 for the exact claim; everything else stays T2.

## T0 — directly trusted (named scopes only)
| Claim (scope) | Surface | Evidence basis | Provenance duty |
|---|---|---|---|
| Conditional structure of the active build for MULTI-LINE regions: skeleton + ifdef-filtered content, inactive branches blank, line numbers stable | read_file (active mode), list_conditional_regions | F-R0-13 PASS set; F-R0-05..09 do not contradict multi-line-region semantics; single-line storage defect (F-V1-002/007) is out of this scope | fact log row w/ tier=T0, file+line cite; any single-line extraction exits T0 scope → T2 rules |
| Direct call/reference edges (proven calls only, single-variant index) | find_callers / find_call_path / find_references with include_possible=false | F-R0-08 direct flows PASS; F-V1-004 contradicts only the transitive may-target claim, not the proven-edge claim | cite edge kind + certainty; may-target rows never emitted from T0 scope |
| Symbol existence + definition location for exact names (multi-line definitions) | lookup_symbol exact=true (definition rows) | F-R0-13; F-V2-002 contradicts the fallback/substitution behavior, which is out of the exact-match-definition-row scope | cite file:line-end_line |
| Build-context condition text (in-band): enclosing condition chains + macro_inputs.def_source | explain_condition — CONDITION TEXT ONLY | F-R0-13; F-R0-15 nondeterminism affects repeat-run identity, not semantic content of one attested run | run-pinned context_id recorded (K1); one attested run per claim |

## T1 — gateway-safe / fail-closed (K1-mediated only)
| Claim | Surface + known defect | K1 deterministic check | Fail behavior |
|---|---|---|---|
| Exact-symbol answers without substitution | lookup_symbol/search_code with `_fallback` rows possible (F-V2-002) | reject any row carrying `_fallback`/`_did_you_mean`; absence answer = explicit "no exact match" + enumerated near-miss list labeled as non-evidence | refuse claim; emit absence with attempts |
| Complete enumeration of a fanout | result limits/truncation (F-V1-008) | detect limit/truncation signals; completeness claims refused; emit lower_bound + explicit truncation marker | mark incomplete; G7 plant test enforces |
| Cross-run stable condition identity | explain_condition nondeterminism (F-R0-15) | re-run comparison OR run-pinned context_id; mismatch ⇒ mark unstable, do not load-bear | waiver recorded or claim downgraded |
| Interrupt PRESENCE claims on assembly-form tables | get_vector_table asm form (honest-empty resolved FR-NEW-4) | form attestation + ≥2-surface corroboration (E5) before any presence claim | no presence claim without corroboration |
| Source-reachability-negative ("unreachable from ATTESTED known roots") | find_dead_code root sets (F-V1-005) | require root-set attestation (linker script/ENTRY present) + explicit caveat; "dead in final binary" claims NEVER emitted | emit unreachable-from-roots w/ caveat only |

## T2 — quarantined (defects not safely wrappable for the intended claim)
| Quarantined class | Finding(s) | Wave-1 treatment |
|---|---|---|
| Bare-generator multi-variant truth / cross-variant body claims | F-V1-003 (+ F-V1-002 variant form) | banned (single-variant per index; GA-4); re-earn: U3 measured (MEASURE-GAP-U3) |
| Macro-VALUE truth (stored/expanded values vs build truth) | F-V1-001 | labeled degraded-provenance instrument + G8 boundary policing; in-band channels = condition text (T0) + build-file -D flags (out-of-band); values never load-bearing |
| Single-line / small-fragment body storage claims | F-V1-007 (F-V1-002 subsumed where applicable) | ≥2-surface cross-check for any single-line claim; never load-bearing alone; wave-1 corpus case must NOT let this surface silently contribute (rubric item 4/8) |
| Transitive callback may-target completeness | F-V1-004 | uncertainty-labeled lower bound only (G2); family #2 re-earn |
| Dead-code FINAL-BINARY truth on compile_commands-only projects | F-V1-005 | out of wave-1; attested-unreachable (T1) is the only permitted form |
| CMSIS C-form vector detection / vector ABSENCE claims | F-V2-001 | E5 + corroboration; absence claims from vector surface never load-bearing; fix = Track-B S-gated |

## Disposition of ALL 15 validation finding IDs (authoritative count; F-V1-002 subsumed/amended by F-V1-007 where applicable)
- F-V0-001..F-V0-004 (environment/infra): outside substrate claims; environment via F-R0-14 constraints.
- F-V1-001 macro values → T2. F-V1-002 conditional body storage → T2 (subsumed by F-V1-007 class). F-V1-003 variant divergence → T2. F-V1-004 transitive callbacks → T2. F-V1-005 dead-code roots → T2 (final-binary truth) / T1 (attested-unreachable form). F-V1-006 slot-0 data symbol → T1 presence claims via G9-classification discipline (non-load-bearing wave-1); absence claims T2. F-V1-007 single-line storage → T2. F-V1-008 truncation → T1 (K1 truncation detection).
- F-V2-001 C-form vector false absence → T2. F-V2-002 exact-lookup fallback → T1 (deterministic `_fallback` rejection).
- F-V3-* / F-V4-001 (as present in FINDINGS.json): F-V4-001 long-session divergence → watch item M8 (NEUTRAL wave-1, quantified envelope required before long sessions).

## Substrate bounding facts (unchanged)
No in-band per-variant provenance signal at 0.31.0 (A4 UQ-1) → G1 = out-of-band build-manifest diff. Runtime queue semantics unreachable from source alone (scenario-03 ceiling) → two-part facts + forbidden-overclaims contract. Only F-V1-003 was BLOCKING_FOR_AGENT_MVP and is handled by the single-variant fence, not by a pre-MVP repair wave; all repairs remain S-gated Track-B pulled by measured misses (no hidden repair wave).
