# A13_VERIFY — independent verifier

Fresh context. Do not modify production code.

Read task contract, accepted design, diff/commit, test spec, implementer receipt.
Rerun required tests independently.

Reject:
- self-oracle tests
- in-memory-only evidence for stdio
- Windows success dependent on fake fcntl shim
- Linux test only on /mnt/c
- silent scope narrowing absent from issue ledger

For process tests use sacrificial owned processes only.
