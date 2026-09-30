# fw-context-mcp — Cross-Platform Hardening & Closeout Repair Wave

Single entrypoint: `ENTRYPOINT_T14.md`

Purpose: close the production-significant gaps found by the previous FINAL_HANDOFF_AUDIT without
re-running the entire T00–T14 research program.

Tier-1 platforms:
- Windows 11 native
- WSL2 Ubuntu on the WSL Linux filesystem (for example `~/src/fw-context-mcp`)

Interop path:
- Windows Harness/DSH -> WSL MCP over real stdio launch/bridge

Do not use `/mnt/c/...` as the primary Linux correctness filesystem. It may be tested separately as
an interop case.

Harness rules:
- only T14 may spawn agents
- children never spawn children
- research/diagnostic concurrency <= 2
- implementation concurrency = 1
- verifier concurrency = 1
- long output goes to artifact files
- no silent issue disappearance

DONE means all blocking issues are fixed and verified, and every other discovered issue is explicitly
FIXED, REJECTED_WITH_REASON, DEFERRED_WITH_REASON, or NOT_REPRODUCED.
