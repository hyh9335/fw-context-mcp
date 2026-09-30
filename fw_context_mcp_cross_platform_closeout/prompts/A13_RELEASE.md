# A13_RELEASE — release audit

Fresh checkouts/index roots where practical.

Run:
- Windows regression
- WSL2 regression from Linux filesystem
- semantic parity
- real stdio Windows
- real stdio WSL
- Windows -> WSL stdio bridge
- old-schema migration
- docs-behavior parity
- stress/fanout
- issue-ledger equality rule

No blocking issue may be deferred.
No OPEN issue may remain.

Produce FINAL_RELEASE_AUDIT.md, FINAL_TEST_RESULTS.json,
FINAL_ISSUE_LEDGER.json, KNOWN_LIMITATIONS.md.
