# Validation state machine

`INIT -> V0_ENV -> V1_GOLDEN -> V2_PUBLIC_REAL -> V3_CROSS_PLATFORM -> V4_ADVERSARIAL -> V5_RELEASE -> DONE`

No transition may be skipped.

## V0_ENV gate
Requires:
- fw-context HEAD and package version
- Windows runtime facts
- WSL distro/kernel/runtime facts
- real stdio smoke on both
- compiler/linker/tool inventory
- public-download storage path
- WSL path proven to be Linux filesystem

## V1_GOLDEN gate
Every required golden fixture must have:
- materialized compile database / build inputs
- exact expected file
- product query results
- independent oracle result where applicable
- normalized comparison
- verifier disposition

## V2_PUBLIC_REAL gate
Each selected public corpus must have:
- repository URL
- requested tag/release
- actual resolved SHA
- license file path
- build command
- compile_commands provenance
- selected test scenarios
- oracle method
- results
- no unclassified failure

## V3_CROSS_PLATFORM gate
Required golden/core public subset:
- Windows
- WSL
- canonical semantic parity
- real stdio on both
- Windows -> WSL stdio bridge if available

## V4_ADVERSARIAL gate
Required mutations:
- shared header change
- macro/config change
- build variant switch
- source deletion/addition
- stale DB/no-op reindex
- repeated reindex x3
- partial/unsupported architecture query
- malformed or missing optional environment components

## V5_RELEASE gate
Requires:
- coverage matrix complete
- all findings classified
- no missing evidence
- final report + machine JSON
- product tree unchanged from validation start

Possible final verdicts:
- READY_FOR_REAL_PROJECT_TRIAL
- READY_WITH_LIMITATIONS
- NOT_READY
- BLOCKED_ENVIRONMENT
