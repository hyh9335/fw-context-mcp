#!/usr/bin/env bash
# V0 WSL toolchain supplement v2 — run as root via `wsl -u root` (no sudo password)
export DEBIAN_FRONTEND=noninteractive
echo "== apt-get update =="
apt-get update -qq 2>&1 | tail -2
echo "== apt-get install clang cmake ninja-build =="
apt-get install -y -qq clang cmake ninja-build 2>&1 | tail -4
echo "== verify =="
for t in clang clang-14 clang-15 cmake ninja; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "$t: $($t --version 2>&1 | head -1)"
  else
    echo "$t: STILL-MISSING"
  fi
done
echo "SETUP_JOB_DONE"
