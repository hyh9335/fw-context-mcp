# M3 Verifier Findings — Adversarial Validation (FAIL)

**Verifier:** Fresh independent verifier (subagent)
**Date:** 2026-09-29
**Verdict:** **FAIL** — M3 is not safely complete against its stated scope.
**Stop-boundary impact:** Do NOT stop. Returning PASS would leave the program
on the false premise that v2 is the default production read authority.

All 6 test suites pass (167 tests). The routing primitive is sound in
isolation. But the M3 *cutover* did not actually happen in the running
MCP server, and the production routing primitive carries a correctness
defect the codebase itself forbids.

---

## F1 — [Completeness, DECISIVE] Production handlers are NOT wired to v2

`v2_authority_route` is applied to **zero of 45** production tools.

- `server.py` registers tools as
  `mcp.tool()(_with_project_selector(_wrap_tool(search.lookup_symbol)))`
  — no `v2_authority_route` anywhere (45 `mcp.tool()` calls, 0 v2 wraps).
- The actual `lookup_symbol` handler (`handlers/_lookup.py`) is 100%
  legacy SQL (`LOOKUP_EXACT_SQL` / `LOOKUP_PREFIX_SQL` over the legacy
  `symbols` table). Zero imports of `authority`, `v2`, `route_authority`.
- `_wrap_tool` (server.py:195) and `_with_project_selector` (server.py:574)
  inject error boundary + thread dispatch + project param — NOT v2 routing.
- `grep` across `src/` for `v2_authority_route|route_authority(` returns
  only self-references inside `authority.py`. No handler, no server code.

**Consequence:** The M3 default flip is **inert in production**.
`v2_read_authority_enabled()` returns True, but no production read path
consults it. The running MCP server is 100% legacy for every tool.

**Status contradictions (materially misleading):**
- `PROJECT_STATUS.md:17` "v2 is now the DEFAULT authority for participating
  tools" — the participating set is empty in production.
- `PROJECT_STATUS.md:26` "v2-authoritative (DEFAULT since M3)" table for
  lookup_symbol / find_callers / find_references — false in the live server.
- `PROJECT_STATUS.md:37` "v2_authority_route decorator wraps production
  handlers" — false; it wraps test harnesses only.
- `CURRENT_STAGE.md` M3 scope #2 "Wire production MCP handlers through the
  real request path" — UNMET.
- D006 minor issue #2 ("E2E tests are within the test harness, not the
  actual MCP server path") was claimed resolved ("M3 added real
  request-path tests — DONE") — NOT resolved. The M3 tests still wrap the
  handler *inside the test file* (`v2_lookup_symbol = v2_authority_route(
  "lookup_symbol", prod_lookup_symbol, ...)` at test line 65), not in
  `server.py`.

**Verified working:** the flag, the primitive, the bridge, the tests.

---

## F2 — [Correctness, latent] `v2_authority_route` uses `allow_tree_mismatch=True`

`authority.py:293`:
```python
pinned = resolve_or_reuse_view(project_root, session_key,
    allow_tree_mismatch=True)   # shadow-safe for M3 validation
```

The codebase's own docstrings forbid this for production authority:
- `queryview.py:270-272`: "**WARNING**: this is a M1 shadow-validation
  convenience that is NOT safe for M3 — it can mask semantic drift. It
  MUST remain False for production authority."
- `queryview.py:283`: "Set True ONLY for shadow validation, never for
  v2 authority."

Verified against the real sensor_hub corpus (which HAS a real tree
mismatch: stored `1c66…` ≠ current `598e…`):
- `allow_tree_mismatch=False` → `NoActiveWorld` (fail-closed, correct).
- `allow_tree_mismatch=True` (what the wrapper uses) → resolves to
  `pin_reason="session_default_tree_mismatch"` via **highest world_id**
  (the exact RISK A / D004 pattern), with runtime warning:
  "SHADOW-ONLY fallback, NOT safe for v2 authority."

This violates PROJECT_GOAL P2 (historical/version isolation) and P4
(fail-closed). Latent today (primitive not wired — see F1), but would
serve semantically-drifted data the moment it is wired.

---

## F3 — [Correctness, design gap] Equivalence gate does NOT cover v2-SUCCESS path

`route_authority` calls `_can_safely_fallback` (the RISK B equivalence
gate) **only** in the `except` branches (v2 failure /
V2UnsupportedOperation / QueryViewResolutionError). When v2 SUCCEEDS
(the common path), the result is returned with **no equivalence check**.

Combined with F2: if the wrapper resolved a tree-mismatched/wrong world,
v2 returns that wrong world's data silently. RISK B (D005) prevents
wrong-WORLD *legacy fallback*; it does NOT prevent wrong-WORLD *v2
success*. The guarantee is asymmetric.

---

## Confirmed-correct (no issue)

1. **M3 default flip** — `v2_read_authority_enabled()` returns True with no
   env, False with `=0`, True with `=1`. ✅ Minor: garbage values ("yes",
   "false") enable v2 (only exact `"0"` opts out) — fail-safe direction,
   acceptable.
2. **Opt-out / rollback** — `=0` → `legacy_sole`; `route_authority` calls
   legacy directly, v2 not touched. ✅ (at primitive level)
3. **Equivalence bridge** — real legacy config_hash `ef814b7e…` → v2 build_id
   `241f5861…`; unknown → None; empty → None. Verified against real
   `reuse_admissions` + `build_configs`. ✅
4. **Session stability (section 29)** — W1 stays pinned across tools and
   after W2 creation; `force_refresh` rotates; new session gets own pin. ✅
5. **V2UnsupportedOperation prefix fallback** — `exact=False` raises
   V2UnsupportedOperation; route_authority catches; equivalence gate
   applies. ✅
6. **All 6 test suites pass (167 tests).** ✅

---

## Why FAIL (not PASS_WITH_MINOR_ISSUES)

The M3 stop boundary is `V2_DEFAULT_READ_AUTHORITY` — "make v2 the DEFAULT
semantic read authority for normal fw-context use." In the running MCP
server, v2 is the authority for **zero** tools. The flag says v2 is
default; production reality says legacy is sole. Returning PASS would stop
the program on a premise the deployed code does not satisfy, and the next
program (write authority / legacy retirement) would proceed believing v2
handles production reads when it does not.

F1 is a completeness failure against M3's own scope. F2/F3 are
correctness defects in the M3 primitive that would silently serve
semantically-drifted data once wired. These are not "small non-structural
issues" — they are the cutover itself not having happened.

## Remediation required before M3 can PASS

1. **Wire production handlers** — apply `v2_authority_route` in
   `server.py` to at least lookup_symbol / find_callers / find_references
   (the 3 "participating" tools), so the flag flip has production effect.
2. **Set `allow_tree_mismatch=False`** in `v2_authority_route`
   (authority.py:293). On tree mismatch, fail closed to legacy (the
   wrapper's existing `except QueryViewResolutionError` path already does
   this safely).
3. **Reconcile STATUS/STAGE docs** with reality: until (1) lands, the
   "v2-authoritative (DEFAULT since M3)" claim is false.
4. (Recommended) Extend the equivalence guarantee to the v2-success path,
   or document explicitly why v2-success needs no equivalence gate when
   the view is resolved fail-closed (post-F2 fix).
