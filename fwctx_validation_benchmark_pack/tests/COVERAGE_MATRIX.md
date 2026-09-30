# Required coverage matrix

| Feature | Golden | Public-real | Required oracle |
|---|---|---|---|
| compile action identity | G02 | Zephyr/NuttX/RIOT | compile_commands |
| shared-header affected contexts | G01/G13 | NuttX copied checkout | incremental == full |
| conditional provenance | G03 | Zephyr config pair | clang -E + build config |
| variant group/diff | G04 | Zephyr/NuttX pair | config + semantic differential |
| declaration-only override | G05 | optional C++ public sample | Clang AST |
| templates | G05 | optional public C++ | Clang AST |
| indirect callbacks | G06 | FreeRTOS | source/AST registration evidence |
| dead-code roots | G07 | FreeRTOS optional QEMU/linker | linker/map/ELF |
| Zephyr async | G08 | Zephyr workq | source + official runtime test |
| FreeRTOS async | G09 | FreeRTOS | source/API evidence |
| Cortex-M vector | G10 | CMSIS 6 | official vector mapping |
| RISC-V vector | G11 | RIOT/NuttX RISC-V source | ISA/source/ELF; never pointer-table assumption |
| raw/active/directives | G12 | Zephyr/NuttX configs | byte hash + preprocessor |
| scale/fanout | G13 | Zephyr/NuttX/RIOT | invariants + performance |
| real stdio | project smoke | all applicable | subprocess JSON-RPC |
| Windows/WSL parity | golden subset | public subset | canonical result parity |
