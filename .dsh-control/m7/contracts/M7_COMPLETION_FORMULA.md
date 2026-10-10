# M7 Completion Formula (frozen)

This contract defines the EXACT conditions under which the M7 program and
the original multi-version reuse Goal may be declared complete. Equivalent
logic is encoded in `run_original_goal_final_gate.py`.

## Part A — M7 production migration complete

`M7_SHARED_FACT_GRAPH_PRODUCTION_AUTHORITY_COMPLETE` requires ALL of:

```
BOOTSTRAP_PASS                              = true
M7_1_PASS                                  = true   (dual read parity)
M7_2_PASS                                  = true   (read authority)
M7_3_PASS                                  = true   (write authority)
M7_4_PASS                                  = true   (lifecycle authority)
M7_5_PASS                                  = true   (v2 retirement)

CORE_TOOL_DENOMINATOR_STABLE               = true
PRODUCTION_M6_NATIVE_READ_TOOLS            == CORE_SEMANTIC_TOOLS
PRODUCTION_M6_NATIVE_INDEX_ENTRYPOINTS     == NORMAL_INDEX_ENTRYPOINTS

NORMAL_OLD_V2_SEMANTIC_READS              = 0
NORMAL_OLD_V2_REQUIRED_WRITES              = 0
NORMAL_OLD_V2_FALLBACKS                    = 0
NORMAL_OLD_V2_FINALIZATION_DEPENDENCIES    = 0

FALSE_SHARED_SEMANTIC_FACTS                = 0
QUERY_PARITY_FAILURES                      = 0

FREERTOS_FULL_CANARY_PASS                  = true
MBEDTLS_CANARY_PASS                        = true
M6_5_ADVERSARIAL_REGRESSION_PASS           = true

LIFECYCLE_PASS                             = true
CRASH_RETRY_PASS                           = true
PRODUCTION_DEFAULT_LANE_PASS               = true

UNRESOLVED_BLOCKING_DEFECTS                = 0

FRESH_FINAL_VERIFIER_PASS                  = true
FRESH_FINAL_GUARDIAN_ACCEPT                = true
REVIEW_HEAD_MATCH                          = true
```

PLUS a durable checkpoint exists at the final HEAD.

## Part B — Original multi-version reuse Goal closed

`ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED` requires ALL of Part A PLUS:

```
semantic correctness                        = PASS
multi-Snapshot correctness                  = PASS
multi-Build correctness                     = PASS
historical correctness                      = PASS

fine-grained cross-Build reuse              = ACTIVE IN PRODUCTION
production read authority                   = Shared Fact Graph
production indexing authority               = Shared Fact Graph
production lifecycle authority              = Shared Fact Graph

false reuse                                 = 0
whole-TU v2 normal dependency               = 0

storage economics                           = PASS
semantic materialization economics          = PASS
query performance                           = PASS
end-to-end indexing economics               = PASS
```

## Part C — Alternative terminal: frontend bottleneck

If Part A passes BUT end-to-end indexing economics remain materially
blocked by Clang frontend parsing cost (frontend share dominates total
wall time so the original indexing-cost Goal is not meaningfully
achieved), then:

- `M7_SHARED_FACT_GRAPH_PRODUCTION_AUTHORITY_COMPLETE` = declared;
- `ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED` = NOT declared;
- Return `ORIGINAL_GOAL_REMAINING_FRONTEND_BOTTLENECK` with exact
  measurements (frontend share, total indexing scaling, why the Goal
  remains open, recommended next decision: evaluate a separate frontend
  incremental/variability-aware parsing project — do NOT auto-start it).

## Part D — Anti-gaming self-tests (master gate must include)

A. 29+ tools routed to M6 adapter, but adapter executes old v2 → FAIL.
B. M6 write path receives copied results after old full materialization
   → FAIL semantic-materialization authority.
C. Old v2 silently supplies answer when M6 query fails → FAIL.
D. Sharing/storage improves but wrong Build fact leaks → FAIL.
E. Performance report omits frontend cost → FAIL.
F. Verifier artifact belongs to previous HEAD → FAIL.
G. Guardian accepted a different HEAD → FAIL.
H. Current report says zero fallback but runtime counter observes one → FAIL.

## Part E — No lying

If any condition cannot be honestly satisfied, the program returns
`M7_BLOCKED_HUMAN_DECISION` (for genuine product/architecture decisions)
or continues the repair loop (for ordinary engineering). A markdown
narration may NEVER override a failing gate.
