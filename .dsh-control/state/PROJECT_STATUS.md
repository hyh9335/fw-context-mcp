# PROJECT_STATUS — fw-context M3B (cache, NOT truth)

> **This file is a CACHE.** The scanner (`.dsh-control/gates/scan_authority_state.py`)
> and the hard gate (`.dsh-control/gates/run_stage_gate.py`) are TRUTH. Any
> divergence is resolved in favor of the scanner. Drift is logged in
> `DECISION_LOG.md`.

**Last scanner run:** see `.dsh-control/evidence/authority_state.json` (`generated_at_utc`).
**HEAD at last scan:** see `authority_state.json` (`git.head`).

## Code-derived counters (mirror of latest scan)

| Counter | Value | M3B target |
|---|---|---|
| CORE_NORMAL_USE_SEMANTIC_TOOLS | 29 | > 0 |
| CORE_V2_ROUTED | 29 | == CORE |
| CORE_V2_NATIVE_READ | 5 | == CORE (29) |
| CORE_LEGACY_SEMANTIC_EXECUTION | 26 | == 0 |
| CORE_UNSAFE_FALLBACK | 1 | == 0 |
| UNRESOLVED_NORMAL_USE_V2_BUGS | 0 | == 0 |

## M3B hard gate: FAIL

Failing: C5, C6, C7 (and C9/C10/C11 not yet produced).

## Note on divergence from prior PROJECT_STATUS

The earlier (pre-controller) `PROJECT_STATUS.md` at repo root claimed
`native=4, legacy_exec=25`. The code-derived scanner reports `native=5,
legacy_exec=26`: it finds `find_all_callers_recursive` ALSO has a v2 read path
(PARTIAL) in addition to its deferral branch. The scanner is authoritative;
this cache has been corrected. This is exactly why counters are derived from
code, not ledgers.
