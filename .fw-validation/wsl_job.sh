#!/usr/bin/env bash
# V0 probe job — WSL environment facts (validation-only)
echo "== FWCTX V0 WSL PROBE =="
echo "uname: $(uname -a)"
echo "id: $(id)"
echo "os-release: $(head -5 /etc/os-release 2>/dev/null | tr '\n' ' ')"
echo "== toolchain =="
for t in python3 python3.11 clang clang-15 clang-14 gcc g++ cmake ninja make git ld; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "$t: $(command -v "$t") :: $($t --version 2>&1 | head -1)"
  else
    echo "$t: MISSING"
  fi
done
echo "== filesystem =="
mkdir -p ~/fwctx-bench/public
df -T "$HOME/fwctx-bench" | tail -1
echo "home: $HOME"
echo "== proxy env =="
env | grep -i proxy || echo "(no proxy vars in WSL env)"
echo "WSLENV: ${WSLENV:-<unset>}"
echo "DONE_MARKER_OK"
