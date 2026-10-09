# M6.5 Coverage Matrix — Test-Coverage Archaeology

> **Inventory of every current M6 test** as of HEAD `fe0c74d`.
> This matrix does NOT assume `21 cases / 39 builds` is sufficient.

## 1. Existing Test Inventory

| Test File | Purpose | Gates |
|---|---|---|
| `test_shadow_differential.py` | §34 query parity vs FULL_BUILD_GT | GATE 1 |
| `test_shadow_mutation.py` | §35 mutation testing (M1-M21) | GATE 2 |
| `test_shadow_store.py` | §M1.6 GC safety suite (a-e) | GATE 3 |
| `prototype_c.py` | Certificate safety + cost classes | GATE 4, GATE 7 |
| `prototype_a.py` | Phase R regression (Fact+BuildSet) | GATE 5 |
| `test_concurrency.py` | I3 concurrent indexing | I3 |
| `test_certificate_store.py` | I7 certificate store integration | I7 |
| `test_cross_tu.py` | I9 cross-TU resolution | I9 |
| `test_gc_lifecycle.py` | I10 GC lifecycle (8 steps) | I10 |
| `test_real_corpus.py` | I11/§36 real corpus differential | I11 |
| `test_cost_and_cert.py` | I12 cost class + cert safety | I12 |
| `test_usefulness_gate.py` | §10 frozen thresholds | GATE 8 |

## 2. Fact-Kind Coverage Matrix

| Fact Kind | Synthetic Case | Multi-Build Diff | Mutation | Query Parity | Real Corpus |
|---|---|---|---|---|---|
| Symbol | T01,T05,T07,T27 | ✓ (39 builds) | M4 (drop USR) | ✓ | ✓ (84 facts) |
| Function | T01,T07,T31 | ✓ | M2 (drop sig), M11 (drop attrs) | ✓ | ✓ (22 facts) |
| Call | CALL_GRAPH_DISPATCH, T30 | ✓ | M5 (drop line), M10 (drop callee_usr) | ✓ | ✓ (28 facts) |
| Variable | T01,T27,T31 | ✓ | M3 (drop init), M13 (drop storage) | ✓ | ✓ (40 facts) |
| Type | C04,T24,C05 | ✓ | M1 (drop field_sig), M12 (drop pack) | ✓ | ✓ (16 facts) |
| Field | T14,C05 | ✓ | M6 (drop field_type), M14 (drop bitfield) | ✓ | ✓ (22 facts) |
| EnumConst | T01 | ✓ | — (GAP) | ✓ | ✓ (6 facts) |
| Inherits | T32 | ✓ | M8 (drop base_usr) | ✓ | ✗ **MISSING** |
| Reference | T30,T31 | ✓ | M16 (drop ref_kind), M17 (drop target_usr), M18 (drop line) | ✓ | ✓ (117 facts) |
| ConditionalState | T22,C01,C08,C09 | ✓ | M15 (drop taken), M21 (drop directive) | ✓ | ✓ (40 facts) |
| Body | (implicit) | ✓ | M19 (drop content_digest) | ✓ | ✓ (22 facts) |
| FileContent | (implicit) | ✓ | M20 (drop content_digest) | ✓ | ✓ (4 facts) |

### Missing Coverage
1. **Inherits fact kind in real corpus**: REAL_FIRMWARE is pure C → 11/12. Need C++ corpus.
2. **EnumConst mutation**: No mutation targets EnumConst canon (value, enum_usr). Need M22+.

## 3. Corpus Case Inventory (21 cases)

| Case | Builds | Fact Kinds Exercised | Preprocessor Features |
|---|---|---|---|
| T01_object_macros | 2 | Symbol, Function, Variable, EnumConst | object macros, conditional |
| T04_variadic_macros | 2 | Symbol, Function | variadic macros, `__VA_ARGS__` |
| T05_token_paste | 2 | Symbol, Function | `##` token paste |
| T07_macro_gen_func_names | 2 | Symbol, Function | macro-generated identifiers |
| T14_macro_gen_struct_fields | 2 | Type, Field | macro-generated fields |
| T17_xmacros | 2 | Variable, EnumConst | X-Macros, repeated include |
| T18_repeated_include | 2 | Symbol, Type | include guards, repeated include |
| T22_attributes | 2 | Function, Type | attributes, `__attribute__` |
| T24_pragma_pack | 2 | Type, Field | `#pragma pack` |
| T25_file_macro | 2 | Variable | `__FILE__`, `__LINE__` |
| T27_counter | 2 | Variable, Symbol | `__COUNTER__` |
| T29_typeof | 2 | Type, Variable | `typeof`, statement expr |
| T30_cross_tu_callee | 2 | Call, Reference | cross-TU calls |
| T31_storage_class | 2 | Variable, Function | static/extern/inline |
| T32_inheritance | 2 | Inherits, Type, Function | C++ inheritance |
| C01_paste_conditional | 2 | ConditionalState, Symbol | `##` + conditional |
| C04_typedef_layout | 2 | Type, Field | typedef + struct layout |
| C05_pragma_macro_fields | 2 | Type, Field | `#pragma pack` + macro fields |
| C08_counter_paste | 1 | Symbol | `__COUNTER__` + `##` |
| C09_counter_expand_paste | 1 | Symbol | `__COUNTER__` + expansion + `##` |
| CALL_GRAPH_DISPATCH | 2 | Call, Function, Reference | function pointer dispatch |

### Preprocessor Feature Gaps
- ✗ Empty macro arguments
- ✗ Nested macros (3+ levels)
- ✗ Stringification `#`
- ✗ Macro-generated function definitions
- ✗ Macro-generated function declarations
- ✗ Macro-generated function parameters
- ✗ Macro-generated return types
- ✗ Macro-generated typedefs
- ✗ Macro-generated initializers
- ✗ Macro-generated function-pointer tables
- ✗ `#undef` / redefine order
- ✗ Command-line `-U`
- ✗ Forced include
- ✗ Conditional include
- ✗ Include search-root changes
- ✗ `#include_next`
- ✗ `#pragma once`
- ✗ `_Pragma`
- ✗ Generated headers
- ✗ `__INCLUDE_LEVEL__`
- ✗ Target-dependent builtins

### Combinatorial Coverage Gaps
- ✗ Token paste + conditional macro definition (pairwise)
- ✗ X-Macro + repeated include
- ✗ Generated header + include-root change
- ✗ Typedef macro + downstream function signature
- ✗ Typedef macro + struct layout
- ✗ Pragma pack + macro-generated fields (C05 is close but not differential)
- ✗ Static/extern + macro-generated identifier
- ✗ Inline + conditional function body
- ✗ `__COUNTER__` + token paste (C08/C09 exist but single-build)
- ✗ Function pointer table + conditional initializer
- ✗ Attribute + macro-generated declaration
- ✗ Compiler builtin + conditional typedef

## 4. C Type / ABI Coverage Gaps
- ✗ Anonymous struct
- ✗ Anonymous union
- ✗ Nested anonymous aggregates
- ✗ Bitfields (T14 covers macro-gen fields, not bitfield width differential)
- ✗ Bitfield width controlled by macros
- ✗ Flexible array members
- ✗ Enum conditional members
- ✗ Enum value changes (differential)
- ✗ Packed structs (T24 covers but not differential)
- ✗ Aligned structs
- ✗ Nested structs
- ✗ Unions
- ✗ Function pointer typedefs
- ✗ Qualifier changes (const/volatile differential)
- ✗ Signed/unsigned changes
- ✗ Integer width changes (int vs long)
- ✗ Pointer target-type changes

## 5. Linkage Coverage Gaps
- ✗ Static function same spelling multiple TUs
- ✗ Static variable same spelling multiple TUs
- ✗ Extern declaration + definition
- ✗ Tentative definitions
- ✗ Inline functions
- ✗ Static inline functions
- ✗ Weak symbols
- ✗ Alias attributes
- ✗ Visibility attributes
- ✗ Section attributes
- ✗ Conditionally present definitions
- ✗ Multiple declarations with Build-dependent attributes

## 6. Function Pointer / Indirect-Call Gaps
- ✗ Global function pointer
- ✗ Local function pointer
- ✗ Struct member callback
- ✗ Function-pointer arrays
- ✗ Macro-generated callback tables
- ✗ Conditional callback registration
- ✗ Initializer-selected callback
- ✗ Callback type changed by typedef macro
- ✗ Cross-TU callback declaration/definition

## 7. Cross-TU Dependency Gaps
- ✗ Header changes typedef/layout affecting multiple TUs
- ✗ Build-dependent macro affecting shared header
- ✗ CROSS_TU_STALE_SHARED_FACTS validation

## 8. Include-Resolution Gaps
- ✗ Same include spelling → different physical files per Build
- ✗ Include search-root changes

## 9. Generated Header Gaps
- ✗ Same path, different content per Build
- ✗ Restart + rebuild validation

## 10. Property-Based / Differential Gaps
- ✗ Generated C projects (2-6 TUs, 2-10 builds)
- ✗ Bounded RNG seed persistence
- ✗ Failing seed retention

## 11. Metamorphic Testing Gaps
- ✗ Equivalent transformations (typedef alias, macro indirection)
- ✗ Non-equivalent transformations (int→long, static→extern)

## 12. Mutation Coverage Gaps
- ✗ BuildSet membership mutation (M7 exists but not differential)
- ✗ Certificate macro dependency mutation
- ✗ Typedef propagation mutation
- ✗ Struct layout propagation mutation
- ✗ Include-root dependency mutation
- ✗ Generated-header dependency mutation
- ✗ Pragma state mutation
- ✗ Target/toolchain builtin dependency mutation
- ✗ Static/local symbol scoping mutation
- ✗ Token paste resolution mutation
- ✗ X-Macro invocation identity mutation
- ✗ Cross-TU dependency closure mutation
- ✗ GC reachability mutation
- ✗ BuildSet owner removal mutation
- ✗ Query Build filtering mutation
- ✗ EnumConst mutation (value, enum_usr)

## 13. Real Corpus Gaps
- ✗ FreeRTOS-Kernel (primary)
- ✗ Mbed TLS (secondary)
- ✗ Multi-build scaling (5/10/20 builds)
- ✗ P50/P95/P99 query latency baselines
- ✗ Lifecycle combination tests (pin/GC/rebuild/restart)

## 14. Summary

**Current state**: 21 synthetic cases, 39 builds, 21 mutations, 11/12 fact kinds in real corpus.

**Critical gaps for M6.5**:
1. Inherits fact kind in real corpus (the 12th kind)
2. EnumConst mutations
3. Combinatorial macro attacks (pairwise)
4. C type / ABI corner cases
5. Linkage and symbol-identity attacks
6. Function pointer / indirect-call testing
7. Cross-TU dependency closure
8. Include-resolution attacks
9. Generated header attacks
10. Property-based differential generation
11. Metamorphic testing
12. Mutation expansion (per safety category)
13. False-confidence tests
14. FreeRTOS-Kernel real corpus
15. Multi-build scaling (5/10/20)
16. Lifecycle combination tests
17. Performance baselines (P50/P95/P99)
