#!/bin/bash
# env repairs for V2 builds (root)
export DEBIAN_FRONTEND=noninteractive
echo "== apt update =="
apt-get update -qq 2>&1 | tail -2
echo "== kconfig-frontends =="
apt-get install -y -qq kconfig-frontends 2>&1 | tail -3
command -v kconfig-tweak && echo KCONFIG-OK || echo KCONFIG-STILL-MISSING
echo "== dtc gperf (zephyr/nuttx deps) =="
apt-get install -y -qq device-tree-compiler gperf 2>&1 | tail -2
command -v dtc && echo DTC-OK
echo "== qemu + arm gcc for Z4 =="
apt-get install -y -qq qemu-system-arm gcc-arm-none-eabi 2>&1 | tail -3
command -v qemu-system-arm >/dev/null && echo QEMU-OK || echo QEMU-MISSING
command -v arm-none-eabi-gcc >/dev/null && echo ARMGCC-OK || echo ARMGCC-MISSING
echo "== uv (for python 3.13) =="
if [ ! -x /root/.local/bin/uv ] && [ ! -x ~/.local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv_install.sh --max-time 60 \
    && sh /tmp/uv_install.sh >/dev/null 2>&1
fi
for u in /root/.local/bin/uv ~/.local/bin/uv; do
  [ -x "$u" ] && { "$u" --version; echo UV-OK; break; }
done
