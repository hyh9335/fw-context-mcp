#!/bin/bash
find ~/fwctx-v2-work/R1/riot-src -name compile_commands.json 2>/dev/null | head -3
echo "== last lines of make_cc.log =="
tail -5 /mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/R1/make_cc.log
echo "== build success check =="
ls ~/fwctx-v2-work/R1/riot-src/examples/basic/default/bin/native/ 2>/dev/null | head -5
