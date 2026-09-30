# V5 — release synthesis

Do not rerun everything unless evidence is missing.

Verify:
- required case ledger has zero NOT_RUN
- every FAIL is classified
- product repo HEAD unchanged since V0
- public repo lock SHAs unchanged
- evidence paths exist
- exact GT and public-real results are kept separate

Produce:
- FINAL_VALIDATION_REPORT.md
- FINAL_VALIDATION_RESULTS.json
- FINDINGS.json
- PUBLIC_LOCK.json
- PERFORMANCE_SUMMARY.json
- KNOWN_VALIDATION_LIMITS.md

Final recommendation:
READY_FOR_REAL_PROJECT_TRIAL / READY_WITH_LIMITATIONS / NOT_READY / BLOCKED_ENVIRONMENT.
