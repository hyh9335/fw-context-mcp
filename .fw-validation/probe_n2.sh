#!/bin/bash
echo "N2/index tree:"
find ~/fwctx-v2-work/N2/index -maxdepth 2 | head -10
echo "--- N2 nuttx-src config:"
ls ~/fwctx-v2-work/N2/nuttx-src/.fw-context/ 2>/dev/null
echo "--- N2 work-root config:"
ls ~/fwctx-v2-work/N2/.fw-context/ 2>/dev/null
