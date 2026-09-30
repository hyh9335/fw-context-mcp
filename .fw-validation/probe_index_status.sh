#!/bin/bash
ps aux | grep "cli index" | grep -v grep | head -2
echo ---
ls -la ~/fwctx-v2-work/N1/index/*/index.db ~/fwctx-v2-work/Z1/index/*/index.db 2>/dev/null
