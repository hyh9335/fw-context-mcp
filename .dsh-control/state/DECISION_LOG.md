# DECISION_LOG — fw-context M3B autonomous controller

Append-only. Every controller decision, drift correction, and out-of-scope
production change goes here.

## 2026-09-30 — Bootstrap

- Detected DSH capabilities: MODE A (continuable subagents + goal tools + pwsh/file-edit).
- Resolved canonical project root: `D:/github/fw-context-mcp`, HEAD `ac76feb`, branch `main`.
- Installed project-local control tree under `.dsh-control/` (contracts, prompts,
  gates, state, evidence). No DSH_HOME / profile / preset / Host-plugin changes.
- Built code-derived scanner `scan_authority_state.py` (AST over server.py +
  v2_shadow_adapters.py + authority.py). Baseline: CORE=29, native=5,
  legacy_exec=26, unsafe_fallback=1, bugs=0.
- Built hard gate `run_stage_gate.py` (12 clauses, no warning-pass).
- Ran self-tests A-F: 6/6 PASS. Gate proven unable to fool itself.
- **Drift corrected:** prior root `PROJECT_STATUS.md` claimed native=4/legacy=25;
  scanner derives native=5/legacy=26 (find_all_callers_recursive is PARTIAL, not
  pure-legacy). Scanner is authoritative.
- Created persisted DSH Goal for M3B (high round budget).
- Entered autonomous loop. First highest-leverage clause: C7 (shared unsafe
  fallback in `v2_authority_route` wrapper).

## 2026-09-30 — WP0 (clause C7): eliminate shared unsafe legacy fallback

**Owner:** Lead (WP0). **Target:** `src/fw_context_mcp/mcp/shared/authority.py`,
`v2_authority_route` → nested `wrapper`, `except QueryViewResolutionError:` handler.

**Problem (scanner baseline):** `CORE_UNSAFE_FALLBACK == 1`. The wrapper's
`except QueryViewResolutionError:` handler did `return legacy_fn(*args, **kwargs)`
WITHOUT gating it through `_can_safely_fallback(...)`. When v2 World resolution
failed (no v2 store / tree mismatch), the wrapper silently executed legacy
semantics — an unguarded, unsafe fallback inherited by all 29 CORE tools.

**Fix (option (a) — explicit guard, mirrors `route_authority` lines 160-176):**
Replaced the unguarded `return legacy_fn(...)` with a guarded path:
`if _can_safely_fallback(None, None, project_root): return legacy_fn(...)`
else `raise` (re-raise the `QueryViewResolutionError`). At this site `pinned`
was never assigned (resolution raised before assignment) and
`legacy_config_hash` is not yet computed (step 2 runs after this block), so
`_can_safely_fallback(None, None, project_root)` is always False — the
fallback branch never executes and the v2 resolution failure propagates
visibly. This is the M3B-correct behavior: v2 is the READ AUTHORITY; a
missing/stale v2 store is a visible failure, not silent legacy execution.

**Why explicit guard (not bare re-raise):** Structural consistency with
`route_authority`'s canonical RISK-B pattern; scanner-recognized as GUARDED;
forward-correct if a future refactor resolves `pinned` earlier. The guard
is NOT weakened — `_can_safely_fallback` is unchanged.

**Files changed:**
- `src/fw_context_mcp/mcp/shared/authority.py` — `wrapper` except handler
  (was lines 307-317; now 307-339). No other file touched.

**Scanner counters (before → after):**
- `CORE_UNSAFE_FALLBACK`: 1 → **0** ✅ (C7 GREEN)
- `unsafe_fallback_sites`: 1 site (wrapper@307/317) → **[]** (empty)
- Other counters unchanged by this WP (C5/C6 are separate work packages):
  CORE=29, V2_ROUTED=29, V2_NATIVE_READ=5, LEGACY_EXEC=26, BUGS=0.

**Tests (focused suite per WP0):** all green.
`python -m pytest tests/test_fallback_hygiene_slice_a.py tests/test_slice3_guard.py
tests/test_slice3_queryview.py tests/test_slice2_worlds.py
tests/test_store_v2_identity.py tests/test_store_v2_inertness.py
tests/test_legacy_migration.py tests/test_server.py -x -q --tb=short`
→ **108 passed, 2 skipped, 0 failed** (20s).
Skips are environmental: `test_legacy_migration.py` "gcc not available"
(indexing prerequisite), unrelated to this change. No test files modified.

**Test-update rationale:** NONE required. No test asserted the old "fall back
to legacy when v2 store missing" behavior for this wrapper path; the
equivalence-gate guards in `test_fallback_hygiene_slice_a.py` /
`test_slice3_guard.py` were not weakened and still pass.

**Risks / open issues:**
- This WP closes C7 only. The M3B numeric gate remains FAIL on C5
  (`CORE_V2_NATIVE_READ == 5`, needs 29) and C6
  (`CORE_LEGACY_SEMANTIC_EXECUTION == 26`, needs 0) — those are subsequent
  work packages (adapter v2-native read conversions), NOT WP0.
- Behavioral change for callers: a tool whose v2 store is missing/stale now
  raises `QueryViewResolutionError` instead of silently returning legacy
  data. This is the intended M3B semantic (v2 = read authority). Any
  downstream code that depended on silent legacy fallback at THIS site would
  now see the error — none was found in the focused tests.

## 2026-09-30 — Controller: git state restoration (out-of-scope, justified)

**Problem:** During WP0 execution, HEAD was moved from `ac76feb` (M3 checkpoint,
7 src/ files tracked) to `aefebcc` (minimal 2-commit repo, src/ untracked) via
4 unlogged reflog transitions. At `aefebcc`, the entire `src/` tree (including
all 3 M3B-critical files: server.py, authority.py, v2_shadow_adapters.py) was
untracked. This would have blocked the "durable checkpoint" clause (C12) and
made Verifier/Guardian artifacts name a HEAD that doesn't include the production
code under review.

**Decision:** `git reset --mixed ac76feb` — restore the index to the M3
checkpoint (src/ tracked) while preserving the working tree (Lead's authority.py
change intact). This is the minimal operation to restore the pre-existing
tracking state without discarding any working-tree changes.

**Justification (out-of-scope production change):** The M3B contract §4
requires "durable checkpoint (git commit) exists for the accepted HEAD." A
HEAD that doesn't track the production code under modification cannot serve as
a durable checkpoint. The reset restores the state that existed at bootstrap
(HEAD=ac76feb, src/ tracked) — it does not introduce new tracking, weaken any
gate, or modify production code. It is a git-state restoration, not a code
change. Recorded per AGENTS.local.md: "Production code changes outside the
target Goal require normal engineering justification, recorded in
DECISION_LOG.md."

**Verification after reset:**
- HEAD = `ac76feb` (M3 checkpoint). src/ has 7 tracked files (the M3B-critical
  set: server.py, authority.py, v2_shadow_adapters.py, queryview.py, shadow.py,
  symbols.py, certify.py).
- Scanner re-run: CORE=29, V2_ROUTED=29, V2_NATIVE_READ=5, LEGACY_EXEC=26,
  **UNSAFE_FALLBACK=0**, BUGS=0. C7 still GREEN.
- Focused tests re-run: 108 passed, 2 skipped, 0 failed. No regressions.
- WP0 authority.py change is a tracked modification (`M` in git status).

## Next: WP1 — convert 24 V2_ROUTED_LEGACY_EXEC adapters to v2-native reads

