#!/bin/bash
echo "== clones =="
for d in ~/fwctx-bench/public/*/; do
  name=$(basename "$d")
  head=$(git -C "$d" rev-parse HEAD 2>/dev/null | head -c 12)
  echo "$name @ $head"
done
echo "== toolchain =="
for t in gcc g++ make cmake ninja clang python3 west; do
  if command -v $t >/dev/null 2>&1; then
    echo "$t: $($t --version 2>&1 | head -1)"
  else
    echo "$t: MISSING"
  fi
done
echo "== disk =="
df -h ~ | tail -1
