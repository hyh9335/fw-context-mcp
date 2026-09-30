# M4 Archive — V2 Write Finalization Authority

> FROZEN snapshot of the M4 milestone, accepted at durable checkpoint
> `10d1f36e7f2aabb897c5c136fe5986e3f0e2c7e9` on branch `m3b-native-v2`.

## Milestone

`M4_V2_WRITE_FINALIZATION_AUTHORITY_COMPLETE`

## Accepted state

- **Durable checkpoint**: `10d1f36`
- **Gate**: `run_m4_stage_gate.py` → EXIT 0 (27/27 clauses OK)
- **Fresh verifier**: PASS at `10d1f36` (`m4_verifier_10d1f36.json`)
- **Fresh guardian G-FINAL**: ACCEPT_COMPLETION at `10d1f36` (`m4_guardian_10d1f36.json`)
- **M3B regression**: PASS (CORE=29, V2_ROUTED=29, V2_NATIVE_READ=29, LEGACY_EXEC=0, UNSAFE_FALLBACK=0)

## Semantic achievement

v2 World finalization is the AUTHORITATIVE success criterion for normal
indexing. The 4 success-matrix cases are enforced in code:

- **CASE A** (v2 ok + legacy ok) → SUCCESS
- **CASE B** (v2 ok + legacy fail) → SUCCESS + LEGACY_COMPATIBILITY_DEGRADED
- **CASE C** (v2 fail + legacy ok) → FAILURE (IndexIntegrityError)
- **CASE D** (v2 incomplete + legacy ok) → FAILURE (IndexIntegrityError)

Production default write mode: `V2_PRIMARY_LEGACY_MIRROR` (flag default ON).
Rollback: `FW_CONTEXT_V2_WRITE_AUTHORITY=0` restores legacy-primary.

All 4 M4 bugs resolved (M4-BUG-001/002/003/004).

## Archived files

| File | Purpose |
|---|---|
| `IMMUTABLE_M4_GOAL_CONTRACT.md` | Frozen M4 contract |
| `M4_GATE.json` | M4 gate spec (13 numeric + 7 fault + acceptance) |
| `m4_verifier_10d1f36.json` | Fresh verifier PASS artifact |
| `m4_guardian_10d1f36.json` | Fresh guardian ACCEPT_COMPLETION artifact |
| `scan_write_authority.py` | Code-derived M4 scanner |
| `run_m4_stage_gate.py` | M4 executable hard gate |
| `m4_fault_tests.py` | 8 fault-injection tests |
| `m4_self_test.py` | 7 anti-gaming self-tests |
| `WRITE_AUTHORITY_LEDGER.md` | M4 write-authority ledger (cache) |
| `M4_6_LEGACY_GATE_REASSESSMENT.md` | All 18 legacy postprocess steps LEGACY_COMPAT |
| `M4_WRITE_PATH_ARCHAEOLOGY.md` | M4 write-path archaeology |
| `v2_write_bugs.json` | M4 bug register (4 resolved) |

## Note

This archive is FROZEN historical evidence. The M4 gate continues to run
as the `M4_REGRESSION` clause of the M5 gate. Do not modify archived files.
