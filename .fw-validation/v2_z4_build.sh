#!/bin/bash
# Z4: qemu_cortex_m3 with GNU Arm Embedded toolchain + QEMU smoke run
set -x
ZEP=~/fwctx-bench/public/zephyr
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z4
WORK=~/fwctx-v2-work/Z4
mkdir -p "$OUT" "$WORK"
export ZEPHYR_BASE="$ZEP"
export PATH="$HOME/fwctx-venv312/bin:$PATH"
export ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb
export GNUARMEMB_TOOLCHAIN_PATH=/usr
cd "$WORK"
cmake -B build -S "$ZEP/samples/hello_world" -GNinja -DBOARD=qemu_cortex_m3 \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake.log 2>&1 \
  || { echo Z4-CMAKE-FAILED; tail -25 cmake.log; exit 2; }
ninja -C build > ninja.log 2>&1 || { echo Z4-NINJA-FAILED; tail -25 ninja.log; exit 3; }
echo "Z4-BUILD-OK"
CC=$(find build -name compile_commands.json | head -1)
~/fwctx-venv312/bin/python - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "Z4", "board": "qemu_cortex_m3", "toolchain": "gnuarmemb",
        "tu_count": len(cc), "cc_path": cc_path}
json.dump(prov, open(os.path.join(out, "z4_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/z4_compile_commands.json" 2>/dev/null || true
cp cmake.log ninja.log "$OUT/" 2>/dev/null || true

# QEMU smoke: run 15s, capture output, expect Hello World
cd build
timeout --signal=KILL 15 ninja run > "$OUT/qemu_run.log" 2>&1 &
QPID=$!
sleep 12
kill -9 $QPID 2>/dev/null
grep -c "Hello World" "$OUT/qemu_run.log" || echo "no hello"
grep -m1 "Hello World" "$OUT/qemu_run.log" || true
echo "Z4-DONE"
