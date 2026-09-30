# Issue ledger — anti-skipping invariant

Every issue discovered in the prior audit or this wave gets a stable ID and remains in the ledger.

Final states:
- FIXED
- REJECTED_WITH_REASON
- DEFERRED_WITH_REASON
- NOT_REPRODUCED

Transient:
OPEN, REPRODUCED, DESIGN_ACCEPTED, IMPLEMENTED, VERIFY_FAILED, VERIFY_PASS

Before DONE:

`ALL_DISCOVERED == FIXED ∪ REJECTED_WITH_REASON ∪ DEFERRED_WITH_REASON ∪ NOT_REPRODUCED`

Sets must be disjoint. No OPEN/transient issue may remain.

Blocking issues assigned to W1–W4 cannot be DEFERRED unless the user explicitly changes scope.

FIXED requires:
- RED/reproduction
- patch commit
- GREEN
- independent verification
- integration proof
- required platform proof

REJECTED_WITH_REASON requires evidence explaining why the issue premise is wrong.

DEFERRED_WITH_REASON requires explicit user-visible limitation/safe fallback, rationale, and future test.

NOT_REPRODUCED requires exact attempted reproduction and environment.

Seed issue IDs:
AUD-R1 Windows/POSIX interprocess lock + daemon lifecycle
AUD-R2 real stdio transport
AUD-R3 declaration-only override false negative
AUD-R4 KEEP-root dead-code false positive
AUD-R5 semantic diff promised-but-absent
AUD-R6 PID reuse / kill ownership
AUD-R7 old-schema fixture missing
AUD-R8 docs/behavior drift
AUD-R9 raw CRLF exact-on-disk mismatch
AUD-R10 raw-search budget mismatch
AUD-R11 large-project stress missing
AUD-R12 no Linux/WSL full regression
AUD-R13 host fcntl no-op shim dependency
