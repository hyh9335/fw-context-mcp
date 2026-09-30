#!/bin/bash
# C1 work setup: CMSIS CM3 startup template + include + index + dump vector region for oracle
set -x
CS=~/fwctx-bench/public/cmsis_6
WORK=~/fwctx-v2-work/C1
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/C1
mkdir -p "$WORK/src" "$OUT"
STARTUP="$CS/CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3/startup_ARMCM3.c"
cp "$STARTUP" "$WORK/src/"
# oracle duty: dump the vector-table region for the controller's byte-level check
sed -n '55,120p' "$STARTUP" > "$OUT/startup_vector_region.txt"
# also the declarations head
sed -n '1,54p' "$STARTUP" > "$OUT/startup_head.txt"

python3 - "$WORK" "$CS" <<'EOF'
import json, sys, os
work, cs = sys.argv[1], sys.argv[2]
src = os.path.join(work, "src", "startup_ARMCM3.c")
cc = [{"directory": work, "file": src,
       "arguments": ["clang", f"-I{cs}/CMSIS/Core/Include", "-DCMSIS_DEV", "-c", src,
                     "-o", "startup.o"]}]
json.dump(cc, open(os.path.join(work, "compile_commands.json"), "w"), indent=1)
print("CC entries:", len(cc))
EOF

PY=~/fwctx-venv311/bin/python
IDX=~/fwctx-v2-work/C1/index
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli init --project "$WORK" --quick
$PY -m fw_context_mcp.cli index "$WORK/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings
echo "C1-INDEXED"
