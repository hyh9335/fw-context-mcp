# Platform matrix

## Windows Native
- NTFS checkout
- no fake/system `fcntl.py` shim
- real stdio MCP
- Windows backend contract
- concurrent index-process tests
- process identity/termination tests

## WSL2 Ubuntu
Primary Linux test platform.
- checkout under WSL Linux filesystem, e.g. `~/src/fw-context-mcp`
- index DB also on WSL Linux filesystem
- real fcntl, /proc, AF_UNIX, POSIX signals
- real stdio MCP
- same semantic fixtures as Windows

## /mnt/c interop
Optional compatibility test only. Never counts as Linux correctness.

## Windows Harness -> WSL MCP
Test actual intended bridge, e.g. wsl.exe launch + stdio.
