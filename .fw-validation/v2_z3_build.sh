#!/bin/bash
# Z3: zephyr tests/kernel/workq/work_queue native_sim build (host toolchain)
set -x
ZEP=~/fwctx-bench/public/zephyr
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z3
WORK=~/fwctx-v2-work/Z3
mkdir -p "$OUT" "$WORK"
export ZEPHYR_BASE="$ZEP"
export PATH="$HOME/fwctx-venv312/bin:$PATH"
cd "$WORK"
cmake -B build -S "$ZEP/tests/kernel/workq/work_queue" -GNinja -DBOARD=native_sim \
  -DZEPHYR_TOOLCHAIN_VARIANT=host -DCONFIG_ASSERT=y \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake.log 2>&1 \
  || { echo Z3-CMAKE-FAILED; tail -20 cmake.log; exit 2; }
ninja -C build > ninja.log 2>&1 || { echo Z3-NINJA-FAILED; tail -20 ninja.log; exit 3; }
echo "Z3-BUILD-OK"
CC=$(find build -name compile_commands.json | head -1)
~/fwctx-venv312/bin/python - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "Z3", "board": "native_sim", "tu_count": len(cc), "cc_path": cc_path}
json.dump(prov, open(os.path.join(out, "z3_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/z3_compile_commands.json" 2>/dev/null || true
cp cmake.log ninja.log "$OUT/" 2>/dev/null || true
echo "Z3-DONE"
