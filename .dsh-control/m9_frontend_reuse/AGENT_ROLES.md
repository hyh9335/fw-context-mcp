# M9 AGENT ROLES

## Controller
Owns immutable Goal, stage machine, reviewer creation, hard gates, stop authority. Cannot move the finish line.

## Lead Engineer
Owns archaeology, implementation, instrumentation, tests, repairs, and in-scope performance work.

## Frontend Safety Skeptic
Find a Build pair that gets a certificate HIT while Clang semantics differ. Attack macros, includes, generated headers, pragmas, target/toolchain, builtins, repeated includes, path-sensitive builtins, resource-dir, forced includes, language mode.

## Certificate Dependency Auditor
Find missing frontend dependencies. Reject source-only/header-only identities, whole compile-command degeneration, and config_hash aliases disguised as reuse.

## Performance/Economics Auditor
Separate certificate cost, avoided parses, frontend time, semantic time, and total time. Reject hit-rate-only, storage-only, synthetic-only, or unfair warm/cold claims.

## Fresh Safety Verifier
Receives current HEAD, contracts, tests, corpora. Attempts to break correctness.

## Fresh Guardian
Checks Goal alignment, zero false reuse, workload-conditional behavior, and whether complexity is justified.
