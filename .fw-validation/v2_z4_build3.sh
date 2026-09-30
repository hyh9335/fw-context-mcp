#!/bin/bash
# Z4 attempt 3: west workspace in the WORK COPY, only cmsis projects updated
set -x
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z4
WORK=~/fwctx-v2-work/Z4
export http_proxy=http://proxyjp.huawei.com:8080
export https_proxy=http://proxyjp.huawei.com:8080
export GIT_SSL_NO_VERIFY=1
export PATH="$HOME/fwctx-venv312/bin:$PATH"
cd "$WORK"
rm -rf .west
timeout 600 west init -l "$WORK/zephyr-src" > west_init.log 2>&1 \
  || { echo WEST-INIT-FAILED; tail -5 west_init.log; exit 2; }
grep -E '^\s+name: cmsis' "$WORK/zephyr-src/west.yml" | head -4
timeout 600 west update cmsis_6 cmsis > west_update.log 2>&1 \
  || { echo WEST-UPDATE-FAILED; tail -10 west_update.log; exit 3; }
echo WEST-OK
export ZEPHYR_BASE="$WORK/zephyr-src"
export ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb
export GNUARMEMB_TOOLCHAIN_PATH=/usr
cd "$WORK"
rm -rf build
cmake -B build -S "$WORK/zephyr-src/samples/hello_world" -GNinja -DBOARD=qemu_cortex_m3 \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake3.log 2>&1 \
  || { echo Z4-CMAKE3-FAILED; tail -20 cmake3.log; exit 4; }
ninja -C build > ninja.log 2>&1 || { echo Z4-NINJA-FAILED; tail -25 ninja.log; exit 5; }
echo "Z4-BUILD-OK"
CC=$(find build -name compile_commands.json | head -1)
~/fwctx-venv312/bin/python - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "Z4", "board": "qemu_cortex_m3", "toolchain": "gnuarmemb",
        "tu_count": len(cc), "cc_path": cc_path,
        "method": "work-copy zephyr + west workspace (cmsis_6+cmsis updated)"}
json.dump(prov, open(os.path.join(out, "z4_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/z4_compile_commands.json" 2>/dev/null || true
cp cmake3.log ninja.log "$OUT/" 2>/dev/null || true
cd build
timeout --signal=KILL 15 ninja run > "$OUT/qemu_run.log" 2>&1 &
QPID=$!
sleep 12
kill -9 $QPID 2>/dev/null
grep -m1 "Hello World" "$OUT/qemu_run.log" || echo NO-HELLO
echo "Z4-DONE"
