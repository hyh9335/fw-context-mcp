#!/bin/bash
# index Z1 + N1 (both compile DBs ready)
set -x
PY=~/fwctx-venv311/bin/python
PYP=~/fwctx-venv311/bin/python

for CASE in Z1 N1; do
  WORK=~/fwctx-v2-work/$CASE
  IDX=$WORK/index
  p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  rm -rf "$IDX"
  export FW_CONTEXT_INDEX_DIR="$IDX"
  cd "$WORK"
  $PY -m fw_context_mcp.cli init --project "$WORK" --quick 2>&1 | tail -1
  case $CASE in
    Z1) CC="$WORK/build/compile_commands.json" ;;
    N1) CC="$WORK/nuttx-src/build/compile_commands.json" ;;
  esac
  $PY -m fw_context_mcp.cli index "$CC" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -2
  p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  echo "$CASE-INDEXED"
done

$PYP /mnt/d/github/fw-context-mcp/.fw-validation/wsl_v2_z1.py
echo "Z1-QUERIES-DONE"
