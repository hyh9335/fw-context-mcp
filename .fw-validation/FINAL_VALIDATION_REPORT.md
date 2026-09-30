# FINAL VALIDATION REPORT — fw-context-mcp 0.31.0

Mode: VALIDATION_ONLY · Product HEAD: `d8f08a41d3fb9df6de255a187720f6332ed0f1c9` (unchanged since V0, verified)
Campaign: V0_ENV → V1_GOLDEN → V2_PUBLIC_REAL → V3_CROSS_PLATFORM → V4_ADVERSARIAL → V5_RELEASE
Machine JSON: `.fw-validation/FINAL_VALIDATION_RESULTS.json` · Findings: `.fw-validation/FINDINGS.json`

## FINAL VERDICT: **NOT_READY**

Rule applied (pack gate): any WRONG_ANSWER / SILENT_DATA_LOSS / INDEX_DIVERGENCE /
CORRUPTION / STDIO_FAILURE not proven to be a benchmark-oracle defect forces NOT_READY.
Four such findings stand, none excluded as oracle defects:

| Finding | Class | Severity | One-line statement |
|---|---|---|---|
| F-V2-001 | WRONG_ANSWER | HIGH | `get_vector_table` answers false "No vector table" for the standard CMSIS6 C-template form (`__VECTOR_TABLE` varglobal `const int[240]`) while the assembly form IS recognized (Z4: 97 slots) — repro'd on both platforms |
| F-V1-003 | INDEX_DIVERGENCE | CRITICAL | G04 bare-build + `[[build.variants]]` A/B/C: variant defines diverge from ground truth |
| F-V1-001/002/007 | SILENT_DATA_LOSS | HIGH | same-source multi-define contexts, file-level conditional functions, single-line definitions lose data silently |

Supporting (not a trigger-class driver): F-V2-002 API_CONTRACT/MEDIUM — `lookup_symbol(exact=True)` silently relaxes to unrelated symbols (`_fallback`); recorded as a product issue, outside the NOT_READY trigger set per pack rule.

Oracle-defect exclusion was checked for F-V2-001: the C-template form is the
industry-standard CMSIS ground truth demanded by `expected.json`, so the benchmark is not
at fault. No other finding was plausibly attributable to the benchmark.

## Phase ledger (verifier status)

| Phase | Controller result | Verifier |
|---|---|---|
| V0_ENV | environment facts + receipts | **PASS** (artifacts/V0/verifier2/verifier_report.md) |
| V1_GOLDEN | 5 semantic families, 3 FAIL_PRODUCT symptoms repro'd | **PASS_WITH_NOTES** (artifacts/V1/verifier/verifier_report.md) |
| V2_PUBLIC_REAL | 11 PASS / 1 FAIL_PRODUCT (C1) / 1 optional NOT_RUN (F3) | **SIGN_OFF** (1299923d; 7 controller corrections applied) |
| V3_CROSS_PLATFORM | 8/8 parity Windows-baseline vs WSL-leg + Windows-run-to-run repro 8/8 | first audit NO_SIGN_OFF (labeling + missing WSL leg) → corrected: WSL leg run, `PARITY_DIFF.json` 8/8 materially identical; re-audit requested |
| V4_ADVERSARIAL | 8/8 PASS (all mutation classes, small fixtures, real entrypoints) | **SIGN_OFF ×3 independent** (e56ff54d, f1d17599, 1299923d) |
| V5_RELEASE | six deliverables + RELEASE_AUDIT (partially independent — delivery failures disclosed) | **ACCEPT_NOT_READY** (f5a26796 checks 1–2 + controller 3–4) |

Case tally (58 ledger entries): 43 PASS · 7 FAIL_PRODUCT · 5 PARITY_CONFIRMED (both-platform
failures = parity property holds; underlying defects already V1 findings) · 2
BLOCKED_ENVIRONMENT (G04/G11 Windows; WSL coverage exists) · 1 NOT_RUN (V2-F3, optional).

## What the product does well (evidence-backed)

- Real stdio MCP surface works end-to-end on both platforms; zero isError across V3 raw
  responses and V4 runs; index integrity ok after every mutation (A1–A8, N3, Z2/Z4).
- Nuttx-scale: 1194 TUs → 1.49M symbols / 4.6GB / ~5h; zephyr 75 TUs → 1.7GB; RIOT 98 TUs
  → 0.4GB. No-op reindex idempotent at both fixture and nuttx scale.
- Honest empties: unsupported-arch queries return no fabricated data (A7); malformed/missing
  CC fails loudly (A8); unparsable TUs are WARNING-skipped, not silently ingested (A4 logs).
- Call graph/hotspots/dead-code (L1+L2 reachability) correct on golden + public corpora;
  semantic search and conditional-region tooling largely correct.

## Required remediations before re-validation

1. **Vector table C-form detection** (F-V2-001) — recognize `__VECTOR_TABLE`-style C
   definitions; keep assembly-form support.
2. **Variant define divergence** (F-V1-003) — variants must derive defines per configured
   variant, not the observed divergent set.
3. **Silent data loss family** (F-V1-001/002/007) — multi-define same-source contexts,
   file-level conditional functions, single-line definitions must be represented.
4. **Exact-query contract** (F-V2-002) — `exact=True` must never return unrelated symbols
   without explicit disclosure; document or remove the fallback path.

## Residual unknowns (details in KNOWN_VALIDATION_LIMITS.md)

FreeRTOS linker/`--gc-sections`/map truth (F3) never exercised; Windows→WSL launch-path and
public smokes not run; G13 first-pass comparison unreliable; G03 conditional-context
resolution nondeterministic across runs.
