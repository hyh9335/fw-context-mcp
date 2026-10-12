# IMMUTABLE M9 GOAL

Implement safe, conditional cross-Build TU-level frontend reuse for fw-context.

A TU may reuse prior frontend work across different global Build/config identities ONLY when a dependency certificate proves that the TU's real frontend-relevant context is equivalent.

The mechanism must remain upstream of M6, preserve exact Build-specific semantics, produce zero false frontend reuse, fail closed when equivalence cannot be proven, avoid expensive certificate work when expected savings are not worthwhile, materially reduce repeated frontend parsing on eligible per-file-config workloads, impose low overhead on ineligible workloads, and preserve all M6/M6.5/M7 guarantees.

Non-goals: redesign M6, replace/fork Clang, build a new parser, implement general variational parsing, depend on cross-Build PCH, begin Clang CAS work, or improve hit rate through unsafe AST sharing.

Safety principle: `SAFE REUSE > MAXIMAL REUSE`. False miss is acceptable. False hit is blocking.

FreeRTOS/global-config workloads are allowed to produce near-zero hits if dependency closures genuinely differ.
