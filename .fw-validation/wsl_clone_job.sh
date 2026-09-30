#!/usr/bin/env bash
# V0 public corpus clone job — pinned tags, shallow, SHA capture (validation-only)
set -u
PUB="$HOME/fwctx-bench/public"
RES="/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V0/public_clone_results.txt"
mkdir -p "$PUB"
: > "$RES"
echo "[$(date -Is)] clone job start"

# NOTE: proxy comes from env (http_proxy/https_proxy already exported in WSL).
# No global git config mutation — validation hygiene.

clone_tag() {
  local id="$1" url="$2" ref="$3" dest="$4"
  echo "== $id: clone $ref =="
  if [ -d "$dest/.git" ]; then
    echo "SKIP exists: $dest"
  else
    if ! git clone --depth 1 --branch "$ref" "$url" "$dest" >/dev/null 2>&1; then
      echo "RESULT|$id|$url|$ref|||TAG_CLONE_FAILED"
      return 1
    fi
  fi
  local sha lic
  sha=$(git -C "$dest" rev-parse HEAD 2>/dev/null)
  lic=$(ls "$dest" 2>/dev/null | grep -im1 -E '^licen')
  echo "RESULT|$id|$url|$ref|$sha|${lic:-NONE}|OK"
}

clone_default() {
  local id="$1" url="$2" dest="$3"
  if [ -d "$dest/.git" ]; then echo "SKIP exists: $dest"; return 0; fi
  if ! git clone --depth 1 "$url" "$dest" >/dev/null 2>&1; then
    echo "RESULT|$id|$url|default|||DEFAULT_CLONE_FAILED"
    return 1
  fi
  return 0
}

# 1) Zephyr v4.4.2
clone_tag PUB-ZEPHYR https://github.com/zephyrproject-rtos/zephyr.git v4.4.2 "$PUB/zephyr"

# 2) NuttX nuttx-13.0.0 (+ apps)
clone_tag PUB-NUTTX https://github.com/apache/nuttx.git nuttx-13.0.0 "$PUB/nuttx" \
  || clone_default PUB-NUTTX https://github.com/apache/nuttx.git "$PUB/nuttx"
clone_tag PUB-NUTTX-APPS https://github.com/apache/nuttx-apps.git nuttx-13.0.0 "$PUB/nuttx-apps" \
  || clone_default PUB-NUTTX-APPS https://github.com/apache/nuttx-apps.git "$PUB/nuttx-apps"

# 3) FreeRTOS-Kernel V11.3.1
clone_tag PUB-FREERTOS https://github.com/FreeRTOS/FreeRTOS-Kernel.git V11.3.1 "$PUB/freertos-kernel"

# 4) CMSIS 6 v6.3.0
clone_tag PUB-CMSIS https://github.com/ARM-software/CMSIS_6.git v6.3.0 "$PUB/cmsis_6"

# 5) RIOT 2026.07 with fallback commit 50ae2eb
if ! clone_tag PUB-RIOT https://github.com/RIOT-OS/RIOT.git 2026.07 "$PUB/riot"; then
  echo "== RIOT tag missing, fallback commit 50ae2eb =="
  if clone_default PUB-RIOT https://github.com/RIOT-OS/RIOT.git "$PUB/riot"; then
    if git -C "$PUB/riot" fetch --depth 1 origin 50ae2eb >/dev/null 2>&1 \
       && git -C "$PUB/riot" checkout --detach FETCH_HEAD >/dev/null 2>&1; then
      sha=$(git -C "$PUB/riot" rev-parse HEAD)
      lic=$(ls "$PUB/riot" | grep -im1 -E '^licen')
      echo "RESULT|PUB-RIOT|https://github.com/RIOT-OS/RIOT.git|fallback:50ae2eb|$sha|${lic:-NONE}|OK_FALLBACK_COMMIT"
    fi
  fi
fi

echo "[$(date -Is)] clone job end"
echo "CLONE_JOB_DONE"
