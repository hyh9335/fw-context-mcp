# A13_ARCH — architecture adjudicator

Read W0 facts, ledger, W1–W7 design seeds, repository, platform matrix.

Do not modify production code.

Freeze:
- platform service boundary
- process/liveness/identity/termination interface
- interprocess lock interface/backends
- daemon/control endpoint abstraction
- filesystem capability abstraction
- subprocess encoding policy
- migration/compat policy
- task dependency order
- explicit disposition for any rejected/deferred premise

Prefer a small `platform/` adapter layer over scattered `if os.name == ...`.

Produce PLATFORM_ADR.md and accepted_design/W1..W7.md.
