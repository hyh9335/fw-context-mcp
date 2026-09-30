#!/bin/bash
ls -la ~/fwctx-v2-work/N2/nuttx-src/build/ 2>/dev/null | head -12
echo "--- CC anywhere:"
find ~/fwctx-v2-work/N2 -name "compile_commands.json" 2>/dev/null
echo "--- artifacts copy:"
ls -la /mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N2/*.json 2>/dev/null | head -4
