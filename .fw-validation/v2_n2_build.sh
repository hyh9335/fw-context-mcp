#!/bin/bash
# N2: nuttx variant pair second config: sim:knsh (protected/kernel build)
set -x
NX=~/fwctx-bench/public/nuttx
APPS=~/fwctx-bench/public/nuttx-apps
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N2
WORK=~/fwctx-v2-work/N2
rm -rf "$WORK"
mkdir -p "$WORK" "$OUT"
cp -a "$NX" "$WORK/nuttx-src"
ln -s "$APPS" "$WORK/apps"
cd "$WORK/nuttx-src"
export PATH="$HOME/fwctx-venv312/bin:$PATH"
cmake -B build -S . -DBOARD_CONFIG=boards/sim/sim/sim/configs/smp \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake.log 2>&1 \
  || { echo N2-CMAKE-FAILED; tail -25 cmake.log; exit 3; }
make -C build -j4 > make.log 2>&1 \
  || { echo N2-MAKE-FAILED; tail -25 make.log; exit 4; }
echo "N2-BUILD-OK"
CC=build/compile_commands.json
python3 - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
# capture the config truth: .config diff evidence comes from the two build trees
cfg = {}
cfgp = ".config"
for line in open(cfgp, encoding="utf-8", errors="replace"):
    if line.startswith("CONFIG_") and "=" in line:
        k, _, v = line.strip().partition("=")
        cfg[k] = v
prov = {"scenario": "N2", "board": "sim:smp", "tu_count": len(cc), "cc_path": cc_path,
        "config_snapshot": cfg}
json.dump(prov, open(os.path.join(out, "n2_provenance.json"), "w"), indent=1)
print("TU count:", len(cc), "config keys:", len(cfg))
EOF
cp "$CC" "$OUT/n2_knsh_compile_commands.json" 2>/dev/null || true
cp cmake.log make.log "$OUT/" 2>/dev/null || true
echo "N2-DONE"
