#!/usr/bin/env bash
# G07 linker-truth oracle (expected.json link_command_hint)
set -e
W=~/fwctx-v1-work/G07/oracle
rm -rf "$W"; mkdir -p "$W"
cp /mnt/d/github/fw-context-mcp/fwctx_validation_benchmark_pack/golden/G07_dead_keep/main.c "$W/"
cp /mnt/d/github/fw-context-mcp/fwctx_validation_benchmark_pack/golden/G07_dead_keep/linker.ld "$W/"
cd "$W"
clang -ffunction-sections -fdata-sections -c main.c -o main.o
clang -nostdlib -Wl,--gc-sections -Wl,--print-gc-sections -Wl,-T,linker.ld main.o -o app.elf 2> gc_sections.txt || true
{
  echo "== gc-sections output (removed sections) =="
  cat gc_sections.txt
  echo "== symbols in app.elf =="
  nm app.elf 2>/dev/null || llvm-nm app.elf 2>/dev/null || readelf -sW app.elf | head -40
} > oracle_result.txt 2>&1
cat oracle_result.txt
