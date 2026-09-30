#!/bin/bash
df -h /home | tail -1
echo ---
du -sh ~/fwctx-v2-work/N1/index ~/fwctx-v2-work/N2/index 2>/dev/null
