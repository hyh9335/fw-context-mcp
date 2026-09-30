#!/bin/bash
# R1 fix: compile-commands must run from an application directory
set -x
RT=~/fwctx-bench/public/riot
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/R1
WORK=~/fwctx-v2-work/R1
mkdir -p "$OUT" "$WORK"
rm -rf "$WORK/riot-src"
cp -a "$RT" "$WORK/riot-src"
cd "$WORK/riot-src/examples/basic/default"
make compile-commands BOARD=native > "$OUT/make_cc.log" 2>&1 \
  || { echo RIOT-CC-FAILED; tail -25 "$OUT/make_cc.log"; exit 2; }
CC="$WORK/riot-src/compile_commands.json"
ls -la "$CC" || { echo NO-CC-GENERATED; exit 3; }
make -j4 BOARD=native all > "$OUT/make_build.log" 2>&1 \
  || { echo RIOT-BUILD-FAILED; tail -25 "$OUT/make_build.log"; exit 4; }
echo "RIOT-BUILD-OK"
python3 - "$OUT" "$CC" <<'EOF'
import json, sys, os
out, cc_path = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "R1", "board": "native", "tu_count": len(cc),
        "cc_path": cc_path,
        "representative_flags": cc[0]["command"][:400] if cc else None}
json.dump(prov, open(os.path.join(out, "r1_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/riot_compile_commands.json"
echo "R1-DONE"
