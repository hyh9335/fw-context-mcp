#!/bin/bash
echo "== freertos-kernel layout =="
ls ~/fwctx-bench/public/freertos-kernel/*.c 2>/dev/null | head -20
echo "-- include dirs --"
ls -d ~/fwctx-bench/public/freertos-kernel/include ~/fwctx-bench/public/freertos-kernel/portable/* 2>/dev/null | head -10
echo "== cmsis_6 startup templates =="
find ~/fwctx-bench/public/cmsis_6 -iname "*startup*" -name "*.c" 2>/dev/null | head -10
find ~/fwctx-bench/public/cmsis_6 -iname "startup_*template*" 2>/dev/null | head -10
echo "== qemu/arm toolchain (Z4) =="
for t in qemu-system-arm arm-none-eabi-gcc; do
  command -v $t >/dev/null 2>&1 && echo "$t: $($t --version | head -1)" || echo "$t: MISSING"
done
