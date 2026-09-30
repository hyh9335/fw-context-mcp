#!/bin/bash
# C1 fix: compile with the device macro (ARMCM3) as the RTE target requires
set -x
CS=~/fwctx-bench/public/cmsis_6
WORK=~/fwctx-v2-work/C1
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/C1
RTE="$CS/CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3"
ls "$RTE"
python3 - "$WORK" "$CS" "$RTE" <<'EOF'
import json, sys, os
work, cs, rte = sys.argv[1], sys.argv[2], sys.argv[3]
src = os.path.join(work, "src", "startup_ARMCM3.c")
cc = [{"directory": work, "file": src,
       "arguments": ["clang", f"-I{cs}/CMSIS/Core/Include", f"-I{rte}",
                     "-DARMCM3", "-DCMSIS_DEV", "-c", src, "-o", "startup.o"]}]
json.dump(cc, open(os.path.join(work, "compile_commands.json"), "w"), indent=1)
print("CC entries:", len(cc))
EOF

PY=~/fwctx-venv311/bin/python
IDX=~/fwctx-v2-work/C1/index
kill_pid=$(cat "$IDX/watcher.pid" 2>/dev/null)
[ -n "$kill_pid" ] && kill -9 "$kill_pid" 2>/dev/null
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli init --project "$WORK" --quick
$PY -m fw_context_mcp.cli index "$WORK/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings
kill_pid=$(cat "$IDX/watcher.pid" 2>/dev/null)
[ -n "$kill_pid" ] && kill -9 "$kill_pid" 2>/dev/null
echo "C1-REINDEXED"
