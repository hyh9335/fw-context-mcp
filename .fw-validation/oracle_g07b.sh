#!/usr/bin/env bash
# G07 oracle rigor check: prove dead_a/dead_b exist in main.o and are
# removed by the LINKER (not the compiler), with full gc-sections log.
W=~/fwctx-v1-work/G07/oracle
cd "$W"
{
  echo "== symbols in main.o (compiler emitted) =="
  nm main.o
  echo
  echo "== relink with full GC log =="
  clang -nostdlib -Wl,--gc-sections -Wl,--print-gc-sections -Wl,-T,linker.ld main.o -o app2.elf 2>&1
  echo
  echo "== symbols in app2.elf =="
  nm app2.elf
} > oracle_rigor.txt 2>&1
cat oracle_rigor.txt
