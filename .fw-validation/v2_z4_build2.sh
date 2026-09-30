#!/bin/bash
# Z4 attempt 2: work-copy zephyr + inject CMSIS_6 Core headers into the module glue dir
set -x
ZEP=~/fwctx-bench/public/zephyr
CS=~/fwctx-bench/public/cmsis_6
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z4
WORK=~/fwctx-v2-work/Z4
mkdir -p "$OUT" "$WORK"

# what does the module CMake actually include?
grep -n "Include\|CMSIS" "$ZEP/modules/cmsis_6/CMakeLists.txt" | head -10
echo ---
cp -a "$ZEP" "$WORK/zephyr-src" 2>/dev/null
mkdir -p "$WORK/zephyr-src/modules/cmsis_6/CMSIS"
cp -a "$CS/CMSIS/Core" "$WORK/zephyr-src/modules/cmsis_6/CMSIS/"
ls "$WORK/zephyr-src/modules/cmsis_6/CMSIS/Core/Include" | head -4

export ZEPHYR_BASE="$WORK/zephyr-src"
export PATH="$HOME/fwctx-venv312/bin:$PATH"
export ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb
export GNUARMEMB_TOOLCHAIN_PATH=/usr
cd "$WORK"
rm -rf build
cmake -B build -S "$WORK/zephyr-src/samples/hello_world" -GNinja -DBOARD=qemu_cortex_m3 \
  -DZEPHYR_EXTRA_MODULES="$WORK/zephyr-src/modules/cmsis_6" \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake2.log 2>&1 \
  || { echo Z4-CMAKE2-FAILED; tail -20 cmake2.log; exit 2; }
ninja -C build > ninja.log 2>&1 || { echo Z4-NINJA-FAILED; tail -25 ninja.log; exit 3; }
echo "Z4-BUILD-OK"
CC=$(find build -name compile_commands.json | head -1)
~/fwctx-venv312/bin/python - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "Z4", "board": "qemu_cortex_m3", "toolchain": "gnuarmemb",
        "tu_count": len(cc), "cc_path": cc_path,
        "method": "work-copy zephyr + CMSIS_6 headers injected into module glue dir"}
json.dump(prov, open(os.path.join(out, "z4_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/z4_compile_commands.json" 2>/dev/null || true
cp cmake2.log ninja.log "$OUT/" 2>/dev/null || true

cd build
timeout --signal=KILL 15 ninja run > "$OUT/qemu_run.log" 2>&1 &
QPID=$!
sleep 12
kill -9 $QPID 2>/dev/null
grep -m1 "Hello World" "$OUT/qemu_run.log" || echo NO-HELLO
echo "Z4-DONE"
