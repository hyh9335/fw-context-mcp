#!/usr/bin/env bash
for f in $(find ~/fwctx-v1-work/G04 -name 'compile_commands.json'); do
  echo "== $f"
  cat "$f"
  echo
done
