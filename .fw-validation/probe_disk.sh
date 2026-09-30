#!/bin/bash
df -h /home | tail -1
echo ---
ls -la ~/fwctx-v2-work/*/index/*/index.db 2>/dev/null | awk '{printf "%.1fGB %s\n", $5/1073741824, $9}'
