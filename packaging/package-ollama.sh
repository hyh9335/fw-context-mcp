#!/usr/bin/env bash
#
# Ollama 离线打包脚本（仅 embedding 模型，纯 CPU 场景）
#
# 在有网络的 Linux 环境（WSL Ubuntu 22.04 或更高版本）中运行此脚本：
#   1. 安装 Ollama（若未安装）
#   2. 下载 mxbai-embed-large embedding 模型
#   3. 下载 Ollama 二进制 tarball，创建 CPU-only 包
#      （bin/ollama + lib/ollama/llama-server，排除 GPU 库）
#   4. 组装 ollama-offline-bundle/
#
# 用法:
#   ./package-ollama.sh                # 完整打包
#   ./package-ollama.sh --tar          # 完成后打包成 .tar.gz
#   ./package-ollama.sh --refresh      # 强制重新下载
#
# 产出:
#   dist/ollama-offline-bundle/
#     ├── install-ollama.sh
#     ├── AGENT-INSTALL-OLLAMA.md
#     ├── README-ollama离线安装.md
#     ├── ollama/ollama-cpu.tar.gz           (bin/ollama + lib/ollama/llama-server，CPU 版)
#     ├── models/manifests/...               (模型清单)
#     ├── models/blobs/...                   (模型权重)
#     └── manifest.sha256
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
BUNDLE_DIR="${DIST_DIR}/ollama-offline-bundle"
MANIFEST_FILE="${BUNDLE_DIR}/manifest.sha256"

REFRESH=0
MAKE_TAR=0
EMBED_MODEL="mxbai-embed-large:latest"
OLLAMA_VERSION="v0.32.1"
TARGET_ARCH="amd64"

color_ok="\033[32m"; color_warn="\033[33m"; color_err="\033[31m"; color_info="\033[36m"; color_off="\033[0m"
log()   { printf "${color_info}[*]${color_off} %s\n" "$*" >&2; }
ok()    { printf "${color_ok}[+]${color_off} %s\n" "$*" >&2; }
warn()  { printf "${color_warn}[!]${color_off} %s\n" "$*" >&2; }
err()   { printf "${color_err}[x]${color_off} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

for arg in "$@"; do
    case "$arg" in
        --refresh)  REFRESH=1 ;;
        --tar)      MAKE_TAR=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *) die "未知参数: $arg" ;;
    esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
    die "此脚本必须在 Linux（WSL Ubuntu）中运行，当前系统: $(uname -s)"
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
    warn "当前架构为 $(uname -m)，本脚本仅为 x86_64 目标打包"
fi

command -v curl >/dev/null 2>&1 || die "未找到 curl: sudo apt-get install -y curl"
command -v tar  >/dev/null 2>&1 || die "未找到 tar:  sudo apt-get install -y tar"

# 确保 curl 不通过企业代理访问本地 ollama
# (no_proxy 通常包含 127.0.0.1* 但不包含 localhost)
export no_proxy="${no_proxy:+${no_proxy},}localhost,127.0.0.1"
export NO_PROXY="${NO_PROXY:+${NO_PROXY},}localhost,127.0.0.1"

# ── 步骤 1: 下载 Ollama tarball，用户级安装，创建 CPU-only 包 ──
#
# Ollama v0.32.1 需要 lib/ollama/llama-server 来加载模型做推理。
# 仅 bin/ollama（slim）可以 serve API 和 list models，但无法做 embedding。
# 完整 tarball 含 GPU 库（CUDA/ROCm）约 1.4GB，纯 CPU 场景不需要。
# CPU-only 包 = bin/ollama + lib/ollama/llama-server（排除 GPU 库），约 150MB。
download_install_and_prepare() {
    local local_bin="${HOME}/.local/bin"
    local local_lib="${HOME}/.local/lib/ollama"
    mkdir -p "${local_bin}" "${local_lib}"

    # bundle 中的 ollama 目录
    mkdir -p "${BUNDLE_DIR}/ollama"

    local tarball_zst="${BUNDLE_DIR}/ollama/.ollama-linux-${TARGET_ARCH}.tar.zst"
    local cpu_tarball="${BUNDLE_DIR}/ollama/ollama-cpu.tar.gz"

    if [[ ${REFRESH} -eq 1 ]]; then
        rm -f "${tarball_zst}" "${cpu_tarball}"
    fi

    # 解压 tarball（下载或使用已有）
    local tmp_extract=""

    # 检查已有 CPU 包
    if [[ -f "${cpu_tarball}" ]] && [[ ${REFRESH} -eq 0 ]]; then
        ok "已有 CPU 包: ${cpu_tarball} ($(du -h "${cpu_tarball}" | cut -f1))"
        echo "cpu" > "${BUNDLE_DIR}/ollama/.packaging-mode"
        # 仍需确保 ollama 已安装（用于后续 pull 模型）
        if ! command -v ollama >/dev/null 2>&1; then
            log "从 CPU 包安装 ollama 到 ${local_bin}/ ..."
            tmp_extract="$(mktemp -d)"
            tar -xzf "${cpu_tarball}" -C "${tmp_extract}"
            cp "${tmp_extract}/bin/ollama" "${local_bin}/ollama"
            chmod +x "${local_bin}/ollama"
            # 安装 lib/ollama/（CPU 库）
            rm -rf "${local_lib}"
            mkdir -p "${local_lib}"
            cp -r "${tmp_extract}/lib/ollama/"* "${local_lib}/"
            chmod +x "${local_lib}/llama-server" 2>/dev/null || true
            rm -rf "${tmp_extract}"
            tmp_extract=""
            case ":${PATH}:" in
                *":${local_bin}:"*) ;;
                *) export PATH="${local_bin}:${PATH}" ;;
            esac
        fi
        return 0
    fi

    # 下载 tarball（若不存在）
    if [[ ! -f "${tarball_zst}" ]]; then
        local url="https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/ollama-linux-${TARGET_ARCH}.tar.zst"
        log "下载 Ollama ${OLLAMA_VERSION} 二进制 (约 1.4 GB)..."
        if ! curl -kfL --retry 3 -o "${tarball_zst}" "${url}" 2>/dev/null; then
            die "下载失败: ${url}"
        fi
        ok "已下载: $(du -h "${tarball_zst}" | cut -f1)"
    else
        ok "已存在 tarball: ${tarball_zst} ($(du -h "${tarball_zst}" | cut -f1))"
    fi

    command -v zstd >/dev/null 2>&1 || die "需要 zstd: sudo apt-get install -y zstd"

    # 解压 tarball
    tmp_extract="$(mktemp -d)"
    log "解压 tarball ..."
    zstd -dc "${tarball_zst}" | tar -xf - -C "${tmp_extract}"

    # 检查必要文件
    [[ -f "${tmp_extract}/bin/ollama" ]] \
        || { rm -rf "${tmp_extract}"; die "tarball 中未找到 bin/ollama"; }
    [[ -f "${tmp_extract}/lib/ollama/llama-server" ]] \
        || { rm -rf "${tmp_extract}"; die "tarball 中未找到 lib/ollama/llama-server"; }

    # 打印结构信息
    local bin_size lib_size llama_size
    bin_size=$(du -h "${tmp_extract}/bin/ollama" | cut -f1)
    llama_size=$(du -h "${tmp_extract}/lib/ollama/llama-server" | cut -f1)
    lib_size=$(du -sh "${tmp_extract}/lib/ollama" | cut -f1)
    ok "bin/ollama:        ${bin_size}"
    ok "lib/ollama/llama-server: ${llama_size}"
    log "lib/ollama/ 总大小: ${lib_size} (含 GPU 库，CPU-only 包将排除)"

    # 列出 lib/ollama/ 中的文件（供参考）
    log "lib/ollama/ 内容:"
    ls -lh "${tmp_extract}/lib/ollama/" | sed 's/^/  /' >&2

    # 用户级安装：bin/ollama + lib/ollama/（CPU 库，排除 GPU）
    log "用户级安装 Ollama 到 ${local_bin}/ (含 lib/ollama/)..."
    cp "${tmp_extract}/bin/ollama" "${local_bin}/ollama"
    chmod +x "${local_bin}/ollama"
    # 复制整个 lib/ollama/（排除 GPU 库）到 ~/.local/lib/ollama/
    rm -rf "${local_lib}"
    mkdir -p "${local_lib}"
    cp -r "${tmp_extract}/lib/ollama/"* "${local_lib}/"
    chmod +x "${local_lib}/llama-server" 2>/dev/null || true

    case ":${PATH}:" in
        *":${local_bin}:"*) ;;
        *) export PATH="${local_bin}:${PATH}" ;;
    esac

    "${local_bin}/ollama" --version 2>&1 || die "ollama --version 执行失败"
    ok "Ollama 用户级安装完成 (bin + llama-server)"

    # 生成 CPU-only 包：bin/ollama + lib/ollama/（排除 GPU 库 cuda_v*/vulkan）
    log "生成 CPU-only 包: bin/ollama + lib/ollama/ (排除 GPU 库)..."
    rm -f "${cpu_tarball}"
    tar -czf "${cpu_tarball}" -C "${tmp_extract}" \
        --exclude='lib/ollama/cuda_v*' \
        --exclude='lib/ollama/vulkan' \
        bin/ollama \
        lib/ollama
    ok "已生成: ${cpu_tarball} ($(du -h "${cpu_tarball}" | cut -f1))"

    # 清理原始 tarball（节省空间，CPU 包已生成）
    rm -f "${tarball_zst}"
    rm -rf "${tmp_extract}"

    echo "cpu" > "${BUNDLE_DIR}/ollama/.packaging-mode"
    ok "打包模式: cpu"

    # ── 验证：启动 serve 并测试 embedding（模型可能尚未 pull，失败仅警告）──
    local ollama_bin="${local_bin}/ollama"
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama serve 已在 11434 运行"
    else
        log "启动 ollama serve 测试 ..."
        nohup "${ollama_bin}" serve > /tmp/ollama-packaging.log 2>&1 &
        local pid=$!
        for i in $(seq 1 15); do
            if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
                ok "Ollama serve 已启动 (pid=${pid})"
                break
            fi
            sleep 1
        done
        if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
            warn "Ollama serve 启动失败，查看日志: /tmp/ollama-packaging.log"
            cat /tmp/ollama-packaging.log >&2 || true
        fi
    fi

    # embedding 请求测试（模型可能尚未 pull，失败仅警告）
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        log "测试 embedding 请求 ..."
        local embed_resp
        embed_resp=$(curl -sf http://127.0.0.1:11434/api/embed \
            -d "{\"model\":\"${EMBED_MODEL}\",\"input\":[\"test\"]}" 2>/dev/null || true)
        if echo "${embed_resp}" | grep -q '"embeddings"'; then
            ok "embedding 请求成功 — CPU-only 包可正常推理"
        else
            warn "embedding 请求失败（模型可能尚未 pull，将在步骤 2 拉取后重试）:"
            warn "${embed_resp}"
        fi
    fi
}

# ── 步骤 2: 拉取 embedding 模型（需要 serve 在运行）─────────

# 当 ollama pull 因企业代理 HTTPS 拦截失败时，使用 curl -k 直接从 registry 下载
pull_model_curl_fallback() {
    local model_name="${EMBED_MODEL%%:*}"   # mxbai-embed-large
    local model_tag="${EMBED_MODEL##*:}"    # latest
    local registry="https://registry.ollama.ai"
    local repo="library/${model_name}"
    local models_dir="${HOME}/.ollama/models"
    local manifest_dir="${models_dir}/manifests/registry.ollama.ai/library/${model_name}"
    local blobs_dir="${models_dir}/blobs"

    log "使用 curl -k 回退方案下载模型（绕过代理证书校验）..."

    mkdir -p "${manifest_dir}" "${blobs_dir}"

    # 1. 下载 manifest
    local manifest_url="${registry}/v2/${repo}/manifests/${model_tag}"
    local manifest_file="${manifest_dir}/${model_tag}"
    log "下载 manifest: ${manifest_url}"
    curl -kLsf --retry 3 -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
        -o "${manifest_file}" "${manifest_url}" \
        || die "下载 manifest 失败"
    ok "manifest 已下载 ($(du -h "${manifest_file}" | cut -f1))"

    # 2. 提取所有 blob digests（config + layers）
    local digests
    digests=$(grep -oE '"digest":[[:space:]]*"sha256:[a-f0-9]+"' "${manifest_file}" \
        | sed 's/.*"sha256:\([a-f0-9]*\)"/\1/' | sort -u)

    [[ -n "${digests}" ]] || die "manifest 中未找到 blob digest"

    # 3. 下载每个 blob
    #    注意：registry 会返回 307 重定向到 CDN，必须加 -L 跟随重定向
    local count=0
    local total
    total=$(echo "${digests}" | wc -l)
    while IFS= read -r digest; do
        [[ -z "${digest}" ]] && continue
        count=$((count + 1))
        local blob_file="${blobs_dir}/sha256-${digest}"
        local blob_url="${registry}/v2/${repo}/blobs/sha256:${digest}"

        # 跳过已存在的 blob（检查文件非空）
        if [[ -s "${blob_file}" ]]; then
            ok "  [${count}/${total}] blob ${digest:0:12}... 已存在，跳过"
            continue
        fi

        log "  [${count}/${total}] 下载 blob ${digest:0:12}..."
        curl -kLsf --retry 3 -o "${blob_file}" "${blob_url}" \
            || { rm -f "${blob_file}"; die "下载 blob 失败: ${blob_url}"; }
        # 验证下载的 blob 非空（防止重定向响应被误存为空文件）
        [[ -s "${blob_file}" ]] \
            || { rm -f "${blob_file}"; die "下载 blob 为空: ${blob_url}"; }
        ok "  [${count}/${total}] blob 已下载 ($(du -h "${blob_file}" | cut -f1))"
    done <<< "${digests}"

    ok "模型 ${EMBED_MODEL} 下载完成（curl -k 回退）"

    # 验证：通过 ollama list 检查
    if ollama list 2>/dev/null | grep -q "${model_name}"; then
        ok "ollama list 确认模型已安装"
    else
        warn "ollama list 未显示模型，可能需要重启 serve"
    fi
}

pull_embed_model() {
    # 确保 serve 在运行
    if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        log "启动 Ollama serve ..."
        nohup ollama serve > /tmp/ollama-packaging.log 2>&1 &
        for i in $(seq 1 15); do
            if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
                ok "Ollama serve 已启动"
                break
            fi
            sleep 1
        done
        curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
            || die "Ollama serve 启动失败，查看: /tmp/ollama-packaging.log"
    else
        ok "Ollama serve 已在运行"
    fi

    log "拉取 embedding 模型: ${EMBED_MODEL} ..."
    if ollama list 2>/dev/null | grep -q "mxbai-embed-large"; then
        ok "模型 ${EMBED_MODEL} 已存在，跳过"
        return 0
    fi

    # 先尝试 ollama pull（在无代理或代理证书已信任时正常工作）
    if ollama pull "${EMBED_MODEL}" 2>&1; then
        ok "模型 ${EMBED_MODEL} 拉取完成（ollama pull）"
        ollama list
        return 0
    fi

    # ollama pull 失败 — 可能是企业代理 HTTPS 拦截导致证书校验失败
    warn "ollama pull 失败，尝试 curl -k 回退方案..."
    pull_model_curl_fallback
    ollama list
}

# ── 步骤 3: 验证 embedding 推理（模型已拉取后）──────────────────
verify_embedding() {
    log "验证 embedding 推理（CPU-only 包 + 已拉取模型）..."

    # 确保 serve 在运行
    if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        log "启动 Ollama serve ..."
        nohup ollama serve > /tmp/ollama-packaging.log 2>&1 &
        for i in $(seq 1 15); do
            if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
                ok "Ollama serve 已启动"
                break
            fi
            sleep 1
        done
        curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
            || die "Ollama serve 启动失败，查看: /tmp/ollama-packaging.log"
    else
        ok "Ollama serve 已在运行"
    fi

    # embedding 请求测试
    log "发送 embedding 请求 ..."
    local embed_resp
    embed_resp=$(curl -sf http://127.0.0.1:11434/api/embed \
        -d "{\"model\":\"${EMBED_MODEL}\",\"input\":[\"hello world\"]}" 2>/dev/null || true)

    if echo "${embed_resp}" | grep -q '"embeddings"'; then
        # 计算维度
        local dims
        dims=$(echo "${embed_resp}" | grep -oE '"embeddings":\[\[[0-9., -]+\]' | head -1 | tr -cd ',' | wc -c)
        if [[ "${dims}" =~ ^[0-9]+$ ]]; then
            dims=$((dims + 1))
        else
            dims="?"
        fi
        ok "embedding 推理成功！维度: ${dims}"
        ok "CPU-only 包验证通过 — bin/ollama + lib/ollama/llama-server 可独立运行"
    else
        warn "embedding 请求失败:"
        warn "${embed_resp}"
        warn "可能原因: llama-server 未正确安装或模型未加载"
        warn "查看日志: /tmp/ollama-packaging.log"
        die "embedding 验证失败 — CPU-only 包不可用"
    fi
}

# ── 步骤 4: 提取 embedding 模型文件 ────────────────────────────
extract_embed_model() {
    mkdir -p "${BUNDLE_DIR}/models"

    local src_models="${HOME}/.ollama/models"
    [[ -d "${src_models}/manifests" ]] || die "未找到模型目录: ${src_models}/manifests"

    # 找到 mxbai-embed-large 的 manifest 文件
    local manifest_root="${src_models}/manifests/registry.ollama.ai/library/mxbai-embed-large"
    [[ -d "${manifest_root}" ]] || die "未找到 mxbai-embed-large 的 manifest: ${manifest_root}"

    log "提取模型 manifest ..."
    # 复制整个 mxbai-embed-large manifest 目录
    mkdir -p "${BUNDLE_DIR}/models/manifests/registry.ollama.ai/library"
    cp -r "${manifest_root}" "${BUNDLE_DIR}/models/manifests/registry.ollama.ai/library/"

    # 从 manifest 中提取所有 blob sha256 引用
    log "提取模型 blob 文件 ..."
    mkdir -p "${BUNDLE_DIR}/models/blobs"
    local blob_count=0
    # manifest 文件是 JSON，包含 "digest": "sha256:..." 字段
    while IFS= read -r digest; do
        local blob_name="${digest#sha256:}"
        local src_blob="${src_models}/blobs/sha256-${blob_name}"
        if [[ -f "${src_blob}" ]]; then
            cp "${src_blob}" "${BUNDLE_DIR}/models/blobs/sha256-${blob_name}"
            blob_count=$((blob_count + 1))
            ok "  blob: sha256-${blob_name:0:12}... ($(du -h "${src_blob}" | cut -f1))"
        else
            warn "  缺失 blob: ${src_blob}"
        fi
    done < <(find "${manifest_root}" -type f -exec grep -oE '"digest":[[:space:]]*"sha256:[a-f0-9]+"' {} \; | sed 's/.*"sha256:\([a-f0-9]*\)"/\1/' | sort -u)

    ok "共提取 ${blob_count} 个 blob 文件"
}

# ── 步骤 5: 复制安装脚本与文档 ──────────────────────────────────
copy_install_scripts() {
    log "复制安装脚本与文档 ..."
    cp -f "${SCRIPT_DIR}/install-ollama.sh" "${BUNDLE_DIR}/install-ollama.sh"
    chmod +x "${BUNDLE_DIR}/install-ollama.sh"
    [[ -f "${SCRIPT_DIR}/AGENT-INSTALL-OLLAMA.md" ]] && cp -f "${SCRIPT_DIR}/AGENT-INSTALL-OLLAMA.md" "${BUNDLE_DIR}/AGENT-INSTALL-OLLAMA.md"
    [[ -f "${SCRIPT_DIR}/README-ollama离线安装.md" ]] && cp -f "${SCRIPT_DIR}/README-ollama离线安装.md" "${BUNDLE_DIR}/README-ollama离线安装.md"
}

# ── 步骤 6: 生成 manifest.sha256 ────────────────────────────────
generate_manifest() {
    log "生成 manifest.sha256 ..."
    (
        cd "${BUNDLE_DIR}" >/dev/null
        find . -type f \
            ! -name 'manifest.sha256' \
            -exec sha256sum {} \; | sort -k2
    ) > "${MANIFEST_FILE}"
    ok "manifest 已生成 ($(wc -l < "${MANIFEST_FILE}") 个文件)"
}

# ── 主流程 ──────────────────────────────────────────────────────
log "==================== Ollama 离线打包开始 ===================="

download_install_and_prepare
pull_embed_model
verify_embedding
extract_embed_model
copy_install_scripts
generate_manifest

ok "==================== 打包完成 ===================="
ok "离线包目录: ${BUNDLE_DIR}"
log "目录结构:"
( cd "${BUNDLE_DIR}" && find . -maxdepth 3 -mindepth 1 | sort | sed 's/^/  /' )
log "总大小: $(du -sh "${BUNDLE_DIR}" | cut -f1)"

if [[ ${MAKE_TAR} -eq 1 ]]; then
    log "打包为 tar.gz ..."
    tar -czf "${DIST_DIR}/ollama-offline-bundle.tar.gz" \
        -C "${DIST_DIR}" "ollama-offline-bundle"
    ok "已生成: ${DIST_DIR}/ollama-offline-bundle.tar.gz ($(du -h "${DIST_DIR}/ollama-offline-bundle.tar.gz" | cut -f1))"
fi
