#!/bin/bash
# index Z4 (qemu_cortex_m3 gnuarmemb) + queries
set -x
PY=~/fwctx-venv311/bin/python
WORK=~/fwctx-v2-work/Z4
IDX=$WORK/index
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli init --project "$WORK" --quick 2>&1 | tail -1
$PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -2
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
echo "Z4-INDEXED"
~/fwctx-venv311/bin/python /mnt/d/github/fw-context-mcp/.fw-validation/wsl_v2_z4.py
echo "Z4-QUERIES-DONE"
