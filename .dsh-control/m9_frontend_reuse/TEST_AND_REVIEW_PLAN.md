# M9 TEST AND REVIEW PLAN

Use two workload classes.

Negative-control: frozen FreeRTOS global-config corpus. Expected low/zero reuse but exact correctness and low overhead.

Positive-control: M8 per-file-config multi-variant workload. Expected meaningful cross-Build TU equivalence, avoided parses, frontend savings, and total wall-time gains.

Mandatory attacks: `-D`, `-U`, include-root change, transitive-header change, generated-header same path/new content, forced include, target triple, language standard, compiler/libclang, resource-dir, pragma pack, include order, `#include_next`, X-Macro/repeated include, `__FILE__`, `__LINE__`, `__COUNTER__`, builtin macros, static/local identity, conditional typedef/layout, macro-generated function/call/field.

Mutation tests must intentionally ignore key dependencies (include order, generated header, target, forced include, `-U`, pragma, resource dir, builtins) and every mutation must be caught.

Ground Truth must come from independent Clang/libclang parses and production semantic parity, never from the certificate itself.

Performance metrics: certificate cost, candidate pairs, hits, full parses avoided, frontend wall time, total indexing wall time, incremental Build cost. Use repeated runs and raw samples.

Fresh review checkpoints: certificate contract, adversarial safety closure, workload-probe policy, production integration, final performance validation.
