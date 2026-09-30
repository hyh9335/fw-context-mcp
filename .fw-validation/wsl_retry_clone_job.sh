#!/usr/bin/env bash
# V0 retry clone job — corporate proxy MITMs TLS intermittently; disable git ssl
# verification for public corpus acquisition (recorded in PUBLIC_LOCK.json).
set -u
PUB="$HOME/fwctx-bench/public"
RES="/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V0/public_clone_results.txt"
echo "[$(date -Is)] retry clone job start"

gclone() { git -c http.sslVerify=false "$@"; }

clone_tag() {
  local id="$1" url="$2" ref="$3" dest="$4"
  echo "== retry $id: clone $ref =="
  if [ -d "$dest/.git" ] && [ -n "$(git -C "$dest" rev-parse HEAD 2>/dev/null)" ]; then
    local sha0 lic0
    sha0=$(git -C "$dest" rev-parse HEAD)
    lic0=$(ls "$dest" 2>/dev/null | grep -im1 -E '^licen')
    echo "RESULT|$id|$url|$ref|$sha0|${lic0:-NONE}|OK"
    return 0
  fi
  rm -rf "$dest"
  if ! gclone clone --depth 1 --branch "$ref" "$url" "$dest" >/dev/null 2>&1; then
    echo "RESULT|$id|$url|$ref|||TAG_CLONE_FAILED"
    return 1
  fi
  local sha lic
  sha=$(git -C "$dest" rev-parse HEAD 2>/dev/null)
  lic=$(ls "$dest" 2>/dev/null | grep -im1 -E '^licen')
  echo "RESULT|$id|$url|$ref|$sha|${lic:-NONE}|OK"
}

clone_default() {
  local id="$1" url="$2" dest="$3"
  if [ -d "$dest/.git" ] && [ -n "$(git -C "$dest" rev-parse HEAD 2>/dev/null)" ]; then return 0; fi
  rm -rf "$dest"
  if ! gclone clone --depth 1 "$url" "$dest" >/dev/null 2>&1; then
    echo "RESULT|$id|$url|default|||DEFAULT_CLONE_FAILED"
    return 1
  fi
  return 0
}

# NuttX tag existence probe (for the record)
if timeout 90 gclone ls-remote https://github.com/apache/nuttx.git refs/tags/nuttx-13.0.0 >/dev/null 2>&1; then
  echo "NUTTX_TAG_EXISTS refs/tags/nuttx-13.0.0"
else
  echo "NUTTX_TAG_MISSING_OR_NETWORK refs/tags/nuttx-13.0.0"
fi

clone_tag PUB-NUTTX https://github.com/apache/nuttx.git nuttx-13.0.0 "$PUB/nuttx" \
  || clone_default PUB-NUTTX https://github.com/apache/nuttx.git "$PUB/nuttx"
clone_tag PUB-NUTTX-APPS https://github.com/apache/nuttx-apps.git nuttx-13.0.0 "$PUB/nuttx-apps" \
  || clone_default PUB-NUTTX-APPS https://github.com/apache/nuttx-apps.git "$PUB/nuttx-apps"
clone_tag PUB-FREERTOS https://github.com/FreeRTOS/FreeRTOS-Kernel.git V11.3.1 "$PUB/freertos-kernel"
clone_tag PUB-CMSIS https://github.com/ARM-software/CMSIS_6.git v6.3.0 "$PUB/cmsis_6"

if ! clone_tag PUB-RIOT https://github.com/RIOT-OS/RIOT.git 2026.07 "$PUB/riot"; then
  echo "== RIOT tag missing, fallback commit 50ae2eb =="
  if clone_default PUB-RIOT https://github.com/RIOT-OS/RIOT.git "$PUB/riot"; then
    if gclone -C "$PUB/riot" fetch --depth 1 origin 50ae2eb >/dev/null 2>&1 \
       && git -C "$PUB/riot" checkout --detach FETCH_HEAD >/dev/null 2>&1; then
      sha=$(git -C "$PUB/riot" rev-parse HEAD)
      lic=$(ls "$PUB/riot" | grep -im1 -E '^licen')
      echo "RESULT|PUB-RIOT|https://github.com/RIOT-OS/RIOT.git|fallback:50ae2eb|$sha|${lic:-NONE}|OK_FALLBACK_COMMIT"
    fi
  fi
fi

echo "[$(date -Is)] retry clone job end"
echo "RETRY_CLONE_JOB_DONE"
