#!/bin/bash
for d in ~/fwctx-v2-work/N1 ~/fwctx-v2-work/N2 ~/fwctx-v2-work/N3 ~/fwctx-v2-work/Z1 ~/fwctx-v2-work/Z2 ~/fwctx-v2-work/Z3 ~/fwctx-v2-work/Z4 ~/fwctx-v2-work/R1 ~/fwctx-v2-work/R2 ~/fwctx-v2-work/C1 ~/fwctx-v2-work/F1 ~/fwctx-v2-work/F2; do
  if [ -d "$d" ]; then
    echo "== $d"
    find "$d" -maxdepth 3 -name .git -type d 2>/dev/null | while read g; do
      repo=$(dirname "$g")
      echo "   $(basename "$repo") $(git -C "$repo" rev-parse HEAD 2>/dev/null | cut -c1-12) $(git -C "$repo" describe --tags --always 2>/dev/null)"
    done
  fi
done
