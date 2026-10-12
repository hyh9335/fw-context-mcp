# M9 Certificate Archaeology — Frontend-Relevant Input Enumeration

**Lead**: M9 CERTIFICATE_ARCHAEOLOGY
**Date**: session-derived
**Source codebase**: `src/fw_context_mcp/` (Python — read with `read`, NOT fw-context)
**Safety contract**: `.dsh-control/m9_frontend_reuse/contracts/M9_SAFETY_CONTRACT.md`

> Read against production code. Every claim carries a `file:line` citation. Where the
> existing code already captures an input, the report says so and points at the capture
> site. Where it does not, the report names the attack vector.

---

## Executive Summary

The production codebase **already contains a substantially complete certificate key**:
`store_v2.keys.compute_tu_semantic_key` (`store_v2/keys.py:245`), reached through
`store_v2.certify.build_certificate` (`store_v2/certify.py:753`). Its four layers
(L0 source+language, L1 canonical NCC argv, L2 forced+closure+macro content,
L3 toolchain+parser+environment) cover **every** input the M9 safety contract
names — including the historically-missed `-U` undefines, ordered include roots,
forced-include content, resource-dir, and libclang identity.

The M9 work is therefore **NOT** "design a new certificate from scratch". It is:

1. **Confirm** the cross-Build frontend-reuse HIT consults `compute_tu_semantic_key`
   (the KB-C `root` / `tusemantic_key`), not the weaker `config_hash`,
   `flags_hash`, or `manifest_entry_hash`.
2. **Close one residual gap**: the `skip_files` extraction parameter
   (`symbols.py:3656`) changes `parsed.symbols` but is not a frontend input —
   M9 must decide whether reuse targets the `tu` (parse only) or the whole
   `parsed` (extraction too).
3. **Confirm the no-mutation invariant**: none of the three downstream consumers
   mutate `parsed` (verified: `materialize_unit_into_m6`, `certify_and_publish_for_unit`,
   `store_symbols_for_unit` are all read-only over `parsed`).

The weaker Tier-2 hashes (`config_hash`, `flags_hash`, `manifest_entry_hash`) remain
correct for **staleness** (within one build) but are **unsafe for cross-Build reuse**
because they are set-based or build-grouping-scoped. The KB-C root is the only
identity that preserves order and captures content + environment.

---

## 1. The `parsed` object contract

### Definition

`ExtractionResult` is a `@dataclass` at `indexer/symbols.py:3484-3496`:

```python
@dataclass
class ExtractionResult:
    tu: cx.TranslationUnit | None = None
    symbols: list = field(default_factory=list)
    references: list = field(default_factory=list)
    inheritance: list = field(default_factory=list)
    indirect_call_sites: list = field(default_factory=list)
    fp_assignments: list = field(default_factory=list)
    macros: list = field(default_factory=list)
    pending_dispatches: list = field(default_factory=list)
    call_sites: list = field(default_factory=list)
    newly_seen_files: set[str] = field(default_factory=set)
```

### Production

`extract_all()` at `indexer/symbols.py:3651-3826` constructs it. The parse call:

```python
tu = _get_index().parse(
    str(unit.file),
    args=unit.clang_args,
    options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
)
```
(`symbols.py:3697-3701`)

- `_get_index()` (`symbols.py:91-103`) returns a **process-wide singleton**
  `cx.Index` created once via `cx.Index.create()`. The Index serializes concurrent
  `parse()` calls internally; it carries the libclang resource-dir baked in at
  `Index.create()` time. **Changing the libclang install changes the Index, which
  changes every TU produced from it.**
- `PARSE_DETAILED_PROCESSING_RECORD` enables the skipped-ranges / conditional
  processing record that `collect_skipped_lines` (`indexer/skipped_ranges.py`)
  reads to produce the ifdef-filtered `files.content`.
- `unit.clang_args` is the **post-normalization** argv: GCC-only flags stripped
  (`compile_commands.py:23-81` `_DROP_FLAGS`/`_DROP_PREFIXES`/`_DROP_WITH_ARG`),
  relative `-I` resolved against `unit.directory`, `--target` injected from the
  compiler name, GCC cross-compiler system includes appended
  (`compile_commands.py:521` `_gcc_system_includes`), `-Wno-unknown-warning-option`
  appended.
- `unit.file` is the resolved source path; `unit.directory` is the compile cwd;
  `unit.language` is `"c"`/`"cpp"` derived from extension (`compile_commands.py:262`);
  `unit.raw_entry` is the raw `compile_commands.json` dict
  (`compile_commands.py:230-244`).

### What `parsed.tu` carries (build-bound state)

`cx.TranslationUnit` carries:
- The AST (`tu.cursor` — the translation-unit cursor).
- The token stream (`tu.cursor.get_tokens()`).
- The include closure (`tu.get_includes()` — first-inclusion-ordered resolved includes).
- Diagnostics (`tu.diagnostics` — includes "file not found" fatal errors).
- The skipped-processing record (via the `PARSE_DETAILED_PROCESSING_RECORD` option).

All of this is **build-bound**: it is the specific AST that this build's flags + source
+ headers + libclang produced. The TU is not a cache of reusable text — it IS the parse.

### Is `parsed` safe to share across builds?

**Only when all frontend inputs are proven equivalent.** The `tu` object is the
instantiation of one build's frontend inputs; sharing it with another build means
Build B's queries are answered by Build A's AST. The object itself is immutable after
construction (see §5 for the downstream-mutation proof), so sharing is mechanically
safe — but the **safety gate** is the certificate key equality, not the object's shape.

`newly_seen_files` (`symbols.py:3496`) is a `set[str]` populated during the symbol
walk — it is an **extraction artifact**, not a frontend input. It depends on
`skip_files` (which subtrees were skipped) and the walk order.

---

## 2. Per-input dependency map

Legend:
- **Where it enters parse** — the code location where the input reaches libclang.
- **Currently captured by** — the existing identity/hash that covers it.
- **Captured correctly?** — YES (order/content preserved) / PARTIAL (set-based or
  incomplete) / NO (not captured).
- **M9 cert must add?** — NO (already in KB-C root) / VERIFY / YES (gap to close).

| Input | Where it enters parse | Currently captured by | Captured correctly? | M9 cert must add? |
|---|---|---|---|---|
| **main TU content** | `unit.file` resolved → `_get_index().parse(str(unit.file), ...)` `symbols.py:3697-3698` | L0 = `H("L0", source_blob_sha, language)` `keys.py:270`; `source_blob_sha` = `sha256(main_path.read_bytes())` `certify.py:799` | **YES** (full-width sha256 of current bytes, no trust, no cache) | **NO** |
| **language mode/std** | `unit.language` ("c"/"cpp") + `-x`/`-std` in `unit.clang_args` | L0 includes `language` `keys.py:270`; `-x`/`-std` flow through `ncc.canonicalize` into `ncc_serial` (L1) `ncc.py:145+` | **YES** (both the language tag and the `-x`/`-std` flags) | **NO** |
| **target triple** | `--target=`/`-target` injected into `clang_args` by `normalize_args` (`compile_commands.py` from compiler name); also in raw argv when explicit | `-target`/`--target` tokens preserved in-order in `ncc_serial` (L1) via `ncc.canonicalize`; `target_triple` ALSO captured in `environment_digest` via `_certify_parser_identity` `certify.py:977+` | **YES** (double coverage: argv leg + environment leg) | **NO** |
| **ABI flags** (`-fabi-version`, `-fshort-wchar`, `-m*`) | in `unit.clang_args` (some GCC-only ones stripped by `_DROP_FLAGS`/`_DROP_PREFIXES` `compile_commands.py:23-73`) | preserved in-order in `ncc_serial` (L1) — ncc processes the RAW argv (`_raw_argv(unit)` `certify.py:368` reads `unit.raw_entry`), so it captures flags the libclang normalizer DROPPED | **YES** (raw argv is more complete than the normalized argv) | **NO** |
| **compiler/libclang identity** | the `cx.Index.create()` singleton `symbols.py:102`; libclang version baked in | L3: `toolchain_salt = H("fwctx-tc/v1", capture_binary_id)` `certify.py:914`; `parser_salt = H("fwctx-ps/v1", libclang_id, indexer_generation)` `certify.py:915-916`; `libclang_identity()` + `clang_cli_identity()` in `_certify_parser_identity` `certify.py:977+` | **YES** (full class-C capture; failure POISONS, never `UNKNOWN_TAG` for the parser identity) | **NO** |
| **resource-dir / builtin headers** | the `cx.Index` resource dir + clang's builtin headers (`stdint.h` etc.) | L3 `environment_digest` = digest over `libclang_id + capture_binary_id + builtin_digest + builtin_count + target_triple` (`_certify_parser_identity` `certify.py:977+`); `builtin_digest` is the sha256 of `clang -dM -E` builtin predefines | **YES** (builtin predefine digest + binary identity → resource-dir implied) | **NO** |
| **forced includes** (`-include`, `-imacros`) | `-include`/`-imacros` in `unit.clang_args`; libclang injects them before the TU's own content | L1 captures the `-include`/`-imacros` tokens in-order in `ncc_serial` (ncc `JOIN` set `ncc.py:46`); **c3** = `H("c3", *forced_content_legs)` `keys.py:275` where `forced_content_legs` = ordered full-width sha256 of each forced file's BYTES `certify.py:828-844` | **YES** (path in L1, CONTENT in c3 — content is the strong leg; unreadable forced input POISONS `certify.py:838`) | **NO** |
| **include search roots and order** (`-I`, `-isystem`, `-iquote`, `-idirafter`) | in `unit.clang_args`, resolved against `unit.directory` by `normalize_args` | L1: `ncc.canonicalize` preserves ORDER (no sort/dedup — `ncc.py:18-20` "NO sorting / dedup of compile semantics") and joins `-I <path>` → `-I<path>` (`ncc.py:46` `JOIN` set); paths de-tainted to `<PROJECT_ROOT>`/`<COMPILE_DIR>` placeholders (`ncc.py:36`, `ncc.py:59-72`); external roots get `ext:role:<path>` then class-C post-step replaces certified ones with `envcert:<digest>:<rel>` | **YES** (order preserved — this is the critical advantage over `flags_hash` which SORTS) | **NO** |
| **transitive include resolution and content** | libclang resolves includes during parse; closure surfaces via `tu.get_includes()` | **c4** = `H("c4-content-seq", *cert_content_sequence)` `keys.py:278`; `cert_content_sequence` = ordered content legs from `_closure_walk(parsed, ...)` `certify.py:596-630` which walks `parsed.tu.get_includes()` in first-inclusion order; each member re-hashed at cert grade via `_rehash_member` `certify.py:653-660` (reads CURRENT bytes, no trust); unresolved include → `closure_complete=False` → POISONS `certify.py:862-867` | **YES** (content + order + completeness flag; `#include_next` resolution is implicit in the resolved closure) | **NO** |
| **generated headers** | surfaced during parse as includes; `generated` flag is a manifest diagnostic | `_closure_walk` sets `generated=_is_generated_header(display, build_dir_patterns)` `certify.py:628` — BUT this is **diagnostic only** (`certify.py:601-603` "feeds ONLY the diagnostic `generated` flag; certification re-hashes every member's current bytes regardless"). Content capture is unconditional. | **YES** (content captured regardless of `generated`; the `generated` trust rule is a staleness-only optimization, bypassed at certification `certify.py:11`) | **NO** |
| **relevant effective macros** (`-D`) | `-D` in `unit.clang_args` → libclang preprocessor | L1: `-D` tokens preserved in-order in `ncc_serial` (`ncc.py:226-231`); **c5** = `macro_state_digest` = full `clang -dM -E` output digest (object-like AND function-like) `certify.py:884-897`, `macro_environment_digest(lines)` | **YES** (double coverage: argv `-D` leg + full preprocessed macro-state leg — the macro-state leg is the strong one because it captures builtin + command-line + forced-include macros together) | **NO** |
| **`-U` (undefines)** | `-U` in `unit.clang_args` → removes builtin or previously-defined macros | L1: `-U` tokens preserved in-order in `ncc_serial` (`ncc.py:232-237`); `ncc.canonicalize` records `cc.defines_final[body] = "<undef>"` so undefines are explicit in the NCC; c5 macro-state leg reflects the FINAL macro environment (post-undef) | **YES** (this is the historically-missed input — KB-C captures it in BOTH the argv leg and the macro-state leg) | **NO** |
| **macro redefine/undef (in-file `#undef`/`#define` of builtin)** | inside source/header content → preprocessor | covered by `source_blob_sha` (L0) for the file doing it; for cross-file effects, covered by c4 closure content + c5 macro-state; an in-header `#undef` of a builtin affects every includer → c4 captures the header content, c5 captures the resulting macro state | **YES** | **NO** |
| **compiler builtins** | libclang builtins (implied by libclang version + target) | L3 `environment_digest` (builtin_digest + builtin_count + target_triple + libclang_id) | **YES** | **NO** |
| **pragma state (`#pragma pack`, `#pragma once`)** | inside source/header content → preprocessor/parser | covered by `source_blob_sha` (L0) for the file; cross-file via c4 closure content (`#pragma once` affects include resolution → reflected in c4 closure; `#pragma pack` affects layout → reflected in symbol signatures captured from the AST) | **YES** | **NO** |
| **`#include_next`** | preprocessor directive → affects include resolution | implicit in c4 closure (libclang resolves `#include_next` and the resolved file appears in `tu.get_includes()`); the search roots that `#include_next` walks are captured in-order in L1 (`-I`/`-isystem`/`-iquote`/`-idirafter`) | **YES** (resolution + content captured; the directive itself is inside the file content = L0/c4) | **NO** |
| **X-Macro / repeated include contexts** | the file doing the repeated `#include` → source content; the included file's content → closure | covered by `source_blob_sha` (L0) for the file doing the repeated include; c4 captures the closure; c5 captures the resulting macro state (X-macro tables expand via macros → c5 captures the final state) | **YES** | **NO** |
| **`__FILE__`** | preprocessor builtin → expands to the file path string | covered by `source_blob_sha` (L0) (the macro's value depends on the file path, which is the cert's `main_file_display`/`main_file_abs_path` `certify.py:772`, `certify.py:793`); additionally `_tu_path_spelling_sensitive` `certify.py:663-685` scans the token stream for `__FILE__`/`__BASE_FILE__`/`__INCLUDE_LEVEL__` and sets `path_spelling_sensitive=True` → forces path-sensitive comparison | **YES** (with explicit path-sensitivity guard) | **NO** |
| **`__LINE__`** | preprocessor builtin → expands to line number | covered by `source_blob_sha` (L0) (position-dependent, same source → same progression) | **YES** | **NO** |
| **`__COUNTER__`** | preprocessor builtin → stateful counter reset per-TU | covered by `source_blob_sha` (L0) (same source → same counter progression within the TU) | **YES** | **NO** |
| **`__INCLUDE_LEVEL__`** | preprocessor builtin → depends on include depth/order | covered by c4 closure order + `path_spelling_sensitive` guard (`certify.py:677` explicitly lists `__INCLUDE_LEVEL__`) | **YES** | **NO** |
| **`-include-pch`** | precompiled header forced input | L1 captures the `-include-pch` token (in ncc `JOIN` set `context_identity.py:73` and `ncc.py:46` — note: ncc `JOIN` does NOT list `-include-pch`; verify); the PCH content is NOT in c3 (c3 is `-include`/`-imacros` only per `_collect_forced_inputs` `certify.py:400`) | **PARTIAL** — flag captured in L1, but PCH binary content not in c3; PCH is build-output so its content varies per build | **VERIFY** — if PCH is used, M9 should either add PCH content to c3 or MISS→full parse |
| **response file content (`@rsp`)** | expanded into `clang_args` by `normalize_args` (`compile_commands.py:62-66`) | L1: `ncc.canonicalize` re-expands `@rsp` from `rsp_contents` (`certify.py:807`, `ncc.py:145`); `rsp_contents` captured via `_expand_rsp` `certify.py:383`; rsp-unreadable → POISONS `certify.py:808-810`; `response_file_hashes` also in `context_identity.compute_context_id` `context_identity.py:247-262` | **YES** (content re-expanded and canonicalized; missing rsp poisons) | **NO** |
| **`skip_files` (extraction param)** | `extract_all(skip_files=...)` `symbols.py:3656` → changes which subtrees are walked → changes `parsed.symbols` | **NOT captured by any cert key** — it is a run-order input (which TU claimed which header first within an index run), not a build-frontend input | **NO** (by design — not a frontend input) | **VERIFY** — see §4 gap analysis; M9 must decide: reuse `tu` only (re-extract with consuming build's skip_files) or reuse whole `parsed` (then skip_files equivalence must be enforced) |
| **`with_refs` (extraction flag)** | `extract_all(with_refs=...)` `symbols.py:3653` → changes whether references/call_sites are extracted | **NOT captured** — extraction flag, not a frontend input | **NO** (by design) | **VERIFY** — same as skip_files: if reusing whole `parsed`, `with_refs` must match |
| **vendor/project/build_dir patterns (storage path)** | `store_symbols_for_unit(vendor_patterns=, project_patterns=, build_dir_patterns=)` `ops.py:1554-1561` | **NOT frontend inputs** — they affect `is_project`/`generated` CLASSIFICATION of stored rows, NOT the AST content | N/A (out of scope for cert key) | **NO** |
| **`PARSE_DETAILED_PROCESSING_RECORD` option** | `symbols.py:3700` — enables skipped-ranges/conditional record | constant (always set) → does not vary across builds | **YES** (constant, no capture needed) | **NO** |

---

## 3. The `config_hash` vs certificate-key distinction

### Why hashing the whole compile command recreates `config_hash`

`config_hash` (`indexer/manifest.py` `compute_config_hash`) is the **build-grouping**
identity — a set-union of dialect flags across ALL TUs of the build. It deliberately
DROPS per-file identity (so adding/removing a source file doesn't change it) and is
the wrong granularity for per-TU reuse.

The three weaker Tier-2 hashes and why each is unsafe for cross-Build reuse:

| Hash | Definition | Cross-Build reuse defect |
|---|---|---|
| `config_hash` | set-union dialect hash across all TUs (`manifest.compute_config_hash`) | Two TUs of the SAME build with different files collapse; two builds with the same dialect but different per-TU flags collapse. **Build-grouping only.** |
| `flags_hash` | `compute_flags_hash(entry)` = `sha256(" ".join(sorted(normalized_args)))` `config_hash.py:91-100` | **SORTS args** → destroys order of `-I`/`-isystem`/`-iquote` (order matters for resolution: `-I/a -I/b` vs `-I/b -I/a` resolves a `#include "x.h"` to different files when `x.h` exists in both). Also drops the source file token (keyed separately) and `_DROP_WITH_ARG`. **Set-based → false HIT on flag reordering.** |
| `manifest_entry_hash` | `H(source_hash | header_hashes)` where `header_hashes = "".join(sorted(...))` `manifest.py:1497-1520` | Captures source + transitive header CONTENT, but **sorts headers by path** → may miss include-order-sensitive cases. Also: `header_is_trusted` (`manifest.py:1140-1171`) SKIPS re-hashing `generated` headers during staleness — a build-generated header's content changes per build without triggering re-parse (acceptable for staleness, unacceptable for cross-Build reuse). |
| `context_id` | `compute_context_id(config_hash, main_file, compile_dir, canonical_argv, response_hashes, output_name)` `context_identity.py:247-262` | Preserves argv ORDER (`canonical_argv` `context_identity.py:61-85`), but INCLUDES `config_hash` (build-grouping) and `output_name` (transient `-o` target) — both irrelevant to frontend semantics. Better than `flags_hash` but still mixes grouping + transient components. |
| `compute_tu_content_hash` | `H(source_hash | flags_hash | manifest_entry_hash)` `config_hash.py:103-125` | Composes three set-based/sorted hashes → inherits all their defects. This is the Tier-2 content hash stored in `files.content_hash`. |

### The MINIMAL frontend-relevant argv set

From the safety contract's "do not hash the entire compile command if that merely
recreates `config_hash`": the frontend-relevant tokens are exactly those the NCC
canonicalizer KEEPS (`ncc.py:145-240`), which it classifies into:

- **Class-A (defines/undefines)**: `-D`, `-U` — in-order, joined, last-wins recorded.
- **Class-B (forced inputs)**: `-include`, `-imacros` — in-order, content captured separately in c3.
- **Class-C (include roots)**: `-I`, `-isystem`, `-iquote`, `-idirafter` — in-order, paths de-tainted.
- **Class-D (language/target)**: `-x`, `-std`, `-m*`, `--target`/`-target`, `--sysroot`.
- **Class-E (drop)**: `-o`, `-MF`, `-MT`, `-MQ`, `-specs` (output-only), `-MD`/`-MP`/`-MMD`/`-MG`/`-c`/`-g`/`-pipe`/`-save-temps`/`-flto`/`-fno-common` and GCC-only `-W*` (codegen/debug/output-only — don't affect the AST).

The dropped classes are provably non-semantic: `-c`/`-o` are output control; `-MD`
family is dep-gen; `-g` is debug info; `-flto` is link-time; `-W*` are diagnostics.
None changes a declaration, a macro, or which code survives `#ifdef`.

### The candidate certificate key formula (ALREADY IN PRODUCTION)

The existing `compute_tu_semantic_key` (`keys.py:245-321`) IS the M9 certificate
key. Its composition:

```
L0  = H("L0", source_blob_sha, language)                         # main TU content + language mode
L1  = H("L1", ncc_serial)                                        # canonical argv (ordered: -D, -U, -include,
                                                                  #   -imacros, -I, -isystem, -iquote, -idirafter,
                                                                  #   -x, -std, -m*, --target, --sysroot;
                                                                  #   paths de-tainted; rsp expanded; class-C post-step applied)
c3  = H("c3", *forced_content_legs)                              # ordered forced-include CONTENT (full-width sha256)
c4  = H("c4-content-seq", *cert_content_sequence)                # ordered resolved-closure CONTENT (directive-eval order)
c5  = macro_state_digest                                         # full clang -dM -E output (object-like + function-like)
L2  = H("fwctx-l2c/v1", c3, c4, c5, key_format)                 # closure-scoped content layer
L3  = H("L3", toolchain_salt, parser_salt,                       # compiler binary identity + libclang identity
        str(parser_generation),                                  #   + indexer generation
        environment_digest)                                      #   + resource-dir + builtins + target triple
root = H("fwctx-tukb/v1", L0, L1, L2, L3)                       # THE TUSemanticKey (the M9 cert key)
```

**This matches the contract's candidate formula exactly**, with these correspondences:

| Contract term | KB-C leg |
|---|---|
| `source_hash` | L0 (`source_blob_sha`) |
| `defines_canonical_sorted` | L1 (`-D` in `ncc_serial`, order-preserved not sorted — sorting is WRONG, KB-C keeps order) |
| `undefines_canonical_sorted` | L1 (`-U` in `ncc_serial` `ncc.py:232-237`) |
| `forced_includes_content_hash` | c3 (`forced_content_legs`) |
| `include_search_roots_canonical` | L1 (`-I`/`-isystem`/`-iquote`/`-idirafter` in `ncc_serial`, ORDER preserved) |
| `header_closure_content_hash` | c4 (`cert_content_sequence`) |
| `language_mode` | L0 (`language`) + L1 (`-x`/`-std`) |
| `target_triple` | L1 (`--target`/`-target`) + L3 (`environment_digest`) |
| `abi_flags` | L1 (`-m*` in `ncc_serial`) |
| `resource_dir_hash` | L3 (`environment_digest` includes `builtin_digest`) |
| `libclang_identity` | L3 (`parser_salt` + `environment_digest`) |

The contract's "do not hash the entire compile command" is satisfied because the
NCC canonicalizer DROPS class-E (output/debug/codegen) tokens before hashing —
so `ncc_serial` is the frontend-relevant subset, not the whole command.

---

## 4. Risks and gaps

### 4.1 The `skip_files` / `with_refs` extraction-parameter gap

**The gap**: `extract_all(skip_files=..., with_refs=...)` (`symbols.py:3653-3656`)
produces a DIFFERENT `parsed` (different `symbols`, `references`, `call_sites`,
`newly_seen_files`) for the SAME frontend inputs, depending on these extraction
parameters. The cert key captures the PARSE inputs (frontend), not the extraction
parameters.

**Why it is (mostly) out of scope**: `skip_files` is a run-order optimization —
which TU claims which header first within a single index run. It does not change
the AST (`parsed.tu`); it changes only which subtrees the symbol WALK visits.
`with_refs` toggles whether the reference/call-site sub-walks run.

**The attack vector**: Build A indexes TU X with `skip_files={H1}` (H1 claimed by
an earlier TU in run-order) → `parsed_A.symbols` excludes H1's symbols. Build B
indexes TU X with `skip_files={}` → `parsed_B.symbols` includes H1's symbols. The
cert keys are IDENTICAL (frontend inputs match), but `parsed_A.symbols ≠ parsed_B.symbols`.
If M9 reuses the whole `parsed` object across builds, Build B would inherit Build A's
incomplete symbol set for TU X.

**Mitigation (M9 design decision)**:
- **Option A (reuse `tu` only)**: M9 reuses the `cx.TranslationUnit` (the parse) and
  re-runs `extract_all`'s walk on the consuming build's `skip_files`/`with_refs`.
  Safe, but loses the extraction-speed win.
- **Option B (reuse whole `parsed`)**: M9 must add `skip_files` (as a frozen set
  hash) and `with_refs` to the cert key, OR enforce that the reused `parsed` was
  produced with `skip_files=∅` and `with_refs=True` (canonical extraction). The
  latter is cleaner: a canonical extraction is reusable across all builds because
  `skip_files=∅` extracts everything (superset), and the consuming build can filter
  post-hoc.

**Recommendation**: Option B with canonical extraction (`skip_files=∅`,
`with_refs=True`). The cert key already covers all frontend inputs; canonical
extraction makes the `parsed` object itself canonical.

### 4.2 The `-include-pch` gap

`_collect_forced_inputs` (`certify.py:400`) collects forced inputs — verify it
includes `-include-pch`. The `JOIN` set in `context_identity.py:73` lists
`-include-pch`, but `ncc.py:46` `JOIN` does NOT (it lists `-include`, `-imacros`
but not `-include-pch`). If a build uses PCH, the PCH binary content is NOT in c3
(c3 is `-include`/`-imacros` content only), and the `-include-pch` token may be
dropped by the NCC canonicalizer.

**Attack vector**: Build A compiles with PCH-A; Build B compiles with PCH-B (same
source, same flags, different PCH content). If `-include-pch` is dropped, cert keys
match → false HIT → Build B gets Build A's AST (which reflects PCH-A's precompiled
declarations).

**Mitigation**: M9 must verify `_collect_forced_inputs` and `ncc.JOIN` handle
`-include-pch`. If not, either add PCH content to c3 or MISS→full parse when
`-include-pch` is present. PCH is build-output, so its content varies per build —
safest is MISS→full parse.

### 4.3 The generated-header trust rule (staleness-only, correctly bypassed at certification)

`header_is_trusted` (`manifest.py:1140-1171`) returns True ONLY for build-generated
headers, and the staleness check SKIPS re-hashing them. This is correct for
staleness (a generated header's content changes per build without semantic change),
but would be FATAL for cross-Build reuse.

**Verified safe**: `build_certificate` (`certify.py:596-603`) explicitly bypasses the
trust rule — "certification re-hashes every member's current bytes regardless of it".
So c4 captures generated-header CONTENT at certification grade. No gap.

### 4.4 The `discover_implicit_include_dirs` best-effort gap

`discover_implicit_include_dirs` (`certify.py:447`) probes the compiler's implicit
include chain via `clang -v -E`. A failure yields NO implicit-dir rows, and
"admission will MISS honestly" (`certify.py:1150-1151`). The implicit dirs are
captured as `dir_attestations` (Slice-4A) — a separate attestation face, NOT part of
the KB-C root digest.

**Risk**: if the implicit include chain changes (e.g., a different GCC sysroot is
installed) without changing the explicit `-I`/`-isystem` flags or the libclang
identity, the cert key may not catch it. The `environment_digest` (L3) captures the
builtin predefines via `clang -dM -E`, which reflects the implicit include chain
indirectly (a changed `stdint.h` changes builtin macro expansions).

**Mitigation**: verify that `environment_digest` (builtin_digest) is sensitive to
implicit-include-chain changes. If a header-only change in the implicit chain does
not affect `clang -dM -E` output, M9 should add `implicit_dirs` content to L3 or
the attestation set. Low risk in practice (implicit dirs are toolchain-stable).

### 4.5 The `flags_hash` sorting defect (Tier-2 only, not in KB-C)

`compute_flags_hash` (`config_hash.py:91-100`) SORTS args:
`args = " ".join(norm["args"])` where `norm["args"]` is `sorted(result)`
(`config_hash.py:87`). This destroys `-I` order. **This defect exists ONLY in the
Tier-2 staleness hash, NOT in the KB-C root** (which uses `ncc_serial` with
order-preserved canonicalization). M9 must NOT use `flags_hash` or
`compute_tu_content_hash` (which composes `flags_hash`) for cross-Build reuse
decisions — only for within-build staleness.

---

## 5. The reuse safety question (mutation analysis)

**Question**: Does `materialize_unit_into_m6` MUTATE `parsed`? Does
`certify_and_publish_for_unit` MUTATE `parsed`? Does `store_symbols_for_unit`
MUTATE `parsed`?

**Answer**: NO to all three. The `parsed` object is read-only through the entire
downstream pipeline. Reuse across builds is mechanically safe (the object won't be
corrupted by a consumer) — the safety gate is the certificate key, not mutation
concerns.

### 5.1 `materialize_unit_into_m6` (`store_v2/m6_direct_materialization.py:48-293`)

Reads ONLY via `getattr`:
- `getattr(parsed, "symbols", None)` (line 90)
- `getattr(parsed, "call_sites", None)`
- `getattr(parsed, "references", None)`
- `getattr(parsed, "inheritance", None)`

Constructs NEW `usr_to_name` dicts, NEW fact lists. Calls `store.register_world()`
and `store.add_fact()` — mutates the STORE, never `parsed`. No assignment to any
`parsed.*` attribute anywhere in the function body (lines 48-293).

**Verdict: READ-ONLY. Reuse safe.**

### 5.2 `certify_and_publish_for_unit` → `build_certificate` (`store_v2/certify.py:753-967`)

Reads from `parsed`:
- `_tu_path_spelling_sensitive(parsed)` (line 777) — reads `parsed.tu.cursor.get_tokens()` (READ-ONLY token iteration)
- `_closure_walk(parsed, project_root, build_dir_patterns)` (line 860) — reads `parsed.tu.get_includes()` (line 608) — READ-ONLY include iteration
- `_diagnose_unresolved_includes(parsed)` (line 861) — reads `parsed.tu.diagnostics` (line 637) — READ-ONLY diagnostic iteration
- `_rehash_member(member)` (line 870) — reads `member.abs_path` from disk, NOT from `parsed`

Then `extract_state_facts(parsed, cert)` (`facts.py:114-298`) reads:
- `parsed.symbols` (line 141) — iterates, sorts into NEW `ordered` list
- `parsed.references`, `parsed.indirect_call_sites`, `parsed.fp_assignments`, `parsed.inheritance`

Writes to `facts` (NEW StateFacts) and `cert.branch_states = list(facts.branch_states)`
(line 297) — **mutates the CERT, NOT `parsed`**.

`build_cold_payload(parsed, cert, facts)` (`facts.py:493-526`) reads `parsed.macros`
(line 509) and `parsed.symbols` (line 518) — iterates into NEW lists.

**Verdict: READ-ONLY over `parsed`. Reuse safe.** (The cert object IS mutated, but
the cert is per-publication, not shared across builds.)

### 5.3 `store_symbols_for_unit` (`indexer/ops.py:1549-1848`)

Uses `pre_parsed` (the ExtractionResult):
- `result = pre_parsed` (line 1623)
- `tu = result.tu` (line 1624)
- `syms = result.symbols` (line 1625) — iterates into NEW symbol rows
- `refs = result.references`, `inheritance = result.inheritance`, etc.

Then calls `_build_filtered_file_content(conn, unit, ..., existing_tu=tu, ...)`
(ops.py:805-809) which reuses `tu` via `tu.get_includes()`, `tu.cursor.get_tokens()`,
`tu.cursor.walk_preorder()`, `collect_skipped_lines(tu)` — ALL READ-ONLY.

Calls `collect_skipped_and_kept(tu)` — derives skipped/kept sets FROM tu, writes to
NEW dicts, does NOT write back to `tu`.

Does DB writes (`insert_symbols_batch`, `insert_refs_batch`, etc.) — mutates the DB,
never `parsed`.

**Verdict: READ-ONLY over `parsed`. Reuse safe.**

### Conclusion

All three downstream consumers (`materialize_unit_into_m6`,
`certify_and_publish_for_unit`, `store_symbols_for_unit`) treat `parsed` as a
read-only input. M9 can safely share the `parsed` object across builds — **provided
the certificate key (KB-C root) proves the frontend inputs equivalent**. No
deep-copy is required for mutation safety.

---

## 6. Existing safety identity inputs (from fw-context itself)

fw-context ALREADY computes several identity layers. M9 should REUSE these, not
reinvent them:

### 6.1 `compute_tu_semantic_key` (the KB-C root) — `store_v2/keys.py:245`

**This IS the M9 certificate key.** It is the production per-TU semantic identity
computed by `build_certificate` (`certify.py:918-932`) and stored as
`cert.tusemantic_key` / `cert.key_result.root`. It captures every frontend input
the safety contract names (see §2, §3).

**M9 should gate cross-Build frontend reuse on `key_result.root` equality (plus
`is_reusable` and `closure_complete`).** The `nonce` is per-world and participates
ONLY in poison digests (`keys.py:249-250`), so well-formed keys are
world-independent and directly comparable across builds.

### 6.2 `context_id` — `indexer/context_identity.py:compute_context_id`

The per-compile-action identity (ADR-001): `H(build_id, main_file, compile_dir,
canonical_argv, response_hashes, output_name)` truncated to 16 hex chars. Includes
`config_hash` (build-grouping) and `output_name` (transient).

**M9 should NOT use `context_id` as the reuse key** — it mixes grouping + transient
components. It remains the correct PER-CONTEXT row key (which `files.content_hash`
row to read), but not the cross-Build reuse predicate.

### 6.3 `config_hash` — `indexer/manifest.py:compute_config_hash`

The build-grouping identity (set-union dialect hash). **M9 must NOT use this for
cross-Build reuse** — it deliberately drops per-file identity.

### 6.4 `flags_hash` / `compute_tu_content_hash` — `indexer/config_hash.py`

Tier-2 staleness hashes (set-based/sorted). **M9 must NOT use these for cross-Build
reuse** — they destroy order and compose set-based components.

### 6.5 `manifest_entry_hash` — `indexer/manifest.py:compute_current_entry_hash`

Source + header content hash (sorted). Correct for within-build staleness;
**insufficient for cross-Build reuse** (sorted headers, generated-header trust rule).

### 6.6 `ncc.canonicalize` — `store_v2/ncc.py:145`

The frozen S2 canonicalizer. Produces `ncc_serial` (L1 leg of KB-C) and
`include_list` (ordered class-E entries for the A5′ attestation face). **M9 reuses
this via the KB-C root** — no separate integration needed.

### 6.7 `_certify_parser_identity` — `store_v2/certify.py:977`

Captures `libclang_id`, `capture_binary_id`, `builtin_digest`, `builtin_count`,
`target_triple`, `indexer_generation`. Produces `environment_digest` (L3 leg of
KB-C) and the salts. **M9 reuses this via the KB-C root.**

### 6.8 `discover_implicit_include_dirs` — `store_v2/certify.py:447`

Probes the compiler's implicit include chain (`clang -v -E`). Captured as
`dir_attestations` (Slice-4A), a SEPARATE attestation face NOT in the KB-C root
digest. M9 should verify whether implicit-dir changes are caught by the
`environment_digest` (builtin_digest) indirectly; if not, consider adding to L3.

### Recommendation

**M9 should build the cross-Build reuse predicate directly on
`cert.key_result.root` (the KB-C TUSemanticKey) and `cert.key_result.is_reusable`.**
This reuses the entire existing certification pipeline (`build_certificate` →
`compute_tu_semantic_key`) without reinventing any identity computation. The only
net-new work is:

1. The `skip_files`/`with_refs` canonical-extraction decision (§4.1).
2. The `-include-pch` verification (§4.2).
3. The reuse HIT/MISS protocol that consults the KB-C root instead of the weaker
   Tier-2 hashes.

---

## Appendix A: File reading manifest

| File | Lines read | Purpose |
|---|---|---|
| `indexer/symbols.py` | 1-200, 3480-3650, 3650-3826, 80-169 | `ExtractionResult` def (3484), `extract_all` (3651), `_get_index` singleton (91), `_discover_template_instances` |
| `indexer/runner.py` | 580-779 | `get_file_hashes_by_context`, `header_hash_cache` mechanism, manifest_lookup per-context |
| `indexer/config_hash.py` | 1-125 (full) | `_normalize_entry` (sorts args), `compute_flags_hash` (set-based), `compute_tu_content_hash` |
| `indexer/context_identity.py` | 1-288 (full) | `canonical_argv` (order-preserved), `defines_of_argv`, `dialect_hash`, `compute_context_id` |
| `indexer/manifest.py` | 1-1144, 1145-1522 | `resolve_headers`, `fold_headers`, `check_tu_staleness`, `compute_current_entry_hash`, `header_is_trusted`, `merge_header_records` |
| `indexer/_unit_processor.py` | 1-823 (full) | The seam: `_check_and_parse_unit` → `extract_all(return_tu=True)` → `parsed` flows to certify/materialize/store |
| `indexer/compile_commands.py` | 1-668 (full) | `CompilationUnit` dataclass (230-244), `_DROP_FLAGS`/`_DROP_PREFIXES`/`_DROP_WITH_ARG`, `normalize_args`, `_gcc_system_includes`, `_detect_language` |
| `indexer/ops.py` | 1-200, 306-585, 1549-1848 | `store_symbols_for_unit` (1549), `_build_filtered_file_content` (306), `_compute_content_hash` |
| `store_v2/m6_direct_materialization.py` | 1-293 (full) | `materialize_unit_into_m6` (48) — getattr-only reads |
| `store_v2/certify.py` | 1-1264 (key ranges) | `build_certificate` (753), `_closure_walk` (596), `_diagnose_unresolved_includes` (633), `_tu_path_spelling_sensitive` (663), `_certify_parser_identity` (977), `discover_implicit_include_dirs` (447), `_raw_argv` (368), `_collect_forced_inputs` (400), publish path (1152-1264) |
| `store_v2/keys.py` | 1-321 (full) | `CertifiedSemanticInputs` (123), `compute_tu_semantic_key` (245), `TUKeyResult` (208), poison/salt guards |
| `store_v2/ncc.py` | 1-240 | `canonicalize` (145), `JOIN` set (46), `DROP_WITH_ARG`/`DROP_TOKEN`/`DROP_GCC_WARN`, path de-tainting (`_canon_path` 59) |
| `store_v2/facts.py` | 1-299, 480-552 | `extract_state_facts` (114), `build_cold_payload` (493), `fact_set_digest` (541), `StateFacts` dataclass (39) |

---

## Appendix B: The seam (where M9 reuse intercepts)

From `_unit_processor.py`, the production hot path per TU:

1. `_check_and_parse_unit` (Phase 1, parallel) calls `extract_all(unit, ..., return_tu=True)`.
2. The returned `parsed` (ExtractionResult) flows to three consumers:
   - `certify_and_publish_for_unit(unit=unit, parsed=parsed, ...)` — certification
   - `materialize_unit_into_m6(store=_m6_store, parsed=parsed, ...)` — M6 facts
   - `store_symbols_for_unit(conn, unit, config_hash, ..., pre_parsed=parsed, ...)` — legacy DB

**The M9 reuse intercept point is step 1**: instead of calling `extract_all` blindly,
M9 checks the cert key (KB-C root) against a cross-Build reuse cache. On HIT, the
cached `parsed` (or cached `tu`) is reused; on MISS, `extract_all` runs and the
result is cached under its cert key. The three consumers in step 2 are unchanged
(they are read-only, per §5).
