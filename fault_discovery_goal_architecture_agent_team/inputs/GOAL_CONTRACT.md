# Goal Contract — Authoritative

## Top-level goal
Build an Agent that can:
1. inspect firmware/software source code;
2. discover plausible fault mechanisms / failure modes in the implementation;
3. reconstruct causal mechanism and relevant execution/configuration conditions;
4. derive corresponding detection or diagnosis rules that are operationally useful;
5. attach source evidence and confidence/limitations so humans/downstream systems can audit them.

“From source code” is central. Logs, bug history, specs, or runtime traces may validate/enrich but must not silently redefine the goal into a log-mining project.

## Non-goals unless later justified
- perfect general-purpose static analysis;
- solving all compiler/linker semantics;
- making fw-context a standalone best-in-class product;
- exhaustively modeling every RTOS before an MVP;
- eliminating human review.

Success requires explaining how source facts become fault hypotheses, how hypotheses become rule candidates, how candidates are verified/falsified, how false positives are controlled, and which fw-context capabilities are necessary vs optional.
