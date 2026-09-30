# Independent verifier

Fresh context; do not modify product or expected files.

Given a phase result:
- inspect commands and artifacts
- rerun a representative subset
- verify oracle independence
- inspect negative assertions
- check canonicalization is not hiding semantic differences
- check public repo SHA and build config
- check any EXPECTED_UNSUPPORTED is explicit in product output

For V1, independently rerun at least one case from each semantic family:
contexts, conditionals, graph/callback, linker/vector, raw.

For V2, independently rerun one Zephyr and one non-Zephyr scenario.

FAIL if evidence is prose-only.
