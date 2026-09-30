#!/bin/bash
# R1: index the RIOT native compile DB + smoke queries
set -x
WORK=~/fwctx-v2-work/R1/riot-src
PY=~/fwctx-venv311/bin/python
IDX=~/fwctx-v2-work/R1/index
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/R1
mkdir -p "$OUT"
python3 - "$OUT" "$WORK" <<'EOF'
import json, sys, os
out, work = sys.argv[1], sys.argv[2]
cc = json.load(open(os.path.join(work, "compile_commands.json")))
prov = {"scenario": "R1", "board": "native", "tu_count": len(cc),
        "cc_path": os.path.join(work, "compile_commands.json"),
        "first_entry_keys": sorted(cc[0].keys()) if cc else None,
        "representative_command": (cc[0].get("command") or " ".join(cc[0].get("arguments", [])))[:400] if cc else None}
json.dump(prov, open(os.path.join(out, "r1_provenance.json"), "w"), indent=1)
print("TU count:", len(cc), "keys:", prov["first_entry_keys"])
EOF
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
rm -rf "$IDX"
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
$PY -m fw_context_mcp.cli init --project "$WORK" --quick
$PY -m fw_context_mcp.cli index "$WORK/compile_commands.json" --project "$WORK" --no-analyze --no-embeddings
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
echo "R1-INDEXED"
