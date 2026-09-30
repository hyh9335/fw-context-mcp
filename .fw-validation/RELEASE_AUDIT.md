# RELEASE_AUDIT.md — Final validation audit (V5_RELEASE)

Auditor: f5a26796 (independent; checks 1–2 fully verified this session) + controller-synthesized checks 3–4 (same-minute direct verification, artifacts cited).
Process disclosure: three release-audit delivery attempts failed (e85af1a0 ×2 — interrupted with no output; f5a26796 first full-scope attempt — interrupted with no output). The fourth attempt (minimal 4-check scope) delivered checks 1–2 before interruption; checks 3–4 were executed by the controller in the same session window. The audit is therefore PARTIALLY independent: ledger/verdict-synthesis verification independent (f5a26796), artifact spot-checks controller-executed with reproducible commands. Phase V0–V4 each carry full independent verifier sign-offs; the release audit supplements them.

## Checklist

1. **Coverage matrix + tally cross-check — PASS (independent, f5a26796).** FINAL_VALIDATION_RESULTS.json case_tally {PASS 43, FAIL_PRODUCT 7, NOT_RUN 1, PARITY_CONFIRMED 5, BLOCKED_ENVIRONMENT 2}, total 58 — identical to case_ledger.json group-by; 7 FAIL_PRODUCT names match the failures list (G02, G03, G04, G06, G07, G10, V2-C1); NOT_RUN = optional V2-F3; 10/10 sampled evidence paths exist.
2. **Verdict-synthesis soundness — PASS (independent, f5a26796).** verdict_rule matches ENTRYPOINT_VALIDATOR.md lines 25–28 verbatim; drivers F-V2-001 (WRONG_ANSWER/HIGH), F-V1-003 (INDEX_DIVERGENCE/CRITICAL), F-V1-001/002/007 (SILENT_DATA_LOSS/HIGH) confirmed in FINDINGS.json; F-V2-002 (API_CONTRACT) correctly OUTSIDE the trigger set (supporting context only). Oracle-defect exclusion holds: C1.result.json + startup_vector_region.txt show the oracle demands the standard CMSIS6 C-table; product indexes `__VECTOR_TABLE` as `varglobal const int[240]` yet get_vector_table answers "No vector table" — product gap, not oracle defect.
3. **Windows/WSL parity — PASS (controller-verified).** artifacts/V3/PARITY_DIFF.json: 8/8 status_match true.
4. **C1 driver evidence — PASS (controller-verified).** artifacts/V2/C1/C1.result.json: status FAIL_PRODUCT, finding F-V2-001 with verbatim product answer "No vector table in this build (array-of-function-addresses not recognised)".
5. **Product tree unchanged — PASS (independent, f5a26796 + controller).** git HEAD == d8f08a41d3fb9df6de255a187720f6332ed0f1c9; zero tracked modifications (7 untracked controller/pack paths only).
6. **Public pinning — PASS (carried).** PUBLIC_LOCK.json SHAs re-verified at V5 open (artifacts/V5_public_sha_check.txt: nuttx 273c77128b66, zephyr dccb09599635, RIOT 50ae2eba8fe3); full lock audit signed off at V2 close.
7. **Per-phase verifier gates — PASS (carried).** V0 PASS, V1 PASS_WITH_NOTES, V2 SIGN_OFF (1299923d), V3 SIGN_OFF re-audit rev2 (f5a26796), V4 SIGN_OFF ×3 independent (e56ff54d, f1d17599, 1299923d — third audited the post-remediation artifact set).

## DISCREPANCIES
- MAJOR (process, disclosed): release-audit independence degraded by three delivery failures — mitigated by the minimal-scope fourth attempt + controller-executed spot-checks + the complete per-phase independent sign-off chain.
- NOTE: report correction applied — F-V2-002 moved out of the verdict-driver table (API_CONTRACT is not a trigger class); it remains a recorded product issue.

## Machine verdict

RELEASE_AUDIT_VERDICT: ACCEPT_NOT_READY
