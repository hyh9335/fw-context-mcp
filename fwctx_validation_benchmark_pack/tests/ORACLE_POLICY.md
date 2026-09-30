# Oracle policy

Priority:
1. Hand-authored exact expected.json for golden fixtures
2. Clang preprocessor / AST independently executed
3. Linker map / ELF / --gc-sections
4. Build-system truth: compile_commands, Kconfig/defconfig/generated config
5. Official project runtime test
6. Metamorphic invariant: incremental == clean full, repeated reindex idempotence
7. Cross-platform canonical parity

Never use fw-context output to generate its own expected result.

Examples:
- conditional truth -> `clang -E`, macro dump, AST
- override/template -> Clang AST
- KEEP/dead -> linker GC/map/ELF
- vector -> CMSIS documented slot table / ELF section
- build variants -> build-generated config + compile commands
- incremental correctness -> clean full rebuild is comparison oracle
