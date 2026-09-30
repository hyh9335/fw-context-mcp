#!/bin/bash
# Z1 v2: python3.12 venv + west + zephyr build (native_sim, plain cmake attempt first)
set -x
ZEP=~/fwctx-bench/public/zephyr
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/Z1
WORK=~/fwctx-v2-work/Z1
mkdir -p "$OUT" "$WORK"

export http_proxy=http://proxyjp.huawei.com:8080
export https_proxy=http://proxyjp.huawei.com:8080
/opt/py312/bin/python3.12 -m venv ~/fwctx-venv312
PIP=~/fwctx-venv312/bin/pip
$PIP install -q --upgrade pip 2>&1 | tail -1
$PIP install -q -i https://repo.huaweicloud.com/repository/pypi/simple \
  --trusted-host repo.huaweicloud.com west pyelftools pykwalify 2>&1 | tail -2
~/fwctx-venv312/bin/west --version || echo WEST-MISSING

export ZEPHYR_BASE="$ZEP"
export PATH="$HOME/fwctx-venv312/bin:$PATH"

cd "$WORK"
if cmake -B build -S "$ZEP/samples/hello_world" -GNinja -DBOARD=native_sim -DZEPHYR_TOOLCHAIN_VARIANT=host \
     -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake1.log 2>&1; then
  echo "PLAIN-CMAKE-OK"
else
  echo "PLAIN-CMAKE-FAILED"
  tail -20 cmake1.log
  cd "$WORK"
  ~/fwctx-venv312/bin/west init -l "$ZEP" > west_init.log 2>&1 || tail -5 west_init.log
  cd "$WORK"
  ~/fwctx-venv312/bin/west update -o "-c http.sslVerify=false" > west_update.log 2>&1 \
    || tail -10 west_update.log
  cd "$WORK"
  if cmake -B build -S "$ZEP/samples/hello_world" -GNinja -DBOARD=native_sim -DZEPHYR_TOOLCHAIN_VARIANT=host \
       -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > cmake2.log 2>&1; then
    echo "WEST-CMAKE-OK"
  else
    echo "WEST-CMAKE-FAILED"
    tail -30 cmake2.log
    exit 2
  fi
fi

ninja -C build > ninja.log 2>&1 || { echo NINJA-FAILED; tail -20 ninja.log; exit 3; }
echo "BUILD-OK"
CC=$(find build -name compile_commands.json | head -1)
echo "CC=$CC"
~/fwctx-venv312/bin/python - "$CC" "$OUT" <<'EOF'
import json, sys, os
cc_path, out = sys.argv[1], sys.argv[2]
cc = json.load(open(cc_path))
prov = {"scenario": "Z1", "board": "native_sim", "tu_count": len(cc), "cc_path": cc_path,
        "representative_flags": cc[0]["command"][:400] if cc else None}
json.dump(prov, open(os.path.join(out, "z1_provenance.json"), "w"), indent=1)
print("TU count:", len(cc))
EOF
cp "$CC" "$OUT/zephyr_compile_commands.json" 2>/dev/null || true
cp cmake*.log ninja.log "$OUT/" 2>/dev/null || true
echo "Z1-DONE"

