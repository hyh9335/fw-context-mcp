#!/bin/bash
n=0
for f in $(find ~/fwctx-v1-work ~/fwctx-bench -name watcher.pid 2>/dev/null); do
  pid=$(cat "$f" 2>/dev/null)
  if [ -n "$pid" ]; then kill -9 "$pid" 2>/dev/null && n=$((n+1)); fi
done
echo "wsl daemons killed: $n"
