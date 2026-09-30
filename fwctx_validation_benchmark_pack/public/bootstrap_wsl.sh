#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$HOME/fwctx-bench/public}"
mkdir -p "$ROOT"
cd "$ROOT"

clone_tag () {
  name="$1"; url="$2"; tag="$3"
  if [ ! -d "$name/.git" ]; then
    git clone --filter=blob:none "$url" "$name"
  fi
  git -C "$name" fetch --tags --force
  git -C "$name" checkout --detach "$tag"
  printf "%s %s %s\n" "$name" "$tag" "$(git -C "$name" rev-parse HEAD)"
}

clone_tag zephyr https://github.com/zephyrproject-rtos/zephyr.git v4.4.2
clone_tag freertos-kernel https://github.com/FreeRTOS/FreeRTOS-Kernel.git V11.3.1
clone_tag cmsis6 https://github.com/ARM-software/CMSIS_6.git v6.3.0

# RIOT release tag spelling is expected to be 2026.07. If unavailable, use the verified release commit.
if [ ! -d RIOT/.git ]; then git clone --filter=blob:none https://github.com/RIOT-OS/RIOT.git RIOT; fi
git -C RIOT fetch --tags --force
if git -C RIOT rev-parse -q --verify refs/tags/2026.07 >/dev/null; then
  git -C RIOT checkout --detach 2026.07
else
  git -C RIOT checkout --detach 50ae2eb
fi
printf "RIOT %s\n" "$(git -C RIOT rev-parse HEAD)"

# NuttX tag spelling can vary. Try the expected release tag and fail closed if not found.
if [ ! -d nuttx/.git ]; then git clone --filter=blob:none https://github.com/apache/nuttx.git nuttx; fi
if [ ! -d apps/.git ]; then git clone --filter=blob:none https://github.com/apache/nuttx-apps.git apps; fi
git -C nuttx fetch --tags --force
git -C apps fetch --tags --force
for t in nuttx-13.0.0 13.0.0; do
  if git -C nuttx rev-parse -q --verify "refs/tags/$t" >/dev/null; then
    git -C nuttx checkout --detach "$t"
    if git -C apps rev-parse -q --verify "refs/tags/$t" >/dev/null; then git -C apps checkout --detach "$t"; fi
    printf "nuttx %s %s\n" "$t" "$(git -C nuttx rev-parse HEAD)"
    printf "apps %s\n" "$(git -C apps rev-parse HEAD)"
    exit 0
  fi
done
echo "NuttX 13.0.0 tag not found; use official Apache 13.0.0 source archive and record SHA512." >&2
exit 2
