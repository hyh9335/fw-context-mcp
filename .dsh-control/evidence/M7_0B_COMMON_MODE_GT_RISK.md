# M7.0B §17: Common-Mode GT Risk Check

## Problem Statement

Both FULL_BUILD_GT (Oracle A) and M6 projection ultimately rely on
Clang-derived semantic information. If a Clang parsing error or a
libclang limitation affects both paths identically, the M6 parity
check (Oracle A vs M6) would still pass, but the underlying semantic
facts could be wrong.

This is a **common-mode failure**: a single cause affects both
the ground truth and the verification path.

## Risk Sources

1. **libclang parse failures** — If a TU fails to parse correctly,
   both extract_build_facts (GT) and the M6 projection see the
   same incomplete data. Parity passes but facts are missing.

2. **Preprocessor environment differences** — If the includes/defines
   differ between what the harness uses and what the real build uses,
   both GT and M6 see the same wrong preprocessor state.

3. **Canonicalization bugs** — If the canonicalization function
   misrepresents a fact (e.g., two different functions get the same
   canonical hash), both GT and M6 produce the same wrong facts.

4. **Struct layout misinterpretation** — If libclang reports wrong
   struct sizes or field offsets, both GT and M6 inherit the error.

## Mitigation via Oracle B/C

Oracle B (compiler structural sentinels) and Oracle C (known semantic
sentinels) provide independent verification that does NOT pass through
M6 canonicalization:

### Oracle B — Direct Compiler Evidence

- **Record layout dump** (`clang -Xclang -fdump-record-layouts`):
  Provides TCB struct sizes and field offsets directly from the
  compiler, NOT from libclang's clang.cindex API. This is a
  completely independent code path.

- **Preprocessor output** (`clang -E`):
  Confirms whether a symbol appears in the preprocessed source,
  independent of libclang symbol extraction.

- **Macro dump** (`clang -dM -E`):
  Confirms which macros are actually defined in the build environment.

### Oracle C — Source-Ground Truth

- **Expected field presence/absence**: Derived from reading the
  FreeRTOS source code (tasks.c lines 377-455) and understanding
  which `#if` guards control each TCB field. These expectations
  are grounded in the source text, not in any tool output.

- **Expected function presence/absence**: Derived from knowing
  which source files are guarded by which config macros.

## Coverage Assessment

For each high-risk semantic difference, at least one independent
check exists:

| Risk | Oracle A (GT/M6) | Oracle B (Compiler) | Oracle C (Source) |
|------|-------------------|---------------------|---------------------|
| TCB field presence | libclang fields | clang layout dump | Source #if analysis |
| TCB struct size | libclang type size | clang layout sizeof | N/A (source-derived) |
| Function presence | libclang symbols | clang preprocessor | Source #if guards |
| Macro environment | defines dict | clang -dM -E | FreeRTOSConfig.h text |
| Preprocessed output | N/A | clang -E hash | N/A |

## Residual Risk

The following common-mode paths remain:

1. **Clang itself has a bug** — Both Oracle B and the GT use clang.
   If clang misparses the source, both could be wrong. Mitigation:
   the source-grounded Oracle C expectations are derived from human
   reading of the source, not from clang output.

2. **Config file generation bug** — If `generate_config()` produces
   wrong config text, all three Oracles see the same wrong config.
   Mitigation: the matrix definition is documented, and macro
   fingerprints confirm the intended macros reach the compiler.

3. **Include path errors** — If the include paths are wrong, all
   oracles see the same wrong headers. Mitigation: the resource-dir
   discovery (§16) is hardened, and the TUs parse without fatal
   diagnostics.

## Conclusion

The Oracle B/C sentinels provide independent verification for all
high-risk semantic differences in the M7.0B matrix. The common-mode
risk between GT and M6 is mitigated by:

- Oracle B using a different clang code path (layout dump vs libclang)
- Oracle C using source-grounded expectations (not tool output)
- Macro fingerprints confirming config reaches compiler

No residual common-mode risk is identified for the semantic phenomena
tested in M7.0B. The matrix provides high-confidence verification of
M6 projection correctness for the tested configurations.
