# Questions the Team Must Resolve

1. Operational definition of “discover a fault from source code”?
2. Is the target bug pattern, failure mode, root-cause hypothesis, detection rule, or sequence of these?
3. What exactly is a “rule”: static code rule, build/config rule, runtime observable rule, causal diagnosis rule, invariant, event sequence?
4. Which rule types can be validated with source-only GT?
5. What evidence is necessary to avoid hallucinated causal chains?
6. Which semantic relations are MVP-critical: direct call, callback, async, state/data flow, compile conditions, variant identity, vector, reachability?
7. How much uncertainty can the reasoning Agent safely absorb?
8. What can be delegated to Clang/clangd/tree-sitter/grep/build tools instead of fw-context?
9. What is the first public end-to-end fault case with objective answer?
10. What evidence would prove current architecture is a local optimum?
