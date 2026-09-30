#!/bin/bash
# quick deps: libssl-dev (python ssl), kconfig-frontends (nuttx), dtc/gperf
export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a 2>&1 | tail -2
apt-get install -y -qq libssl-dev zlib1g-dev libffi-dev kconfig-frontends \
  device-tree-compiler gperf 2>&1 | tail -3
command -v kconfig-tweak && echo KCONFIG-OK || echo KCONFIG-STILL-MISSING
command -v dtc && echo DTC-OK
