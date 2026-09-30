# Anti-skip rules

Every planned case has a stable CASE_ID.

Allowed case states:
- PASS
- FAIL_PRODUCT
- FAIL_ORACLE
- EXPECTED_UNSUPPORTED
- BLOCKED_ENVIRONMENT
- NOT_RUN

Before DONE:
- NOT_RUN count must be zero for all REQUIRED cases.
- FAIL_PRODUCT must remain visible in the final report.
- BLOCKED_ENVIRONMENT requires exact missing dependency and reproduction command.
- EXPECTED_UNSUPPORTED requires a user-visible explicit unsupported/partial signal from fw-context.

Hard evidence rule:
PASS requires command + exit code + raw result artifact + expected/oracle + comparison artifact.

No "looks right", "likely", "should pass", or child-agent prose can close a case.

Product-failure severity:
- P0: destructive/corrupting or silent database corruption
- P1: silent wrong semantic answer
- P2: explicit wrong/failed query or transport failure
- P3: performance/docs/ergonomics

The controller must maintain:
`.fw-validation/case_ledger.json`

and verify:
`planned_required_case_ids == terminal_required_case_ids`
before release.
