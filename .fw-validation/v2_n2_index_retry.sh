#!/bin/bash
PY=~/fwctx-venv311/bin/python
WORK=~/fwctx-v2-work/N2/nuttx-src
IDX=~/fwctx-v2-work/N2/index
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
ls -la build/compile_commands.json 2>&1 | head -2
$PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -6
echo "index rc=$?"
ls -la "$IDX" 2>&1 | head -4
