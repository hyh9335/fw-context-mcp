#!/usr/bin/env bash
#
# Ollama 离线安装脚本（用户级，无 sudo，仅 embedding 模型）
#
# 在 Ubuntu 20.04 (或更高版本) 离线服务器上运行此脚本，安装 Ollama + embedding 模型。
# 所有文件安装到用户家目录：~/.local/bin/ollama + ~/.ollama/models/
#
# 用法:
#   ./install-ollama.sh              # 安装
#   ./install-ollama.sh --verify     # 仅校验文件完整性
#   ./install-ollama.sh --uninstall  # 卸载
#   ./install-ollama.sh --help       # 帮助
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 安装位置（全部用户级，无 sudo）
LOCAL_BIN="${HOME}/.local/bin"
LOCAL_LIB="${HOME}/.local/lib/ollama"
OLLAMA_HOME="${HOME}/.ollama"
MODELS_DIR="${OLLAMA_HOME}/models"
MANIFEST_FILE="${SCRIPT_DIR}/manifest.sha256"

color_ok="\033[32m"; color_warn="\033[33m"; color_err="\033[31m"; color_info="\033[36m"; color_off="\033[0m"
log()   { printf "${color_info}[*]${color_off} %s\n" "$*" >&2; }
ok()    { printf "${color_ok}[+]${color_off} %s\n" "$*" >&2; }
warn()  { printf "${color_warn}[!]${color_off} %s\n" "$*" >&2; }
err()   { printf "${color_err}[x]${color_off} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

ACTION="install"
for arg in "$@"; do
    case "$arg" in
        --verify)    ACTION="verify" ;;
        --uninstall) ACTION="uninstall" ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *) die "未知参数: $arg (可用: --verify, --uninstall, --help)" ;;
    esac
done

# ── 前置检查 ────────────────────────────────────────────────────
preflight_checks() {
    log "前置检查 ..."

    # 操作系统
    if [[ "$(uname -s)" != "Linux" ]]; then
        die "此脚本仅支持 Linux，当前系统: $(uname -s)"
    fi
    ok "操作系统: Linux"

    # 架构
    local arch
    arch="$(uname -m)"
    if [[ "${arch}" != "x86_64" ]]; then
        die "此离线包仅为 x86_64 打包，当前架构: ${arch}"
    fi
    ok "架构: x86_64"

    # glibc 版本（Ollama 需要 glibc >= 2.31）
    local glibc_ver
    if glibc_ver=$(ldd --version 2>/dev/null | head -1 | awk '{print $NF}'); then
        local major minor
        major="${glibc_ver%%.*}"
        minor="${glibc_ver#*.}"; minor="${minor%%.*}"
        if [[ "${major}" -lt 2 ]] || { [[ "${major}" -eq 2 ]] && [[ "${minor}" -lt 31 ]]; }; then
            die "glibc 版本 ${glibc_ver} 低于 2.31 (Ubuntu 20.04)，Ollama 无法运行"
        fi
        ok "glibc 版本: ${glibc_ver} >= 2.31"
    else
        warn "无法检测 glibc 版本，继续安装"
    fi

    # 磁盘空间（至少 2GB）
    local avail_kb
    avail_kb=$(df -P "${HOME}" 2>/dev/null | awk 'NR==2 {print $4}')
    if [[ -n "${avail_kb}" ]] && [[ "${avail_kb}" -lt 2097152 ]]; then
        die "磁盘空间不足: 需要 >= 2GB，可用 $((avail_kb / 1024)) MB"
    fi
    ok "磁盘空间充足"

    # 家目录可写
    if [[ ! -w "${HOME}" ]]; then
        die "家目录 ${HOME} 不可写"
    fi
    ok "家目录可写: ${HOME}"

    # 必需命令
    command -v tar >/dev/null 2>&1 || die "缺少命令: tar (sudo apt-get install -y tar)"
    command -v sha256sum >/dev/null 2>&1 || die "缺少命令: sha256sum (sudo apt-get install -y coreutils)"
    command -v curl >/dev/null 2>&1 || die "缺少命令: curl (sudo apt-get install -y curl)"
    ok "必需命令齐全 (tar, sha256sum, curl)"

    # 确保 curl 不通过企业代理访问本地 ollama
    export no_proxy="${no_proxy:+${no_proxy},}localhost,127.0.0.1"
    export NO_PROXY="${NO_PROXY:+${NO_PROXY},}localhost,127.0.0.1"

    # CRLF 检查（防止 Windows 编辑器破坏脚本）
    if grep -rl $'\r' "${SCRIPT_DIR}/install-ollama.sh" >/dev/null 2>&1; then
        die "install-ollama.sh 包含 CRLF 行尾（Windows 格式）。请在 WSL/Linux 中运行: dos2unix install-ollama.sh"
    fi
    ok "脚本行尾检查通过（无 CRLF）"
}

# ── 完整性校验 ──────────────────────────────────────────────────
verify_bundle() {
    log "校验离线包文件完整性 (manifest.sha256) ..."
    if [[ ! -f "${MANIFEST_FILE}" ]]; then
        warn "未找到 manifest.sha256，跳过完整性校验"
        return 0
    fi
    local failed=0
    cd "${SCRIPT_DIR}"
    while read -r expected file; do
        [[ -z "${file}" ]] && continue
        if [[ ! -f "${file}" ]]; then
            err "缺失文件: ${file}"; failed=$((failed+1)); continue
        fi
        local actual
        actual="$(sha256sum "${file}" | awk '{print $1}')"
        if [[ "${expected}" != "${actual}" ]]; then
            err "校验失败: ${file} (期望 ${expected:0:12}..., 实际 ${actual:0:12}...)"
            failed=$((failed+1))
        fi
    done < "${MANIFEST_FILE}"
    cd - >/dev/null
    if [[ ${failed} -gt 0 ]]; then
        die "完整性校验失败 ${failed} 个文件，请重新拷贝离线包"
    fi
    ok "完整性校验通过"
}

# ── 安装 Ollama 二进制 ──────────────────────────────────────────
install_ollama_binary() {
    mkdir -p "${LOCAL_BIN}" "${LOCAL_LIB}"

    # 判断打包模式（cpu、slim 或 full）
    local packaging_mode="cpu"
    if [[ -f "${SCRIPT_DIR}/ollama/.packaging-mode" ]]; then
        packaging_mode=$(cat "${SCRIPT_DIR}/ollama/.packaging-mode")
    fi

    local ollama_dst="${LOCAL_BIN}/ollama"

    # ── 检查是否需要更新：通过 tarball hash 判断 ──
    # hash 一致且二进制存在 → 跳过安装，避免重复解压复制
    local tarball_path=""
    case "${packaging_mode}" in
        cpu)  tarball_path="${SCRIPT_DIR}/ollama/ollama-cpu.tar.gz" ;;
        slim) tarball_path="${SCRIPT_DIR}/ollama/ollama-bin.tar.gz" ;;
        full) tarball_path="${SCRIPT_DIR}/ollama/ollama-full.tar.gz" ;;
    esac

    local _new_tarball_hash=""
    if [[ -n "${tarball_path}" ]] && [[ -f "${tarball_path}" ]]; then
        local tarball_hash_file="${OLLAMA_HOME}/.tarball_hash"
        local tarball_new_hash
        tarball_new_hash="$(sha256sum "${tarball_path}" | cut -d' ' -f1)"
        _new_tarball_hash="${tarball_new_hash}"

        if [[ -f "${tarball_hash_file}" ]] \
           && [[ "$(cat "${tarball_hash_file}")" == "${tarball_new_hash}" ]] \
           && [[ -x "${ollama_dst}" ]]; then
            ok "Ollama 二进制未变化 (tarball hash 一致)，跳过安装"
            return 0
        fi
    fi

    case "${packaging_mode}" in
        cpu)
            local cpu_tarball="${SCRIPT_DIR}/ollama/ollama-cpu.tar.gz"
            [[ -f "${cpu_tarball}" ]] || die "未找到 ollama-cpu.tar.gz"

            log "安装 Ollama 二进制（CPU 版，bin/ollama + lib/ollama/）..."
            local tmp_extract
            tmp_extract="$(mktemp -d)"
            tar -xzf "${cpu_tarball}" -C "${tmp_extract}"

            [[ -f "${tmp_extract}/bin/ollama" ]] \
                || { rm -rf "${tmp_extract}"; die "tarball 中未找到 bin/ollama"; }
            [[ -f "${tmp_extract}/lib/ollama/llama-server" ]] \
                || { rm -rf "${tmp_extract}"; die "tarball 中未找到 lib/ollama/llama-server"; }

            rm -f "${ollama_dst}"
            cp "${tmp_extract}/bin/ollama" "${ollama_dst}"
            chmod +x "${ollama_dst}"

            # 安装整个 lib/ollama/ 目录（CPU 库 + llama-server + 共享库）
            rm -rf "${LOCAL_LIB}"
            mkdir -p "${LOCAL_LIB}"
            cp -r "${tmp_extract}/lib/ollama/"* "${LOCAL_LIB}/"
            chmod +x "${LOCAL_LIB}/llama-server" 2>/dev/null || true

            rm -rf "${tmp_extract}"
            ok "Ollama 二进制已安装: ${ollama_dst}"
            ok "lib/ollama/ 已安装: ${LOCAL_LIB}/ ($(find "${LOCAL_LIB}" -type f | wc -l) 个文件)"
            ;;

        slim)
            # 旧版瘦身包（仅 bin/ollama，无 llama-server，无法做推理）
            warn "检测到旧版 slim 包（仅 bin/ollama，无法做 embedding 推理）"
            warn "建议使用新版 cpu 包（含 llama-server）"
            local slim_tarball="${SCRIPT_DIR}/ollama/ollama-bin.tar.gz"
            [[ -f "${slim_tarball}" ]] || die "未找到 ollama-bin.tar.gz"

            log "安装 Ollama 二进制（瘦身版，仅 bin/ollama）..."
            local tmp_extract
            tmp_extract="$(mktemp -d)"
            tar -xzf "${slim_tarball}" -C "${tmp_extract}"

            [[ -f "${tmp_extract}/bin/ollama" ]] \
                || { rm -rf "${tmp_extract}"; die "tarball 中未找到 bin/ollama"; }

            rm -f "${ollama_dst}"
            cp "${tmp_extract}/bin/ollama" "${ollama_dst}"
            chmod +x "${ollama_dst}"
            rm -rf "${tmp_extract}"
            ok "Ollama 二进制已安装: ${ollama_dst} (slim — 推理不可用)"
            ;;

        full)
            local full_tarball="${SCRIPT_DIR}/ollama/ollama-full.tar.gz"
            [[ -f "${full_tarball}" ]] || die "未找到 ollama-full.tar.gz"

            log "安装 Ollama 二进制（完整版，含 lib/）..."
            # 完整版需要解压到 ~/.ollama/runtime/，然后用 LD_LIBRARY_PATH 运行
            local runtime_dir="${OLLAMA_HOME}/runtime"
            rm -rf "${runtime_dir}"
            mkdir -p "${runtime_dir}"
            tar -xzf "${full_tarball}" -C "${runtime_dir}"

            [[ -f "${runtime_dir}/bin/ollama" ]] || die "解压后未找到 bin/ollama"

            # 创建包装脚本，设置 LD_LIBRARY_PATH
            rm -f "${ollama_dst}"
            cat > "${ollama_dst}" <<'WRAPPER'
#!/usr/bin/env bash
# Ollama 启动包装脚本（设置 LD_LIBRARY_PATH 以加载完整版的 lib/）
export OLLAMA_RUNTIME_DIR="${HOME}/.ollama/runtime"
export LD_LIBRARY_PATH="${OLLAMA_RUNTIME_DIR}/lib/ollama:${LD_LIBRARY_PATH:-}"
exec "${OLLAMA_RUNTIME_DIR}/bin/ollama" "$@"
WRAPPER
            chmod +x "${ollama_dst}"
            ok "Ollama 二进制已安装: ${ollama_dst} (full — 含 GPU 库)"
            ;;

        *)
            die "未知打包模式: ${packaging_mode} (期望: cpu, slim, 或 full)"
            ;;
    esac

    # 安装成功后保存 tarball hash，供下次更新时跳过判断
    if [[ -n "${_new_tarball_hash}" ]]; then
        echo "${_new_tarball_hash}" > "${OLLAMA_HOME}/.tarball_hash"
    fi
}

# ── 安装模型文件 ────────────────────────────────────────────────
install_models() {
    log "安装 embedding 模型文件 ..."

    # ── 检查模型是否已安装：manifest 存在则跳过（避免复制 670MB） ──
    local model_manifest="${MODELS_DIR}/manifests/registry.ollama.ai/library/mxbai-embed-large/latest"
    if [[ -f "${model_manifest}" ]]; then
        ok "模型已安装 (manifest 存在)，跳过复制 (~670MB)"
        return 0
    fi

    mkdir -p "${MODELS_DIR}"

    # 复制 manifests
    if [[ -d "${SCRIPT_DIR}/models/manifests" ]]; then
        mkdir -p "${MODELS_DIR}/manifests"
        cp -r "${SCRIPT_DIR}/models/manifests/"* "${MODELS_DIR}/manifests/" 2>/dev/null || true
        ok "manifests 已复制"
    else
        die "未找到 models/manifests 目录"
    fi

    # 复制 blobs
    if [[ -d "${SCRIPT_DIR}/models/blobs" ]]; then
        mkdir -p "${MODELS_DIR}/blobs"
        cp -r "${SCRIPT_DIR}/models/blobs/"* "${MODELS_DIR}/blobs/" 2>/dev/null || true
        local blob_count
        blob_count=$(find "${MODELS_DIR}/blobs" -type f | wc -l)
        ok "blobs 已复制 (${blob_count} 个文件)"
    else
        die "未找到 models/blobs 目录"
    fi
}

# ── 验证安装 ────────────────────────────────────────────────────
verify_install() {
    log "验证安装 ..."

    local ollama_bin="${LOCAL_BIN}/ollama"
    [[ -x "${ollama_bin}" ]] || die "ollama 命令未安装成功: ${ollama_bin}"

    # 检查 llama-server（cpu 模式）
    local packaging_mode="cpu"
    [[ -f "${SCRIPT_DIR}/ollama/.packaging-mode" ]] && packaging_mode=$(cat "${SCRIPT_DIR}/ollama/.packaging-mode")
    if [[ "${packaging_mode}" == "cpu" ]]; then
        if [[ -x "${LOCAL_LIB}/llama-server" ]]; then
            local lib_count
            lib_count=$(find "${LOCAL_LIB}" -type f | wc -l)
            ok "lib/ollama/ 已安装: ${LOCAL_LIB} (${lib_count} 个文件)"
        else
            warn "llama-server 未找到: ${LOCAL_LIB}/llama-server (embedding 推理将不可用)"
        fi
    fi

    # 版本检查
    if "${ollama_bin}" --version >/dev/null 2>&1; then
        local ver
        ver=$("${ollama_bin}" --version 2>&1 | head -1)
        ok "ollama 版本: ${ver}"
    else
        die "ollama --version 执行失败，请检查 ${ollama_bin}"
    fi

    # 模型列表检查（需要 ollama serve 运行）
    log "启动临时 ollama serve 以验证模型 ..."
    # 先检查是否已有 serve 在运行
    local serve_was_running=0
    local our_pid=""
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        serve_was_running=1
        ok "Ollama serve 已在运行"
    else
        nohup "${ollama_bin}" serve > "${OLLAMA_HOME}/ollama-install-test.log" 2>&1 &
        our_pid=$!
        # 等待最多 15 秒
        for i in $(seq 1 15); do
            if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
                ok "Ollama serve 已启动 (pid=${our_pid})"
                break
            fi
            sleep 1
        done
        if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
            warn "Ollama serve 启动超时，查看日志: ${OLLAMA_HOME}/ollama-install-test.log"
            cat "${OLLAMA_HOME}/ollama-install-test.log" >&2 || true
            warn "跳过模型验证（serve 未启动）"
            return 0
        fi
    fi

    # 检查模型列表
    local model_list
    model_list=$(curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null || echo '{}')
    if echo "${model_list}" | grep -q "mxbai-embed-large"; then
        ok "embedding 模型 mxbai-embed-large:latest 已安装"
    else
        warn "模型列表中未找到 mxbai-embed-large，可能 manifest/blobs 不完整"
        warn "模型列表: ${model_list}"
    fi

    # embedding 请求测试
    log "测试 embedding 请求 ..."
    local embed_resp
    embed_resp=$(curl -sf http://127.0.0.1:11434/api/embed \
        -d '{"model":"mxbai-embed-large:latest","input":["hello world"]}' 2>/dev/null || true)
    if echo "${embed_resp}" | grep -q '"embeddings"'; then
        # 计算第一个 embedding 向量的维度（数逗号+1）
        local dims="?"
        dims=$(echo "${embed_resp}" | grep -oE '"embeddings":\[\[[0-9., -]+\]' | head -1 | tr -cd ',' | wc -c)
        if [[ "${dims}" =~ ^[0-9]+$ ]]; then
            dims=$((dims + 1))
        fi
        ok "embedding 请求成功 (维度: ${dims})"
    else
        warn "embedding 请求失败:"
        warn "${embed_resp}"
    fi

    # 如果是我们启动的 serve，则停止它（仅停止我们启动的进程，不影响其他）
    if [[ ${serve_was_running} -eq 0 ]] && [[ -n "${our_pid}" ]]; then
        kill "${our_pid}" 2>/dev/null || true
        # 等待进程退出
        for i in $(seq 1 5); do
            kill -0 "${our_pid}" 2>/dev/null || break
            sleep 1
        done
        kill -9 "${our_pid}" 2>/dev/null || true
        log "已停止临时 serve (pid=${our_pid})（安装测试用）"
    fi
}

# ── 卸载 ────────────────────────────────────────────────────────
uninstall_ollama() {
    log "卸载 Ollama ..."

    # 停止 serve 进程
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 1

    # 删除二进制
    if [[ -f "${LOCAL_BIN}/ollama" ]]; then
        rm -f "${LOCAL_BIN}/ollama"
        ok "已移除: ${LOCAL_BIN}/ollama"
    fi

    # 删除 lib/ollama/（cpu 模式）
    if [[ -d "${LOCAL_LIB}" ]]; then
        rm -rf "${LOCAL_LIB}"
        ok "已移除: ${LOCAL_LIB}"
    fi

    # 删除完整版 runtime（如有）
    if [[ -d "${OLLAMA_HOME}/runtime" ]]; then
        rm -rf "${OLLAMA_HOME}/runtime"
        ok "已移除: ${OLLAMA_HOME}/runtime"
    fi

    # 询问是否删除模型（默认保留）
    if [[ -d "${MODELS_DIR}" ]]; then
        log "模型文件位于: ${MODELS_DIR} ($(du -sh "${MODELS_DIR}" | cut -f1))"
        log "如需删除模型，请手动执行: rm -rf ${OLLAMA_HOME}"
        warn "模型文件已保留（可能较大）"
    fi

    ok "卸载完成（模型已保留，如需删除请手动 rm -rf ${OLLAMA_HOME}）"
}

# ── 打印后续提示 ────────────────────────────────────────────────
print_post_install_hint() {
    cat <<EOF

==================== Ollama 安装完成 ====================

Ollama 二进制位置: ${LOCAL_BIN}/ollama
模型存储位置:       ${OLLAMA_HOME}/models/

请确保 ${LOCAL_BIN} 在你的 PATH 中。如未设置，请将以下内容加入 ~/.bashrc：

    export PATH="${LOCAL_BIN}:\$PATH"

然后执行:  source ~/.bashrc

启动 Ollama 服务（后台）:

    nohup ollama serve > ~/.ollama/ollama.log 2>&1 &

验证服务:

    curl http://localhost:11434/api/tags

embedding 模型已预装: mxbai-embed-large:latest
（用于 fw-context-mcp 的 semantic_search 功能）

与 fw-context-mcp 配合:
  确保 ~/.fw-context/config.toml 中 [llm] 段：
    enabled = true
    ollama_url = "http://localhost:11434"
    embed_model = "mxbai-embed-large:latest"

详细文档请阅读: README-ollama离线安装.md

EOF
}

# ── 主流程 ──────────────────────────────────────────────────────
case "${ACTION}" in
    verify)
        preflight_checks
        verify_bundle
        ;;
    uninstall)
        uninstall_ollama
        ;;
    install)
        preflight_checks
        verify_bundle
        install_ollama_binary
        install_models
        verify_install
        print_post_install_hint
        ;;
esac
