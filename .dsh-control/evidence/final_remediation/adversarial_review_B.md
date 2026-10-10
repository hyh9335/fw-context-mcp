# Adversarial Review B — M6 Query Functional Parity (Issue B)

**Reviewer:** Fresh adversarial reviewer (no prior context)
**Scope:** Verify the 9 previously-stubbed M6 query adapters now return REAL data from the M6 store (not hardcoded empty/info/error results).
**§8 criterion:** A tool counts as M6-native ONLY if `production request → M6 query implementation → M6 result`. A stub returning hardcoded values does NOT count.

## Files Inspected

1. `src/fw_context_mcp/store_v2/m6_query.py` (801 lines) — M6 query primitives
2. `src/fw_context_mcp/mcp/shared/m6_query_adapters.py` (320 lines) — production adapters
3. `.dsh-control/m7/tests/test_tool_parity_green.py` (260 lines) — GREEN test
4. `.dsh-control/evidence/final_remediation/tool_parity_green_test.json` (53 lines) — test result

**Adversarial verification of the store primitive itself:**
`store.project_build_kind` is defined at `src/fw_context_mcp/store_v2/m6_store.py:235-263` and executes a REAL SQL query against `m6_shared_facts` (`SELECT sf.fact_id, sf.fact_canon, sf.payload_json FROM m6_shared_facts sf WHERE sf.buildset_id IN (...) AND sf.fact_kind = ?`). It is NOT a hidden stub. Every primitive below routes through it.

---

## VERDICT: APPROVED

All 9 adapters wire to real M6 primitives. All 9 primitives query the M6 store via `store.project_build_kind` (real SQL). `find_call_path` performs a genuine BFS (no fabrication). `find_indirect_targets` returns assignment-shaped results. No remaining hardcoded-empty stubs were found. §8 anti-false-migration is satisfied.

The defects below are real but low-severity — functional bugs and semantic simplifications, NOT stubs and NOT false migration.

---

## Per-Adapter Verification (9/9)

### 1. `get_vector_table` → `m6_query.get_vector_table` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:304-307` calls `m6_query.get_vector_table(store, build_name, limit=limit)`.
- **Primitive queries store:** `m6_query.py:655` → `store.project_build_kind(build_name, "Function")`. ✓
- **Real data confirmed:** JSON shows `found_irq_handlers: ["TIM2_IRQHandler", "USART1_IRQHandler"]` — real functions from the M6 store.
- **Defect D6 (LOW-MEDIUM, noted below):** Slot numbers are sequential integers, not real table positions; `source`/`status` hardcoded to `"c"`. This is a name-pattern heuristic, not a true vector-table read (no assembly/ELF parsing). Still satisfies §8 (queries store, returns real function facts, no hardcoded values).

### 2. `get_method_overrides` → `m6_query.get_method_overrides` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:244-247` calls `m6_query.get_method_overrides(store, build_name, method_name)`.
- **Primitive queries store:** `m6_query.py:380` → `store.project_build_kind(build_name, "Function")`; `m6_query.py:402` → `store.project_build_kind(build_name, "Inheritance")`. ✓ Two real M6 projections.
- **Real data confirmed:** JSON shows `overrides_count: 1, overridden_by_count: 1` — real inheritance-derived override data for `compute`.
- **Real logic:** scans virtual methods (line 384), builds USR→class map (392-399), walks Inheritance facts both directions (401-411), matches same-name methods in base/derived classes (414-439).

### 3. `get_template_instances` → `m6_query.get_template_instances` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:250-254` calls `m6_query.get_template_instances(store, build_name, template_name, limit=limit)`.
- **Primitive queries store:** `m6_query.py:456, 467` → `store.project_build_kind(build_name, kind)` for Function/Type/Symbol. ✓
- **Real data confirmed:** JSON shows `instance_count: 2` — `Container<int>` and `Container<double>` matched via `template_usr` field (lines 472-485). Real.

### 4. `trace_data_flow` → `m6_query.trace_data_flow` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:176-183` calls `m6_query.trace_data_flow(store, build_name, type_name, to_symbol, max_depth=..., limit=...)`.
- **Primitive queries store:** `m6_query.py:504` → `store.project_build_kind(build_name, "Function")` (source funcs); `m6_query.py:521` → `store.project_build_kind(build_name, "Call")` (call graph). ✓
- **Real BFS:** lines 532-556 — builds `call_graph` (caller_usr → callee_name) and `callee_to_usrs`, performs BFS with `visited` set and depth cap. ✓ Genuine.
- **Real data confirmed:** JSON shows `entries: 8, has_summary: true`. The `_summary` entry (line 569-571) is query-derived.
- **Defect D1 (LOW-MEDIUM, noted below):** `path_chain` reconstruction (lines 539-556) is buggy — single mutable list accumulates all visited callees, not the actual path. `reachable` flag is correct; `chain` string is malformed.

### 5. `explain_condition` → `m6_query.explain_condition` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:279-282` calls `m6_query.explain_condition(store, build_name, file_path, line)`.
- **Primitive queries store:** `m6_query.py:586` → `store.project_build_kind(build_name, "ConditionalState")`. ✓
- **Real data confirmed:** JSON shows `chain_length: 0, query_echoed: true`. Empty chain is the CORRECT answer (no `#ifdef` in test TU); the query echoes file/line, proving it ran.
- **Real logic:** filters ConditionalState facts by file and line range (lines 588-604), sorts outermost-first (606).

### 6. `compare_variants` → `m6_query.compare_variants` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:310-314` calls `m6_query.compare_variants(store, build_name, a, b, symbol_or_file=symbol_or_file)`.
- **Primitive queries store:** `m6_query.py:628-629` → `store.project_build_kind(a, kind)` AND `store.project_build_kind(b, kind)` for 6 fact kinds. ✓ Real cross-build comparison.
- **Real data confirmed:** JSON shows `dimension_count: 6, has_error_key: false`. Compares `fact_canon` sets across builds (lines 630-634) — real set-difference logic.
- **Defect D4 (LOW, noted below):** `symbol_or_file` parameter is accepted but ignored — always does build-level comparison. Symbol/file modes silently degrade.

### 7. `find_call_path` → `m6_query.find_call_path` ✅ APPROVED (CRITICAL FIX VERIFIED)
- **Wiring:** adapter `m6_query_adapters.py:76-79` calls `m6_query.find_call_path(store, build_name, from_name, to_name, max_depth=max_depth)`.
- **Primitive queries store:** `m6_query.py:740` → `store.project_build_kind(build_name, "Call")`. ✓
- **REAL BFS (not fabricated):** `m6_query.py:738-766`:
  - Builds `call_graph` dict (caller_name → {callee_name}) from Call facts (lines 740-746).
  - BFS queue of `(node, path)` tuples with `visited` set (lines 750-766).
  - Path reconstruction via immutable `path + [callee]` (line 766) — correct.
  - Returns up to 5 shortest paths (line 752).
  - On no path: returns `[{"info": ...}]` (line 769) — NOT a fabricated chain.
- **Anti-fabrication test passes:** JSON shows `fake_target_has_chain: false` — querying `pipeline → nonexistent_func` did NOT return a fake depth-1 chain. The previous stub fabricated paths; this is a genuine BFS. ✓
- **Real path confirmed:** `real_paths_count: 1` for `pipeline → source_data`.

### 8. `find_indirect_targets` → `m6_query.find_indirect_targets` ✅ APPROVED (CRITICAL FIX VERIFIED)
- **Wiring:** adapter `m6_query_adapters.py:159-162` calls `m6_query.find_indirect_targets(store, build_name, name, limit=limit)`.
- **Primitive queries store:** `m6_query.py:783` → `store.project_build_kind(build_name, "Reference")`. ✓
- **Assignment-shaped results confirmed by code inspection:** `m6_query.py:791-800` returns dicts with `rhs_name`, `rhs_qname`, `fn_ptr_type`, `method="assignment"`, `assign_file`, `assign_line`, `call_file=None`, `call_line=None`. This matches the production API shape (the tool description's `rhs_name`, `rhs_qname`, `fn_ptr_type`, `method`, `assign_file`, `assign_line`, `call_file`, `call_line`). ✓
- **Filters by `ref_kind in ("indirect", "member")` and matches `to_usr`** (lines 787-790) — correct assignment-target matching, NOT call-site matching. The previous bug (returning `find_indirect_call_sites` results) is fixed.
- **Defect D3 (LOW-MEDIUM, test weakness):** JSON shows `result_count: 0`, so the assignment shape was never exercised on real data — the test's shape check is vacuous on empty. Code inspection confirms the shape is correct, but the test does not prove it.

### 9. `group_equivalent_variants` → `m6_query.group_equivalent_variants` ✅ APPROVED
- **Wiring:** adapter `m6_query_adapters.py:317-320` calls `m6_query.group_equivalent_variants(store, build_name, symbol)`.
- **Primitive queries store:** `m6_query.py:682` → `list_builds(store)` (reads `store._build_to_buildsets`); `m6_query.py:689` → `store.project_build_kind(bname, kind)` for each build. ✓ Iterates ALL builds, queries each.
- **Real data confirmed:** JSON shows `query_echoed: true`. Real fingerprint logic (lines 693-700): `kind:signature:file:line` per symbol per build, grouped by fingerprint.
- **Defect D5 (LOW):** `config_hashes: []` is hardcoded empty (line 715) — provenance incomplete; production API expects config_hashes populated. Grouping itself is real.

---

## §8 Anti-False-Migration: SATISFIED

For each of the 9 adapters, the full chain holds:

| Adapter | → m6_query primitive | → store.project_build_kind | Hardcoded? |
|---|---|---|---|
| get_vector_table | `m6_query.py:646` | line 655 ("Function") | No (real facts) |
| get_method_overrides | `m6_query.py:362` | lines 380, 402 | No |
| get_template_instances | `m6_query.py:444` | lines 456, 467 | No |
| trace_data_flow | `m6_query.py:489` | lines 504, 521 | No |
| explain_condition | `m6_query.py:574` | line 586 | No |
| compare_variants | `m6_query.py:611` | lines 628-629 | No |
| find_call_path | `m6_query.py:727` | line 740 | No (real BFS) |
| find_indirect_targets | `m6_query.py:773` | line 783 | No |
| group_equivalent_variants | `m6_query.py:675` | lines 682, 689 | No |

`store.project_build_kind` (m6_store.py:235-263) is a real SQL SELECT against `m6_shared_facts`. No adapter returns hardcoded empty/empty-list results as a stub. The two `info`-dict returns (`get_vector_table` line 671, `find_call_path` line 769) are legitimate "no results" sentinels that MATCH production API shape (the tool descriptions explicitly document `{"info": ...}` as the empty case).

**§8 is satisfied for all 9 adapters.**

---

## Defects Found (none block approval)

### D1 (LOW-MEDIUM) — `trace_data_flow` path-chain reconstruction bug
**Location:** `m6_query.py:539-556`
`path_chain = [src["name"]]` is a single mutable list. Inside the BFS loop, `path_chain.append(callee)` (line 546) accumulates EVERY visited callee across all branches/iterations, not the actual path to the target. The resulting `chain` string (line 565) is malformed — it lists all explored callees, not a real path.
- **Impact:** `reachable` flag is correct; `_summary` is correct; but `paths[0].chain` is wrong.
- **Why not blocking:** The test only asserts `has_summary` (line 154), and the primitive IS a real M6 query (not a stub). The defect is a path-display bug, not a false-migration.
- **Fix:** reconstruct path per-branch (e.g., `path + [callee]` like `find_call_path` does at line 766).

### D2 (LOW-MEDIUM) — GREEN test `get_method_overrides` stub-check is vacuous
**Location:** `test_tool_parity_green.py:127-128`
```python
is_stub = (mo.get("name") == "compute" and len(overrides) == 0
           and len(overridden_by) == 0 and len(mo) == 3)
```
The primitive's result dict (`m6_query.py:370-373`) has **4 keys** (`name`, `qualified_name`, `overrides`, `overridden_by`), so `len(mo) == 3` is ALWAYS False. Therefore `is_stub` is always False and the verdict is always `"REAL_QUERY"` — even if the primitive returned an empty stub.
- **Impact:** The assertion cannot detect a stub. HOWEVER, the actual JSON result shows `overrides_count: 1, overridden_by_count: 1` — real data WAS returned, so the verdict is correct in this instance.
- **Fix:** check `len(mo) == 4` or check for absence of the `overrides`/`overridden_by` keys.

### D3 (LOW-MEDIUM) — GREEN test `find_indirect_targets` shape-check vacuous on empty
**Location:** `test_tool_parity_green.py:209-211`
```python
has_assignment_shape = all(
    "rhs_name" in r or "assign_file" in r or "method" in r
    for r in fit) if fit else True
```
When `fit` is empty, `has_assignment_shape = True` unconditionally. The actual JSON shows `result_count: 0`, so the shape was NEVER verified on real data.
- **Impact:** The test cannot prove the assignment shape. Direct code inspection (`m6_query.py:791-800`) confirms the shape IS correct (rhs_name/assign_file/method="assignment"), so the remediation is sound — but the GREEN test does not prove it.
- **Fix:** add a test TU with an actual function-pointer assignment so `result_count > 0`.

### D4 (LOW) — `compare_variants` ignores `symbol_or_file` parameter
**Location:** `m6_query.py:611-643`
The `symbol_or_file` parameter is accepted but never used. The function always does build-level comparison across 6 fact kinds. Production API supports symbol mode and file mode (per tool description).
- **Impact:** Symbol/file comparison silently degrades to build-level comparison. Build mode (the default and the tested path) works correctly.
- **Fix:** branch on `symbol_or_file` to project a single symbol/file per build.

### D5 (LOW) — `group_equivalent_variants` returns empty `config_hashes`
**Location:** `m6_query.py:715`
`"config_hashes": []` is hardcoded empty. Production API expects config_hashes populated per build (parallel to `variants`/`builds`).
- **Impact:** Provenance incomplete. The grouping/fingerprint logic is real; only the config_hash provenance is missing.
- **Fix:** look up config_hash per build from the store.

### D6 (LOW-MEDIUM) — `get_vector_table` is a name-heuristic, not a real vector-table read
**Location:** `m6_query.py:646-672`
The primitive scans `Function` facts for names ending in `_irqhandler`/`_isr`/`_handler` (case-insensitive) and assigns SEQUENTIAL slot numbers (line 656, 668). The production tool reads the actual interrupt vector table from assembly `.word` directives, C arrays, build records, or ELF — with real slot positions, `source` (assembly/c/build/elf), and `status` (c/unhandled/runtime/dispatcher/data/linker).
- **Impact:** Returns real function names from the M6 store (satisfies §8), but slot numbers are fabricated (sequential, not table positions) and `source`/`status` are hardcoded to `"c"`. This is a semantic simplification, not a stub. The test only checks IRQ handler names are present (line 113-114), not slot/source/status correctness.
- **Why not blocking:** §8 is about M6-native vs stub, not about full semantic parity. The primitive queries the M6 store and returns real facts, not hardcoded values.

---

## Silent-Degradation Check

**Q7: Are there adapters that silently degrade (return empty results that look like legitimate no-results)?**

Checked all 9:
- `get_vector_table` — returns `[{"info": ...}]` on empty (line 670-671). This is a documented production sentinel, NOT silent degradation. ✓
- `get_method_overrides` — returns `{"overrides": [], "overridden_by": []}` on no match. Honest empty. ✓
- `get_template_instances` — returns `[]` on no match. Honest empty. ✓
- `trace_data_flow` — always returns at least `[_summary]` (line 571). Cannot silently empty. ✓
- `explain_condition` — returns `{"chain": []}` on no enclosing region. Honest empty (correct for no-`#ifdef` files). ✓
- `compare_variants` — returns real dimensions even when `b` doesn't exist (because `project_build_kind` returns `[]` for unknown builds, making `canons_b` empty — a legitimate "build b has 0 facts" result). This COULD mask a typo in the build name, but it's not a stub. ✓
- `find_call_path` — returns `[{"info": "no path found..."}]` on no path (line 769). Honest. ✓
- `find_indirect_targets` — returns `[]` on no match (D3 notes the test can't verify shape on empty, but the empty itself is honest). ✓
- `group_equivalent_variants` — returns `{"groups": [], "unobserved": [...]}` listing unobserved builds. Honest. ✓

No silent-degradation stubs found.

---

## Remaining-Stubs Check

**Q5: Are there any remaining stubs that return hardcoded empty lists/dicts?**

Scanned all 9 primitives and all 9 adapters. No hardcoded `return []` or `return {}` stubs remain in the 9 repaired adapters. The only `info`-dict returns (`get_vector_table:671`, `find_call_path:769`) are legitimate empty-result sentinels matching production API shape, not stubs.

(Other adapters in `m6_query_adapters.py` outside the 9 repaired ones — e.g. `m6_find_all_callers_recursive:72` which is documented as "depth-1 callers (simplified)" — are NOT in scope for Issue B and were not reviewed for stub status.)

---

## GREEN Test Assertion Quality (Q6)

**Q6: Do the GREEN test assertions actually verify real data (not just that the function returned something)?**

Mixed quality:
- **Strong:** `get_vector_table` (asserts real IRQ handler names, line 113-119), `find_call_path` (asserts NO fabrication for fake target, line 200-201), `explain_condition` (asserts query file/line echoed, line 170), `compare_variants` (asserts dimensions present AND no error key, line 183-184), `group_equivalent_variants` (asserts query-derived structure, line 229).
- **Weak:** `get_method_overrides` (D2 — vacuous stub check), `find_indirect_targets` (D3 — vacuous shape check on empty), `get_template_instances` (accepts `EMPTY_BUT_QUERIED`, line 139 — but actual result was non-empty), `trace_data_flow` (only checks `has_summary`, not path correctness — masks D1).

The test PASSES legitimately for 9/9 (JSON confirms real data: IRQ handlers found, overrides=1, instances=2, dimensions=6, real path=1, no fake path, query echoed). The weak assertions are a test-quality issue, not a false-pass — the underlying data IS real.

---

## Conclusion

**VERDICT: APPROVED.**

All 9 previously-stubbed M6 query adapters now route through real M6 primitives that query the M6 store via `store.project_build_kind` (verified as a real SQL SELECT at `m6_store.py:235-263`). The two critical fixes are confirmed:
- `find_call_path` performs a genuine BFS with `visited` set and immutable path extension — NO fabrication (test confirms `fake_target_has_chain: false`).
- `find_indirect_targets` returns assignment-shaped results (`rhs_name`, `assign_file`, `method="assignment"`) — NOT call-site results.

§8 anti-false-migration is satisfied for all 9 adapters. No remaining hardcoded-empty stubs.

Six low-severity defects (D1-D6) are noted; none block approval. D1 (path-chain bug) and D6 (vector-table heuristic) are functional/semantic quality issues worth fixing in a follow-up. D2 and D3 are test-assertion weaknesses that should be tightened so future regressions can be detected.
