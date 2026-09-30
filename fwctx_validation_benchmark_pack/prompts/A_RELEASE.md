# Final validation auditor

Read final case ledger and reports only after V1–V4 verifier PASS/qualified PASS.

Audit:
- exact coverage matrix
- missing required cells
- public corpus pinning
- GT independence
- Windows/WSL parity
- stdio evidence
- incremental/full invariants
- scale results
- product tree unchanged

Any silent wrong answer or index divergence => NOT_READY.

Output RELEASE_AUDIT.md and a compact machine verdict.
