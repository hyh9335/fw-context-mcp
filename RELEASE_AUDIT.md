# RELEASE_AUDIT.md — Final validation audit, fw-context-mcp 0.31.0

- Auditor role: A_RELEASE.md (final validation auditor), per pack `fwctx_validation_benchmark_pack/prompts/A_RELEASE.md`
- Date: 2026-09-18 (single turn; interrupted mid-verification, gaps named below)
- Inputs read directly: `.fw-validation/state.json`, `FINAL_VALIDATION_RESULTS.json`, `case_ledger.json`, `findings.json`, `PERFORMANCE_SUMMARY.json`, `PUBLIC_LOCK.json`, `artifacts/V5_public_sha_check.txt`, pack prompts `A_RELEASE.md`/`A_VERIFY.md`, `tests/COVERAGE_MATRIX.md`; artifact trees enumerated by glob.
- Read-only audit. No product sources, pack files, or expected.json modified.

## Checklist (A_RELEASE.md scope) — verification status and citations

### 1. Exact coverage matrix — VERIFIED with discrepancies
- `FINAL_VALIDATION_RESULTS.json` `case_tally` = {PASS 43, FAIL_PRODUCT 7, NOT_RUN 1, PARITY_CONFIRMED 5, BLOCKED_ENVIRONMENT 2}, `total_cases` 58. I independently recounted `case_ledger.json` `cases[]`: V0 15 (all PASS), V1 12 rows (6 PASS: G01/G05/G08/G09/G11/G12; 6 FAIL_PRODUCT: G02/G03/G04/G06/G07/G10), V2 13 (11 PASS, 1 FAIL_PRODUCT C1, 1 NOT_RUN F3), V3 10 (3 PASS, 5 PARITY_CONFIRMED, 2 BLOCKED_ENVIRONMENT), V4 8 (8 PASS). Sum = 58 and per-status sums match the published tally exactly. `failures[]` matches the 7 FAIL_PRODUCT ledger rows. `required_not_run: []` is consistent (V2-F3 correctly in `optional_not_run`).
- **MAJOR D-1:** `G13_fanout_scale` is listed in `case_ledger.json` `planned_required_case_ids` and is a required row of `fwctx_validation_benchmark_pack/tests/COVERAGE_MATRIX.md` ("scale/fanout | G13 | Zephyr/NuttX/RIOT"), but has **no row in `case_ledger.json` `cases[]`** and **no status cell in the FINAL_VALIDATION_RESULTS coverage matrix** (only `residuals[]` pointer "see V1 verifier notes"). The published `total_cases: 58` is therefore derived from an incomplete ledger. The underlying work and evidence exist (`artifacts/V1/G13/` result + raw transcripts incl. all size variants; `state.json` controller_progress V1 "G13: PASS (v5) … PASS at 1/10/50/100/250"; V1 verifier notes record G13 closure), so this is a bookkeeping/coverage-matrix defect, not missing work — but it makes the "exact coverage matrix" inexact.
- **MINOR D-2:** `coverage_matrix.golden_windows` is an empty array `[]`, while its sibling cell says "G01-G03,G05-G07,G10,G12 — see golden_windows (V3-WSL leg reproduces all 8)". The pointer resolves to nothing; the 8 Windows golden statuses actually live in `artifacts/V3/*/G*.result.json` + ledger rows.
- **MINOR D-3:** G08/G09 golden cells (required rows "Zephyr async" / "FreeRTOS async") are absent from the matrix entirely; their PASS statuses + evidence exist in ledger rows only.
- **MINOR D-4:** COVERAGE_MATRIX row "RISC-V vector | G11 | RIOT/NuttX RISC-V source" has no public-real leg represented in the matrix or the V2 scenario set (R1/R2 are native targets); coverage is golden-only via G11 (WSL PASS).

### 2. Missing required cells — see D-1..D-4 above
No required cell is missing evidence at the artifact level; the gaps are in matrix/ledger enumeration.

### 3. Public corpus pinning — VERIFIED
- `PUBLIC_LOCK.json`: 6/6 repos with exact resolved SHAs (zephyr `dccb09599635bdff17633fa7e9dab014b91dce90`, nuttx `273c77128b6698f0c95f0d7cde1d0bb803782021`, nuttx-apps `20ffb1a3…`, freertos `3a22924e…`, cmsis `45dab712…`, riot `50ae2eba8fe3c533539e68f1a64fd34d66c8d14b`), pin rule "never float main", honest TLS-MITM disclosure with SHA cross-verification.
- `artifacts/V5_public_sha_check.txt` (read via PowerShell; the plain reader flags it binary — encoding artifact): N1+N2 `nuttx-src 273c77128b66`, Z4 `zephyr-src dccb09599635`, R1 `riot-src 50ae2eba8fe3` — all three spot SHAs match the lock. **NOTE N-1:** the artifact is incomplete — headers for work dirs Z1/Z2/Z3/C1/F1 carry no SHA lines and the file ends at the F1 header (apparently truncated); CMSIS/FreeRTOS/app repos were not re-verified in this spot check (claimed done at V2 close by verifier 1299923d; not independently re-verified here).

### 4. GT independence — NOT INDEPENDENTLY VERIFIED (named gap G-A)
- Structural separation VERIFIED: golden (V1 `artifacts/V1/`, V3 `artifacts/V3/`) vs public-real (`artifacts/V2/`) vs adversarial (`artifacts/V4/`) are disjoint artifact trees (glob enumeration).
- The pack (incl. golden expected.json) lives inside the tracked product tree, so "expected.json never modified" would be proven by `git status` porcelain — **I did not run git** (turn interrupted). `state.json` asserts `product_tree_unchanged_since_start: true` and "6 untracked paths only; zero tracked-file modifications"; this remains controller-asserted, not auditor-verified.

### 5. Windows/WSL parity — PARTIAL (named gap G-B)
- `artifacts/V3/PARITY_DIFF.json` exists (globbed) but I did not read its content. Ledger V3 rows V3-G01/02/03/05/06/07/10/12 each cite triple evidence (V3 + V1 + `artifacts/V3/wsl/` result JSONs) with per-case "parity SAME" descriptions; `state.json` records 8/8 identical classifications and the remediation loop (first audit NO_SIGN_OFF → rev2 f5a26796 with WSL leg + PARITY_DIFF.json). Content-level confirmation pending.

### 6. stdio evidence — PARTIAL (named gap G-C)
- Transcript existence VERIFIED by glob for V1 (per-case `raw/mcp_transcript.txt`, all G13 size variants), V2 (per-case `mcp_transcript_*.txt` incl. C1 c1/c1b/c1c), V3 (8 Windows + wsl legs), V0 (`stdio_win/wsl_transcript.json`). V4 transcript tree not globbed before interruption. Content-level spot-check (JSON-RPC framing, per-case pairing) NOT performed. Evidence is artifact/file-based, not prose-only, per the trees enumerated — A_VERIFY.md "FAIL if evidence is prose-only" not triggered by anything observed.

### 7. Incremental/full invariants — NOT CONTENT-VERIFIED (named gap G-D)
- Ledger cites per-case result JSONs (V4-A4/A5/A6, V2-N3 + `n3_post_db_corrected.json`); `PERFORMANCE_SUMMARY.json` records N3 delta +1 probe / no-op delta=0 and reindex costs. The specific strings cited in the campaign preconditions (A4 `raw/reindex_delete.log` "TUs to index: 1 / 1 updated, 0 skipped"; A2 `materialize.log` exit 0) are recorded in `state.json` V4 verifier corrections but were not independently opened by me. Two independent V4 verifier sign-offs (e56ff54d: 4 cases materially verified vs raw logs; f1d17599 relaunch: 6/6 checklist) are recorded in `state.json` and `case_ledger.json` `phase_verdicts`.

### 8. Scale results — VERIFIED at summary level (named gap G-E)
- `PERFORMANCE_SUMMARY.json` N1 row (1194 TUs / 1,494,309 syms / 58,254 files / 4.6 GB / ~5 h) matches ledger V2-N1 description; R1 file_count 9755 "corrected from transcription 10721" matches the V2 verifier correction in `state.json`; Zephyr 75 TUs / 1.7 GB matches build status. Cross-check against the full text of `artifacts/V2/V2_SUMMARY.md` not performed.

### 9. Product tree unchanged — NOT INDEPENDENTLY VERIFIED (named gap G-F)
- `state.json` `product.head` = `d8f08a41d3fb9df6de255a187720f6332ed0f1c9`, `worktree_status` "6 untracked paths only; zero tracked-file modifications"; same values in `FINAL_VALIDATION_RESULTS.json.product`. **I did not run `git rev-parse`/`git status`** before interruption.

## Verdict synthesis check
- `FINAL_VALIDATION_RESULTS.json.verdict_rule` ("any WRONG_ANSWER / SILENT_DATA_LOSS / INDEX_DIVERGENCE / CORRUPTION / STDIO_FAILURE not proven a benchmark/oracle defect forces NOT_READY") is consistent with — strictly broader than — pack A_RELEASE.md ("Any silent wrong answer or index divergence => NOT_READY"). Rule application direction is correct.
- Drivers (F-V2-001 WRONG_ANSWER HIGH; F-V1-003 INDEX_DIVERGENCE CRITICAL; F-V1-001/002/007 SILENT_DATA_LOSS HIGH) match `findings.json` classifications verbatim.
- **MINOR D-5 (verdict-neutral):** `findings.json` also holds **F-V1-005 classified WRONG_ANSWER (OPEN, HIGH)** — live call chain (main→live_root→live_leaf) reported `graph_unreachable` on compile_commands-only builds — which is a NOT_READY-forcing class but is **absent from `verdict_drivers[]`** (as are SILENT_DATA_LOSS findings F-V1-004/006/008). Since the verdict is already NOT_READY, the omission errs in the safe direction, but the driver list is incomplete vs the findings register.
- F-V2-002 (API_CONTRACT, exact=True silent fallback) is correctly NOT a driver — API_CONTRACT is not in the forcing list — and is properly disclosed.
- Benchmark-defect exclusion for F-V2-001: the recorded reasoning is internally consistent — the oracle (`expected` in findings F-V2-001, oracle dump `artifacts/V2/C1/startup_vector_region.txt`) demands the industry-standard CMSIS6 C-template `__VECTOR_TABLE` form; the one genuine controller-side oracle defect (first C1 attempt with incomplete CC missing `-DARMCM3`, per ledger note) was corrected **before** the verdict; the assembly form is recognized (Z4, 97 slots), isolating the gap to C-form detection. I did not independently open C1.result.json/responses (named gap G-G), so I accept the exclusion as recorded — which is the correct audit posture given the verdict (NOT_READY) does not depend on the exclusion succeeding.

## Discrepancy register
- **MAJOR D-1** — G13 required case missing from ledger `cases[]` and matrix (bookkeeping; evidence exists).
- **MINOR D-2** — `golden_windows: []` empty pointer in matrix.
- **MINOR D-3** — G08/G09 golden cells absent from matrix.
- **MINOR D-4** — RISC-V public-real leg unrepresented.
- **MINOR D-5** — `verdict_drivers[]` omits OPEN WRONG_ANSWER F-V1-005 (verdict-neutral).
- **MINOR D-6** — `FINAL_VALIDATION_RESULTS.json.phases.V4_ADVERSARIAL.status` = "AUDIT_IN_FLIGHT" (stale) while `state.json` + ledger record dual independent SIGN_OFF; deliverable JSON out of sync with state.
- **NOTE N-1** — sha-check artifact truncated (Z1/Z2/Z3/C1/F1 headers without SHAs).
- **NOTE N-2** — findings file is `findings.json`; state.json deliverable list says "FINDINGS.json" (case-only nit).
- **NOTE N-3** — `artifacts/V5_public_sha_check.txt` flagged binary by the text reader (encoding artifact; content readable via Get-Content).

## Named verification gaps (interrupted turn)
G-A: no git commands run (items 4, 9). G-B: PARITY_DIFF.json content unread. G-C: transcript content spot-check not done; V4 tree not globbed. G-D: V4 A4/A5/A6 + N3 result JSONs and quoted log lines not opened. G-E: V2_SUMMARY.md cross-read not done. G-G: C1 evidence files not opened.

## Machine verdict
RELEASE_AUDIT_VERDICT: ACCEPT_NOT_READY — the campaign's NOT_READY synthesis is correctly derived from the pack rule and the findings register (drivers match classifications; rule application direction correct; F-V1-005 omission is verdict-neutral; F-V2-002 correctly excluded from the forcing set). Coverage completeness has real bookkeeping defects (D-1..D-6) that must be repaired in the ledger/matrix before any future READY claim, but none of them undermines the NOT_READY verdict or indicates fabricated evidence. This audit does not re-litigate the product verdict; it audits synthesis and coverage completeness.
