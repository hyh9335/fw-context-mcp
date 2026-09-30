#!/bin/bash
# N1 v3: clean single-copy checkout + cmake-only flow
set -x
NX=~/fwctx-bench/public/nuttx
APPS=~/fwctx-bench/public/nuttx-apps
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N1
WORK=~/fwctx-v2-work/N1
rm -rf "$WORK"
mkdir -p "$WORK" "$OUT"
cp -a "$NX" "$WORK/nuttx-src"
ln -s "$APPS" "$WORK/apps"
cd "$WORK/nuttx-src"
export PATH="$HOME/fwctx-venv312/bin:$PATH"

# cmake-only flow: no configure.sh; cmake runs kconfig itself
cmake -B build -S . -DBOARD_CONFIG=sim:nsh -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake.log 2>&1 \
  || { echo NUTTX-CMAKE-FAILED; tail -25 cmake.log; exit 3; }
make -C build -j4 > make.log 2>&1 \
  || { echo NUTTX-MAKE-FAILED; tail -25 make.log; exit 4; }
echo "NUTTX-BUILD-OK"
CC=build/compile_commands.json
ls -la "$CC" || { echo NO-CC; find . -name compile_commands.json | head -3; exit 5; }
python3 - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "N1", "board": "sim:nsh", "tu_count": len(cc), "cc_path": cc_path,
        "representative_flags": cc[0]["command"][:400] if cc else None}
json.dump(prov, open(os.path.join(out, "n1_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/nuttx_compile_commands.json" 2>/dev/null || true
cp cmake.log make.log "$OUT/" 2>/dev/null || true
echo "N1-DONE"
