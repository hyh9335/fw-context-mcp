# Sole entrypoint — fw-context-mcp validator

You are the validation controller.

Read first, and only:
1. `MANIFEST.yaml`
2. `workflow/STATE_MACHINE.md`
3. `workflow/ANTI_SKIP_RULES.md`
4. `workflow/CONTEXT_PROTOCOL.md`

Do not load all prompts, fixtures, or public-repo plans at once.

This run is VALIDATION_ONLY:
- do not modify fw-context-mcp production source;
- do not "fix" failures;
- preserve exact reproductions;
- continue other independent cases after one case fails;
- classify every result.

Run:
V0_ENV -> V1_GOLDEN -> V2_PUBLIC_REAL -> V3_CROSS_PLATFORM -> V4_ADVERSARIAL -> V5_RELEASE.

Each phase requires an independent verifier before its gate closes.

A final PASS requires every required coverage cell to be:
PASS, EXPECTED_UNSUPPORTED, or BLOCKED_ENVIRONMENT with evidence.
Any WRONG_ANSWER, SILENT_DATA_LOSS, INDEX_DIVERGENCE, CORRUPTION, or STDIO_FAILURE makes the final
verdict NOT_READY unless it is proven to be a benchmark/oracle defect.

Never let a failed case disappear from the finding ledger.
