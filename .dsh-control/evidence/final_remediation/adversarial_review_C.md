# Adversarial Review — Issue C (Multi-Build World Correctness)

**Reviewer:** Fresh adversarial reviewer (no prior context)
**Scope:** M7 FINAL CLOSURE REMEDIATION — Issue C: Multi-Build World correctness
**Date:** 2025-01
**Files reviewed:** 6 files + cross-references to production indexing path

---

## VERDICT: APPROVED (with non-blocking defects noted)

The core multi-build coexistence mechanism is **correct and safe for normal production**.
Query isolation is enforced by BuildSet-membership projection (not by world_id), and the
production direct-materialization path uses distinct world_ids. Three residual defects were
found in non-default / fallback paths — none cause cross-build query leakage, but they
should be tracked.

**Multi-build coexistence is SAFE** in the production path (`materialize_unit_into_m6`).

---

## Per-Question Findings

### Q1. Is `world_id` parameter in `materialize_unit_into_m6` actually used (not ignored for hardcoded 1)?

**FINDING: YES — the parameter is used directly.**

`m6_direct_materialization.py:87`:
```python
store.register_world(world_id, {build_name})
```

The `world_id` parameter (declared at line 54) flows directly into `register_world`. It is
NOT replaced with a literal `1`. The stats dict (line 79) also echoes it back:
`"world_id": world_id`.

The caller in production (`_unit_processor.py:582-590`) passes `_m6_world_id` which is
derived from `world_ctx.world_id` (v2-derived) or a deterministic hash fallback
(`_unit_processor.py:574-577`), never a literal `1` in the normal path.

✅ Verified.

---

### Q2. Does `register_world(world_id, {build_name})` correctly associate builds with worlds?

**FINDING: YES.**

`m6_store.py:267-280`:
```python
def register_world(self, world_id: int, build_names: Iterable[str]) -> None:
    with self._write_lock:
        for bname in build_names:
            self._conn.execute(
                "INSERT OR IGNORE INTO m6_world_builds(world_id, build_name) VALUES(?, ?)",
                (world_id, bname)
            )
        self._conn.commit()
```

This inserts `(world_id, build_name)` pairs into `m6_world_builds` (schema at
`m6_schema.py:88-94`, PK=`(world_id, build_name)`). So world 1001 → {buildA} and
world 1002 → {buildB} are distinct rows. `INSERT OR IGNORE` makes it idempotent.

This table is the GC-reachability foundation (`active_builds` at line 301, `build_refcounts`
at line 319, `gc_unreachable_facts` at line 340). Worlds are correctly tracked per-build.

✅ Verified.

---

### Q3. Does `resolve_build_name` support multi-build selection via variant/image kwargs?

**FINDING: YES.**

`m6_query.py:65-94`:
```python
def resolve_build_name(store, project_root, variant=None, image=None) -> str:
    if variant and image:
        return f"{variant}/{image}"
    if variant:
        return variant
    if image:
        return image
    # No explicit selector — try the first registered build
    try:
        builds = store._build_to_buildsets
        if builds:
            return next(iter(builds.keys()))
    except Exception:
        pass
    return "default"
```

This constructs distinct build names for distinct (variant, image) tuples, enabling
multi-build selection. When no selector is given, it falls back to the first registered
build (insertion order via dict).

✅ Verified.

---

### Q4. Does `authority.py` actually pass variant/image from the MCP tool call to `resolve_build_name`?

**FINDING: YES.**

`authority.py:450-454` (inside `m6_authority_route` wrapper):
```python
build_name = resolve_build_name(
    store, project_root,
    variant=kwargs.pop("variant", None),
    image=kwargs.pop("image", None),
)
```

`kwargs.pop("variant", None)` extracts the `variant` kwarg from the MCP tool call (if
present) and removes it so it isn't passed to the M6 adapter. Same for `image`. These are
then forwarded to `resolve_build_name`.

✅ Verified.

---

### Q5. Does `add_fact` correctly merge builds into BuildSets (so same-canon facts are shared)?

**FINDING: YES.**

`m6_store.py:146-178` (`add_fact`) calls `merge_buildset` (line 156), which
(`m6_store.py:113-142`):
1. Looks up the fact's current BuildSet via `m6_shared_facts.buildset_id`
2. Unions the current builds with `new_builds`: `merged = current_builds | new_builds`
3. Re-interns the merged BuildSet: `return self.intern_buildset(merged)`

Then `add_fact` updates the fact's `buildset_id` to the merged one (line 161-164).

So when `shared_func` (same canon) is added by buildA then buildB:
- 1st add (buildA): BuildSet `{buildA}` → new fact row
- 2nd add (buildB): `merge_buildset` unions → BuildSet `{buildA, buildB}`, fact's
  `buildset_id` updated to the merged BuildSet's id.

Result: ONE fact row with a BuildSet containing both builds. ✅ Verified.

`intern_buildset` (`m6_store.py:84-111`) also updates `_build_to_buildsets` (line 108-109)
so that the merged BuildSet id is registered under BOTH build names — this is what makes
`project_build` find the shared fact from either build.

✅ Verified.

---

### Q6. Does `project_build(build_name)` correctly filter to only facts visible in that build?

**FINDING: YES — this is the core isolation mechanism and it is correct.**

`m6_store.py:196-233`:
```python
def project_build(self, build_name: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    candidate_bsids = self._build_to_buildsets.get(build_name, set())
    if not candidate_bsids:
        return result
    placeholders = ",".join("?" * len(candidate_bsids))
    rows = self._conn.execute(
        f"""SELECT sf.fact_id, sf.fact_canon, sf.fact_kind, sf.payload_json,
                  sf.buildset_id
           FROM m6_shared_facts sf
           WHERE sf.buildset_id IN ({placeholders})""",
        tuple(candidate_bsids)
    )
    ...
```

**Mechanism:** `_build_to_buildsets[build_name]` holds the set of BuildSet-ids that contain
`build_name` (built in `_load_caches` line 77-80 and `intern_buildset` line 108-109). A fact
is returned iff its `buildset_id` is in that candidate set, i.e. iff `build_name ∈
fact.BuildSet`.

**Isolation proof:**
- `functionA` (BuildSet `{buildA}`): bsid=Y is in `_build_to_buildsets["buildA"]` but NOT in
  `_build_to_buildsets["buildB"]`. So `project_build("buildB")` never returns it.
- `shared_func` (BuildSet `{buildA, buildB}`): bsid=X is in BOTH indices, so both
  `project_build("buildA")` and `project_build("buildB")` return it.

This is BuildSet-membership filtering, independent of `world_id`. Query isolation holds
regardless of how world_ids are assigned.

✅ Verified — the isolation is structurally correct.

---

### Q7. Is there any code path that hardcodes world_id=1?

**FINDING: YES — two residual hardcodings, both in non-default / fallback paths.**

**(a) MEDIUM — `m6_production_indexing.py:83`:**
```python
def index_legacy_db_into_m6(legacy_db_path, project_root, build_name="default"):
    store = open_m6_store(project_root)
    store.register_world(1, {build_name})   # <-- HARDCODED world_id=1
```

This is the **legacy-DB conversion path**. Per `runner.py:1137-1146` and
`runner.py:1155-1176`, this path runs ONLY when `FW_CONTEXT_M6_LEGACY_CONVERSION=1`
(default `"0"`). The code comments (`runner.py:1137-1140`, `m6_direct_materialization.py:18`)
explicitly state it is "an EXPLICIT COMPAT/BENCHMARK MODE only — NOT in normal production."

**Impact:** If an operator enables legacy conversion mode AND indexes multiple builds, all
builds would be registered under world_id=1. This does NOT cause query leakage (isolation is
BuildSet-based), but it corrupts GC-reachability accounting (all builds appear owned by one
World). It also violates the "distinct world_ids" claim for this non-default path.

**(b) LOW — `runner.py:1151-1152` (normal production finalization):**
```python
_m6_world_id = (getattr(world_ctx, "world_id", None)
                if world_ctx else None) or 1
```

This `or 1` fallback triggers only if `world_ctx.world_id` is falsy (None/0) at
finalization time. In normal production, `_unit_processor.py:569-578` derives a
deterministic world_id (hash of build_name) and assigns it to `world_ctx.world_id` BEFORE
finalization runs. So this fallback would only trigger if the direct-materialization hook
threw before the assignment (caught by `except Exception` at `_unit_processor.py:591`),
which is an error path. If `world_ctx` itself is None, `_m6_store` (line 1153) is also None,
so the `elif _m6_store is not None` branch (line 1177) doesn't execute — the `or 1` is then
unused.

**Impact:** Very unlikely to trigger in practice; if it does, it's an error-condition
fallback, not normal operation.

⚠️ Both are non-blocking for the Issue C claim (which targets the production
direct-materialization path), but they are real hardcodings that should be flagged.

---

### Q8. Does the GREEN test actually verify query isolation (build A's facts don't leak to build B)?

**FINDING: YES.**

`test_multi_build_world.py:105-154`:
- Indexes buildA (world_id=1001) and buildB (world_id=1002) into the SAME store.
- Calls `store.project_build("buildA")` and `store.project_build("buildB")`.
- Collects function names per build.
- Asserts (lines 129-134):
  ```python
  assert "functionA" in funcs_a
  assert "functionA" not in funcs_b      # no leakage A→B
  assert "functionB" in funcs_b
  assert "functionB" not in funcs_a      # no leakage B→A
  ```
- Also asserts TypeA/TypeB isolation (lines 153-154).

This directly verifies that build-A-only facts do not appear in build B's projection, and
vice versa. The test uses distinct world_ids (1001/1002), confirming the world_id parameter
is exercised (not hardcoded 1 in the test).

✅ Verified.

---

### Q9. Does the GREEN test verify shared facts are ONE fact in both BuildSets?

**FINDING: YES (via BuildSet membership; fact-row count is implied).**

`test_multi_build_world.py:136-186`:
- Asserts `shared_func` is in BOTH `funcs_a` and `funcs_b` (lines 137-138).
- Retrieves `shared_canon` from build A's projection (lines 159-165).
- Looks up `fact_id = store._fact_cache.get(shared_canon)` (line 171) — `_fact_cache` is a
  `dict[str, int]` (`m6_store.py:57`), so one canon maps to exactly ONE fact_id. This
  implicitly confirms there is a single fact row.
- Queries the fact's BuildSet (lines 173-181) and asserts (lines 184-185):
  ```python
  assert "buildA" in builds_in_bs
  assert "buildB" in builds_in_bs
  ```

The dedup is verified through the BuildSet-merge mechanism: the shared fact's BuildSet
contains both builds, proving `add_fact` merged rather than duplicated.

**Minor gap:** The test does not explicitly assert `COUNT(*) FROM m6_shared_facts WHERE
fact_canon = <shared_canon> == 1`. It relies on the `_fact_cache` dict invariant (one canon →
one fid). This is adequate because the dict is the authoritative in-memory index, but an
explicit row-count assertion would be stronger.

✅ Verified (adequately).

---

### Q10. What happens if two builds have the same canon but different payloads (e.g., ifdef-filtered bodies)? Is this handled correctly?

**FINDING: First-payload-wins for shared facts. Does NOT affect query isolation, but is a
semantic imperfection for non-canon payload fields.**

`m6_store.py:158-166` (`add_fact`, existing-fact branch):
```python
existing_fid = self._fact_cache.get(fact_canon)
if existing_fid is not None:
    # Update buildset_id (may have changed due to merge)
    self._conn.execute(
        "UPDATE m6_shared_facts SET buildset_id = ? WHERE fact_id = ?",
        (bsid, existing_fid)
    )
    self._conn.commit()
    return existing_fid   # <-- RETURNS EXISTING; NEW PAYLOAD IS DROPPED
```

When a fact with the same canon already exists, `add_fact` updates ONLY the `buildset_id`
(merging builds) and **silently discards the new `payload`**. The first build's payload wins.

**Impact analysis:**
- **Canon-included fields** (USR, signature, kind): these ARE in the canon, so same-canon
  facts have the same values by definition. No conflict.
- **Non-canon payload fields** (file, line, end_line, docstring, qualified_name): these CAN
  differ across builds for the same USR (e.g., a header-included function defined in
  different TUs). The first build's values win; querying the second build returns the first
  build's file/line.

**For ifdef-filtered bodies specifically:** `materialize_unit_into_m6` does NOT index body
facts at all — it indexes Function/Type/Variable/Symbol/Call/Reference/Inheritance metadata
only. Body facts are handled by `add_body_fact` (`m6_store.py:429-454`), which keys on
`symbol_usr + body_hash` — so DIFFERENT bodies (different ifdef-filtered text) produce
DIFFERENT body_hash → distinct body-fact rows. However, `add_body_fact` does NOT merge
buildsets for body facts (line 442-447 returns the existing body_id without merging), so
body-fact dedup is incomplete — a body fact is tied to one build's BuildSet only.

**Severity:** LOW. This does not cause cross-build leakage (projection is BuildSet-based).
It means the payload of a shared metadata fact reflects the first inserter, and body facts
are not properly shared. This is a known limitation of the current M6 model, not a regression
introduced by Issue C remediation.

⚠️ Noted as a LOW-severity defect, not a blocker for Issue C.

---

## Holes / Defects Found

| # | Severity | Location | Description |
|---|----------|----------|-------------|
| 1 | **MEDIUM** | `m6_production_indexing.py:83` | `register_world(1, {build_name})` hardcodes world_id=1 in the legacy-DB conversion path. Non-default (gated behind `FW_CONTEXT_M6_LEGACY_CONVERSION=0`), but if enabled with multiple builds, all builds collide on world_id=1. Does NOT cause query leakage (isolation is BuildSet-based) but corrupts GC-reachability accounting. Violates the "distinct world_ids" claim for this non-default path. |
| 2 | **LOW** | `runner.py:1152` | `_m6_world_id = ... or 1` fallback in production finalization. Only triggers if `world_ctx.world_id` is unset at finalization (error path; the unit processor's hash fallback at `_unit_processor.py:574-577` normally prevents this). Unlikely but latent. |
| 3 | **LOW** | `m6_store.py:158-166` | `add_fact` drops subsequent payloads for same-canon facts (first-payload-wins). Non-canon fields (file, line, docstring) reflect the first inserter, not per-build. Does not affect isolation. |
| 4 | **LOW** | `m6_store.py:442-447` | `add_body_fact` does not merge BuildSets for body facts (returns existing body_id without union). Body-fact sharing across builds is incomplete. Not exercised by `materialize_unit_into_m6` (which doesn't index bodies). |
| 5 | **INFO** | `test_multi_build_world.py` | Test only exercises the `materialize_unit_into_m6` (direct) path. The legacy conversion path (`index_legacy_db_into_m6`) — which has the hardcoded world_id=1 — is NOT covered by this test. |

---

## Cross-Build Leakage Safety Assessment

**The code PREVENTS cross-build query leakage.** The isolation mechanism is:

1. **BuildSet-membership projection** (`project_build`, `m6_store.py:196-233`): a fact is
   visible in build B iff `B ∈ fact.BuildSet`. The `_build_to_buildsets` index
   (`m6_store.py:59, 77-80, 108-109`) maps each build name to the set of BuildSet-ids that
   contain it. A fact whose BuildSet is `{buildA}` is invisible to `project_build("buildB")`
   because its buildset_id is not in `_build_to_buildsets["buildB"]`.

2. **BuildSet merging** (`merge_buildset`, `m6_store.py:113-142`): when the same canon is
   added by two builds, the BuildSet is unioned (not duplicated). The shared fact becomes
   visible to BOTH builds via the merged BuildSet, while build-specific facts remain
   isolated.

3. **Canon identity** (`prototype_a.py`): canon functions are USR-based (e.g.,
   `canon_function` = `Function({usr},{signature},{attrs},{storage})`). Two functions with
   the same USR + signature are the same fact; functions with different USRs are distinct
   facts. This correctly distinguishes `functionA` (USR `c:@F@functionA`) from `functionB`
   (USR `c:@F@functionB`).

**This isolation is INDEPENDENT of world_id.** The world_id is used only for GC
reachability (`active_builds`, `gc_unreachable_facts`), not for query filtering. Therefore,
even the hardcoded world_id=1 in the legacy path does NOT cause query leakage — it only
affects GC accounting.

---

## Conclusion

**APPROVED.** The Issue C remediation is correct for the production direct-materialization
path:

- ✅ `materialize_unit_into_m6` uses the `world_id` parameter (not hardcoded 1).
- ✅ `register_world` correctly maps worlds → builds.
- ✅ `resolve_build_name` supports variant/image multi-build selection.
- ✅ `authority.py` passes variant/image through to `resolve_build_name`.
- ✅ `add_fact`/`merge_buildset` correctly merge BuildSets for shared facts.
- ✅ `project_build` correctly filters by BuildSet membership (isolation enforced).
- ✅ The GREEN test verifies query isolation and shared-fact dedup.
- ✅ Multi-build coexistence is SAFE in normal production.

**Non-blocking defects** (MEDIUM #1, LOW #2-4) exist in non-default / fallback paths and do
not cause cross-build leakage. They should be tracked for future cleanup but do not block
Issue C closure.

**Recommendation:** Track Defect #1 (`m6_production_indexing.py:83` hardcoded world_id=1)
for remediation if the legacy conversion path is ever promoted from compat/benchmark to
production. For now, the gating (`FW_CONTEXT_M6_LEGACY_CONVERSION=0` default) and explicit
documentation ("NOT the normal production path") make this acceptable.
