#!/bin/bash
echo "== portable/GCC/Posix =="
ls ~/fwctx-bench/public/freertos-kernel/portable/GCC/Posix/ 2>/dev/null | head
echo "== ThirdParty/GCC/Posix =="
ls ~/fwctx-bench/public/freertos-kernel/portable/ThirdParty/GCC/Posix/ 2>/dev/null | head
echo "== CM3 startup vector symbols =="
grep -n "Vectors\|__initial_sp\|Reset_Handler" \
  ~/fwctx-bench/public/cmsis_6/CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3/startup_ARMCM3.c 2>/dev/null | head -12
echo "== CM3 startup size =="
wc -l ~/fwctx-bench/public/cmsis_6/CMSIS/CoreValidation/Layer/Target/CM3/RTE/Device/ARMCM3/startup_ARMCM3.c
