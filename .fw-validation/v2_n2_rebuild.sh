#!/bin/bash
# N2 rebuild + index immediately (artifact CC copy preserved as evidence)
set -x
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N2
WORK=~/fwctx-v2-work/N2/nuttx-src
IDX=~/fwctx-v2-work/N2/index
cd "$WORK"
export PATH="$HOME/fwctx-venv312/bin:$PATH"
cmake -B build -S . -DBOARD_CONFIG=boards/sim/sim/sim/configs/smp \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake_rebuild.log 2>&1 \
  || { echo N2-CMAKE-FAILED; tail -15 cmake_rebuild.log; exit 3; }
make -C build -j4 > make_rebuild.log 2>&1 \
  || { echo N2-MAKE-FAILED; tail -15 make_rebuild.log; exit 4; }
ls -la build/compile_commands.json || { echo NO-CC-AGAIN; exit 5; }
cp build/compile_commands.json "$OUT/n2_smp_compile_commands.json"

PY=~/fwctx-venv311/bin/python
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
$PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -3
echo "index rc=$?"
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
ls "$IDX"/*/**/index.db "$IDX"/*/index.db 2>/dev/null | head -2
echo "N2-REBUILT-INDEXED"
