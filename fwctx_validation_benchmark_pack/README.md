# fw-context-mcp Validation Benchmark Pack

Purpose: validate `fw-context-mcp` on public, non-confidential firmware code and on deterministic
golden fixtures with independent ground truth.

This pack is **validation-first**. Product source is read-only during this workflow.
If a defect is found, preserve the reproduction and report it; do not patch the product inside this run.

## Single entrypoint

Start only:

`ENTRYPOINT_VALIDATOR.md`

## Three validation layers

1. **Golden exact layer**
   - small deterministic C/C++/ASM/linker fixtures
   - exact expected answers and independent compiler/linker oracles
   - covers every semantic feature intentionally

2. **Public-real layer**
   - Zephyr v4.4.2
   - Apache NuttX 13.0.0
   - FreeRTOS-Kernel V11.3.1 (+ optional official FreeRTOS demo checkout)
   - CMSIS 6.3.0
   - RIOT 2026.07
   - optional Juliet/TACLEBench stress subsets
   - public repositories are cloned by the agent and the resolved commit SHA is recorded

3. **System layer**
   - Windows Native + WSL2
   - real stdio MCP
   - incremental/full equivalence
   - variant parity
   - scale/fanout
   - failure classification

## Important rule

A public project does NOT automatically provide exact semantic ground truth.
Public-real tests therefore use independent oracles:
- compiler preprocessing / AST
- linker map / ELF / --gc-sections
- build-system configuration
- project runtime tests
- metamorphic/differential invariants

Only the self-built golden layer contains hand-authored exact expected semantic answers.

## WSL

Primary Linux/public-project runs use the WSL Linux filesystem, e.g.

`~/fwctx-bench/...`

Do not use `/mnt/c/...` as the Linux correctness environment.
