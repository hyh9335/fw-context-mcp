#!/bin/bash
# R2: holdout variant — same R1 checkout, gnrc module set dropped, SAME index dir reused
# Tests: context/variant separation + no stale carry-over across compile-DB regeneration
set -x
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/R2
WORK=~/fwctx-v2-work/R1/riot-src
APPDIR=$WORK/examples/basic/default
IDX=~/fwctx-v2-work/R1/index
mkdir -p "$OUT"
PY=~/fwctx-venv311/bin/python

cd "$APPDIR"
# kill the daemon BEFORE mutating the tree/index inputs
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
make compile-commands BOARD=native USEMODULE+=saul USEMODULE+=periph_uart > "$OUT/make_cc_r2.log" 2>&1 \
  || { echo R2-CC-FAILED; tail -20 "$OUT/make_cc_r2.log"; exit 2; }
CC=$WORK/compile_commands.json
ls -la "$CC" || { echo NO-CC; exit 3; }
cp "$CC" "$OUT/r2_compile_commands.json"
python3 - "$OUT" "$CC" <<'EOF'
import json, sys, os
out, cc_path = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "R2", "board": "native+USEMODULE=saul,periph_uart", "tu_count": len(cc),
        "cc_path": cc_path}
json.dump(prov, open(os.path.join(out, "r2_provenance.json"), "w"), indent=1)
print("R2 TU count:", len(cc))
EOF

# reindex the SAME index dir against the new compile DB (product must detect + converge)
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli index "$CC" --project "$WORK" --no-analyze --no-embeddings 2>&1 | tail -3
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
echo "R2-REINDEXED"
