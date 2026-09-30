#!/bin/bash
echo "indexers: $(ps aux | grep 'cli index' | grep -v grep | wc -l)"
for c in Z3 N1 Z4; do
  db=$(ls ~/fwctx-v2-work/$c/index/*/index.db 2>/dev/null | head -1)
  if [ -n "$db" ]; then
    sz=$(stat -c %s "$db")
    echo "$c: $((sz / 1073741824))GB $(( (sz % 1073741824) / 1048576 ))MB"
  else
    echo "$c: no db"
  fi
done
