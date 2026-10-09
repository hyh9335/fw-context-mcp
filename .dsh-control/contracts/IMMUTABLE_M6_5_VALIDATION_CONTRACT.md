# IMMUTABLE M6.5 VALIDATION CONTRACT

> **Immutable.** This contract freezes the M6.5 Goal:
> `ADVERSARIAL_VALIDATION_CORNER_CASE_COVERAGE_CLOSURE`

## §1. Goal

Expand adversarial, differential, property-based, mutation, real-project,
multi-Build and lifecycle testing until the M6 test system demonstrates
strong coverage of C/C++ variability corner cases and can reliably detect
deliberately injected structural-reuse defects.

## §2. Hard Constraints

- **No production authority transition is allowed.** M6 remains shadow-only.
  The M6 store MUST NOT be imported into the production query path. The 29
  MCP tools MUST continue to use the v2 whole-TU store as Ground Truth.
- **No reuse-ratio optimization work is allowed** unless required to fix a
  correctness defect discovered during validation.
- **No threshold movement** to pass a gate. Frozen M6 thresholds stay frozen.
- Production defaults MUST NOT change: `legacy_compatibility_mode=False`,
  `v2_write_authority=True`.

## §3. Preserve Existing Accepted Gates

Before starting M6.5 work, the following gates MUST pass at the durable
checkpoint:
- M3B regression (scan_authority_state.py exit 0)
- M4 regression (scan_write_authority.py exit 0)
- M5 regression (run_m5_stage_gate.py exit 0, 39/39 clauses)
- M6 Hard Gate (run_m6_stage_gate.py exit 0, 13/13 gates)

If any fail, repair autonomously before M6.5 work begins.

## §4. Scope of Validation Work

M6.5 covers these validation domains, each requiring its own suite:
1. Test-coverage archaeology (inventory every current M6 test)
2. All 12 production fact kinds must be covered (the missing 12th MUST be covered)
3. C preprocessor feature matrix (§5 of the program)
4. Combinatorial macro attacks (pairwise + targeted 3-way)
5. C type / ABI corner cases
6. Linkage and symbol-identity attacks
7. Function pointer / indirect-call testing
8. Cross-TU dependency closure
9. Include-resolution attacks
10. Generated header attacks
11. Property-based / differential generation
12. Differential fuzzing safety (bounded, reproducible)
13. Metamorphic testing (equivalent + non-equivalent transformations)
14. Mutation test expansion (per critical safety category)
15. False-confidence tests (high sharing + wrong answer = MUST fail)
16. Real corpus — primary (FreeRTOS-Kernel)
17. FreeRTOS build matrix (2/5/10/20 configurations)
18. FreeRTOS ground truth (independent Clang indexing)
19. FreeRTOS query matrix (MCP capabilities)
20. Real corpus — secondary (Mbed TLS)
21. Optional third corpus (TinyUSB)
22. Multi-build scaling (2/5/10/20 Builds)
23. Lifecycle combination tests
24. Concurrency tests
25. Query-path exactness (v2 vs M6 projection)
26. Performance baseline (P50/P95/P99)

## §5. Coverage Evidence

Produce machine-readable `.dsh-control/evidence/m6_5_coverage.json` with
coverage status for each domain.

## §6. M6.5 Hard Gate

Implement `run_m6_5_validation_gate.py`. M6.5 PASS requires at minimum
(see §31 of the program for the full list):
- all previous M6 gates PASS
- 12/12 fact kinds covered
- C macro feature matrix PASS
- required macro-combination matrix PASS
- ABI/type suite PASS
- linkage suite PASS
- function-pointer suite PASS
- cross-TU dependency suite PASS
- include-resolution suite PASS
- generated-header suite PASS
- property-based differential suite PASS
- metamorphic suite PASS
- all critical mutation categories detected
- REAL_CORPUS_FACT_KIND_COVERAGE == 12/12
- primary real embedded corpus differential parity PASS
- FALSE_SHARED_SEMANTIC_FACTS == 0
- QUERY_PARITY_FAILURES == 0
- lifecycle combination suite PASS
- query latency baseline recorded
- M3B/M4/M5/M6 PASS
- Fresh Verifier PASS
- Fresh Guardian ACCEPT_COMPLETION
- durable checkpoint

## §7. Final Verdict

Allowed verdicts:
- `M6_5_VALIDATION_COVERAGE_CLOSED`
- `M6_5_VALIDATION_NOT_STRONG_ENOUGH`

If ordinary test gaps remain, fix them autonomously. Do not return to the
user between test expansions.

## §8. Forbidden Actions

M6.5 MUST NOT:
- import M6 store into normal production query authority
- switch the 29 MCP tools
- remove whole-TU v2 GT
- change production defaults
- add new pointer-analysis capabilities beyond what M6 currently claims
- brute-force full Cartesian expansion of macro combinations

## §9. Truth Source Hierarchy

- FULL_BUILD_GT (independent Clang index per Build) remains ground truth
  for parity checks
- The v2 whole-TU representation remains the production Ground Truth
- M6 store remains shadow — its projections are validated, not authoritative

## §10. Fresh Reviewer Requirements

The Fresh Verifier MUST independently try to break the test system:
- add or select macro combinations not explicitly named in the original suite
- inspect mutation blind spots
- independently choose FreeRTOS configurations
- regenerate GT
- inspect cross-TU stale-reuse risk
- inspect linkage/local symbol risk

The Fresh Guardian MUST answer the 12 questions of §34.

---

**Frozen at:** HEAD `c102073` (M6 completion checkpoint).
**Baseline gates:** M3B PASS, M4 PASS, M5 PASS, M6 PASS (13/13).
