#!/bin/bash
PY=~/fwctx-venv311/bin/python
WORK=~/fwctx-v2-work/N1/nuttx-src
IDX=~/fwctx-v2-work/N1/index
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
echo "--- target mtime:"; stat -c "%y %n" "$WORK/libs/libc/stdio/lib_gets.c"
echo "--- full index output:"
$PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings 2>&1 | head -20
echo "--- rc=$?"
