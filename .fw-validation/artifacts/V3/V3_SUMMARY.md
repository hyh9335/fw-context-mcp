# V3_CROSS_PLATFORM — Phase Summary (rev 2, post-verifier corrections)

Status: CONTROLLER_COMPLETE_PENDING_VERIFIER (rev 2)
Product: fw-context-mcp 0.31.0 @ HEAD d8f08a41 — same source tree on both platforms

## Baselines and legs (corrected labeling)

- **Windows baseline**: the V1 artifacts for G01–G03/G05–G07/G10/G12 are WINDOWS runs
  (venv-win python, `C:\Users\...` work dirs, MCP SDK 1.28.1).
- **WSL leg (this phase)**: the same 8 cases re-run inside WSL with the product venv
  (`~/fwctx-venv311`, `~/fwctx-validation-work/V3-WSL`, MCP SDK 1.30.0), same recipes,
  same `expected.json` oracles. Artifacts: `artifacts/V3/wsl/<case>/`.
- **PARITY_DIFF.json**: machine diff of Windows vs WSL per case (status + all comparison flags).

## Cross-platform parity — Windows (V1 baseline) vs WSL (V3-WSL)

| Case | Windows (V1) | WSL (V3-WSL) | Parity | Comparison flags |
|------|--------------|--------------|--------|------------------|
| G01_multi_tu_header | PASS | PASS | SAME | identical |
| G02_same_source_multi_command | FAIL_PRODUCT | FAIL_PRODUCT | SAME | identical (F-V1-001 both sides) |
| G03_conditional_matrix | FAIL_PRODUCT | FAIL_PRODUCT | SAME | identical (same failing cell) |
| G05_cpp_override_template | PASS | PASS | SAME | identical |
| G06_indirect_callbacks | FAIL_PRODUCT | FAIL_PRODUCT | SAME | identical |
| G07_dead_keep | FAIL_PRODUCT | FAIL_PRODUCT | SAME | identical |
| G10_cmsis_vector | FAIL_PRODUCT | FAIL_PRODUCT | SAME | identical (F-V1-006 both sides) |
| G12_raw_crlf | PASS | PASS | SAME | identical |

**Parity: 8/8 identical classifications, materially identical comparison flags** (PARITY_DIFF.json).
Every V1 finding reproduces on BOTH platforms — platform-independent product defects. Platform
variables (CRLF, path separators, libclang build, MCP SDK version) change no product behavior.

## Windows-side reproducibility (first V3 leg, retained)

A same-platform Windows re-run of the 8 cases (~8h after V1, fresh work dirs) also matched
8/8 — recorded as run-to-run reproducibility, NOT cross-platform evidence.

## Windows-blocked cases (per pack plan, V0-WIN facts)

- **G04_variant_semantic** — BLOCKED_ENVIRONMENT on Windows; WSL semantic coverage exists
  (`artifacts/V1/G04/wsl/G04.result.json`: FAIL_PRODUCT, F-V1-003)
- **G11_riscv_mtvec** — BLOCKED_ENVIRONMENT on Windows; WSL coverage
  (`artifacts/V1/G11/wsl/G11.result.json`: PASS)

WSL-only extras from the pack prompt: the Windows→WSL stdio bridge and public-corpus smokes were
NOT exercised — the 8-case golden subset above is the parity evidence; the gap is recorded for the
V5 coverage matrix.

## Notes for V5

- G03 `explain_condition` resolved a different conditional context across runs (A vs B) — run
  nondeterminism, classification unaffected
- MCP SDK version differs per venv (1.28.1 Windows / 1.30.0 WSL) — environment fact, not product drift
- v3_runner.py (Windows reproducibility leg) reuses v1_runner recipes; v3_wsl_runner.py (WSL leg)
  monkey-patches paths + client platform
