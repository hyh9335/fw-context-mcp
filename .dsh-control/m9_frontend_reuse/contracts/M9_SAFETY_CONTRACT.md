# M9 SAFETY CONTRACT

A cross-Build frontend reuse HIT is valid only when all frontend-relevant inputs for the TU are proven equivalent.

At minimum explicitly account for: main TU content; language mode/std; target triple/ABI flags; compiler/libclang identity; resource-dir/builtin headers; forced includes; include search roots and order; transitive include resolution and content; generated headers; relevant effective macros; `-D`/`-U`; macro redefine/undef; compiler builtins; pragma state; `#pragma pack`; `#include_next`; X-Macro/repeated include contexts; `__FILE__`, `__LINE__`, `__COUNTER__`, `__INCLUDE_LEVEL__`; and any existing fw-context safety identity inputs.

Do not hash the entire compile command if that merely recreates `config_hash` and destroys safe cross-Build reuse.

Do not omit a dependency because M8 did not observe a failure.

When uncertain: MISS -> full parse.
