# Fixed state machine

`INIT -> W0_BASELINE -> ARCH_FREEZE -> IMPLEMENT -> CROSS_PLATFORM_PARITY -> REAL_STDIO -> RELEASE_AUDIT -> DONE`

`BLOCKED_HARD` is allowed only after retry exhaustion.

T14 advances exactly one edge at a time.

## INIT -> W0_BASELINE
Required:
- HEAD/status captured
- Windows environment captured
- WSL distro/kernel/Python captured
- WSL checkout confirmed on Linux filesystem
- issue ledger initialized
- no production changes

## W0_BASELINE -> ARCH_FREEZE
Required:
- Windows baseline
- WSL2 baseline
- current Windows/WSL lock/process/daemon call-site inventory
- real stdio cold-start probe on both
- no host fcntl shim accepted as product dependency

## ARCH_FREEZE -> IMPLEMENT
A13_ARCH must produce PLATFORM_ADR + accepted designs W1–W7.
W1–W4 may not be deferred.

## IMPLEMENT
Each task:
`UNSEEN -> RED_PROVED -> DESIGN_ACCEPTED -> IMPLEMENTED -> SELFTEST_GREEN -> VERIFY_PASS -> INTEGRATED`

No skip. One production-writing task at a time.

## CROSS_PLATFORM_PARITY
Required:
- Windows Native contract suite PASS
- WSL2 Ubuntu contract suite PASS from WSL-native filesystem
- same semantic fixtures canonical-match
- no host Python shim required
- platform-specific primitives confined to approved adapter boundary

## REAL_STDIO
Required real subprocess paths:
1. Windows client -> Windows MCP
2. WSL client -> WSL MCP
3. Windows/DSH-compatible launcher -> WSL MCP

Each must test cold start, initialize, tools/list, first call, second call, shutdown, restart.

## RELEASE_AUDIT -> DONE
Required:
- all blocking ledger entries FIXED + verified
- every other issue has final disposition
- no OPEN/UNKNOWN issue
- old-schema fixture/migration evidence
- docs/behavior parity
- final Windows + WSL smoke
- stress evidence
- clean tree

Retry:
1st failure -> same task repairs failed contract.
2nd -> verifier diagnoses implementation/test/architecture/environment.
3rd -> architect re-adjudicates.
Blocking unresolved -> BLOCKED_HARD.
