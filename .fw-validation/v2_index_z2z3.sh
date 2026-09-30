#!/bin/bash
# index Z2 + Z3 in fresh dirs (parallel-safe, distinct from Z1/N1)
set -x
PY=~/fwctx-venv311/bin/python
for CASE in Z2 Z3; do
  WORK=~/fwctx-v2-work/$CASE
  IDX=$WORK/index
  p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  rm -rf "$IDX"
  export FW_CONTEXT_INDEX_DIR="$IDX"
  cd "$WORK"
  $PY -m fw_context_mcp.cli init --project "$WORK" --quick 2>&1 | tail -1
  $PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -2
  p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  echo "$CASE-INDEXED"
done
