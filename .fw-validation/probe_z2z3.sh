#!/bin/bash
ls -la ~/fwctx-v2-work/Z2/index/*/index.db ~/fwctx-v2-work/Z3/index/*/index.db 2>/dev/null
echo ---
ps aux | grep "cli index" | grep -v grep | wc -l
