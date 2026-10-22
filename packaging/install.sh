#!/usr/bin/env bash
#
# fw-context 离线安装脚本
#
# 在 Ubuntu 20.04 (或更高版本) 离线服务器上运行此脚本，安装 fw-context-mcp。
#
# 用法:
#   sudo ./install.sh                       # 系统级安装（推荐，需 root）
#   ./install.sh                            # 用户级安装（仅装到 ~/.fw-context）
#   ./install.sh --prefix /opt/fw-context   # 自定义安装前缀
#   ./install.sh --uninstall                # 卸载
#   ./install.sh --verify                   # 仅校验文件完整性
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 默认安装位置
PREFIX="/opt/fw-context"
VENV_DIR="${HOME}/.fw-context/.venv"
LOCAL_BIN="${HOME}/.local/bin"
PYTHON_SUBDIR="python3.11"
INSTALL_PYTHON=1

color_ok="\033[32m"; color_warn="\033[33m"; color_err="\033[31m"; color_info="\033[36m"; color_off="\033[0m"
# 所有状态消息输出到 stde
log()   { printf "${color_info}[*]${color_off} %s\n" "$*" >&2; }
ok()    { printf "${color_ok}[+]${color_off} %s\n" "$*" >&2; }
warn()  { printf "${color_warn}[!]${color_off} %s\n" "$*" >&2; }
err()   { printf "${color_err}[x]${color_off} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

ACTION="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall) ACTION="uninstall" ;;
        --verify)   ACTION="verify" ;;
        --prefix)   shift_next=1 ;;
        --prefix=*) PREFIX="${arg#--prefix=}" ;;
        --user)     PREFIX="${HOME}/.fw-context/python" ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *)
            if [[ "${shift_next:-0}" == "1" ]]; then
                PREFIX="$arg"; shift_next=0
            else
                die "未知参数: $arg"
            fi
            ;;
    esac
done

PYTHON_INSTALL_DIR="${PREFIX}/${PYTHON_SUBDIR}"
WHEELS_DIR="${SCRIPT_DIR}/wheels"
LOCK_FILE="${SCRIPT_DIR}/requirements.lock.txt"
REQ_FILE="${SCRIPT_DIR}/requirements.txt"
MANIFEST_FILE="${SCRIPT_DIR}/manifest.sha256"
PYTHON_TARBALL="${SCRIPT_DIR}/python/python-standalone.tar.gz"

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1  (Ubuntu 20.04: sudo apt-get install -y $2)"
}

verify_bundle() {
    log "校验离线包文件完整性 (manifest.sha256) ..."
    require_cmd sha256sum "coreutils"
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

uninstall_fw_context() {
    log "卸载 fw-context ..."
    for bin in fw-context fw-context-mcp fw-cache-server fw-cache-admin; do
        if [[ -L "${LOCAL_BIN}/${bin}" || -e "${LOCAL_BIN}/${bin}" ]]; then
            rm -f "${LOCAL_BIN}/${bin}"
            ok "已移除符号链接: ${LOCAL_BIN}/${bin}"
        fi
    done
    if [[ -d "${HOME}/.fw-context" ]]; then
        log "移除 ~/.fw-context (含 venv 与缓存数据) ..."
        rm -rf "${HOME}/.fw-context"
    fi
    if [[ -d "${PYTHON_INSTALL_DIR}" ]]; then
        log "移除 Python 安装目录 ${PYTHON_INSTALL_DIR} (需要 sudo) ..."
        sudo rm -rf "${PYTHON_INSTALL_DIR}" 2>/dev/null || warn "无法移除 ${PYTHON_INSTALL_DIR}，请手动删除"
    fi
    # 清理空目录
    [[ -d "${PREFIX}" ]] && rmdir --ignore-fail-on-non-empty "${PREFIX}" 2>/dev/null || true
    ok "卸载完成"
}

ensure_libc_compatible() {
    local glibc_ve
    if ! glibc_ver=$(ldd --version 2>/dev/null | head -1 | awk '{print $NF}'); then
        warn "无法检测 glibc 版本，跳过兼容性检查"
        return 0
    fi
    local major minor glibc_too_old=0
    major="${glibc_ver%%.*}"
    minor="${glibc_ver#*.}"; minor="${minor%%.*}"
    if [[ "${major}" -lt 2 ]]; then
        glibc_too_old=1
    elif [[ "${major}" -eq 2 && "${minor}" -lt 31 ]]; then
        glibc_too_old=1
    fi
    if [[ ${glibc_too_old} -eq 1 ]]; then
        warn "glibc 版本 ${glibc_ver} 低于 2.31 (Ubuntu 20.04)，可能存在兼容性问题"
    else
        ok "glibc 版本 ${glibc_ver} >= 2.31"
    fi
}

install_python() {
    if [[ ! -f "${PYTHON_TARBALL}" ]]; then
        warn "未找到 Python 独立构建 (${PYTHON_TARBALL})"
        if command -v python3.11 >/dev/null 2>&1; then
            ok "检测到系统已安装 python3.11，将直接使用"
            PYTHON_INSTALL_DIR=""
            return 0
        elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
            ok "系统 python3 满足版本要求 (>=3.11)"
            PYTHON_INSTALL_DIR=""
            return 0
        else
            die "既没有 Python 独立构建包，系统的 Python 也低于 3.11，无法安装"
        fi
    fi

    if [[ -x "${PYTHON_INSTALL_DIR}/bin/python3.11" ]]; then
        ok "Python 已安装于 ${PYTHON_INSTALL_DIR}，跳过"
        return 0
    fi

    log "安装 Python 3.11 独立构建到 ${PYTHON_INSTALL_DIR} ..."
    local real_tarball
    real_tarball="$(readlink -f "${PYTHON_TARBALL}")"
    # python-build-standalone 解压后目录名通常为 python/（install_only 变体）
    # 或含 install/ 子目录（full 变体）
    local tmp_extract
    tmp_extract="$(mktemp -d)"
    tar -xzf "${real_tarball}" -C "${tmp_extract}"
    local extracted_di
    extracted_dir="$(find "${tmp_extract}" -maxdepth 1 -mindepth 1 -type d | head -1)"
    [[ -n "${extracted_dir}" ]] || { rm -rf "${tmp_extract}"; die "解压 Python 失败"; }
    # full 变体: 实际 Python 安装在 install/ 子目录中
    if [[ -d "${extracted_dir}/install" && -x "${extracted_dir}/install/bin/python3.11" ]]; then
        extracted_dir="${extracted_dir}/install"
    fi
    # 验证 Python 可执行文件存在
    [[ -x "${extracted_dir}/bin/python3.11" ]] || { rm -rf "${tmp_extract}"; die "解压后未找到 python3.11 可执行文件"; }

    # 创建 PREFIX 目录（可能需要 sudo）
    if [[ ! -d "${PREFIX}" ]]; then
        if mkdir -p "${PREFIX}" 2>/dev/null; then
            : # 创建成功
        else
            log "需要 sudo 创建 ${PREFIX}"
            sudo mkdir -p "${PREFIX}"
            sudo chown "${USER}:${USER}" "${PREFIX}"
        fi
    fi

    # 移动解压内容到 PYTHON_INSTALL_DIR
    rm -rf "${PYTHON_INSTALL_DIR}"
    if [[ -w "${PREFIX}" ]]; then
        mv "${extracted_dir}" "${PYTHON_INSTALL_DIR}"
    else
        sudo mv "${extracted_dir}" "${PYTHON_INSTALL_DIR}"
        sudo chown -R "${USER}:${USER}" "${PYTHON_INSTALL_DIR}"
    fi
    rm -rf "${tmp_extract}"

    chmod +x "${PYTHON_INSTALL_DIR}/bin/"python* 2>/dev/null || true
    ok "Python 已安装: ${PYTHON_INSTALL_DIR}/bin/python3.11"
}

create_venv() {
    local py_bin
    if [[ -n "${PYTHON_INSTALL_DIR}" && -x "${PYTHON_INSTALL_DIR}/bin/python3.11" ]]; then
        py_bin="${PYTHON_INSTALL_DIR}/bin/python3.11"
    elif command -v python3.11 >/dev/null 2>&1; then
        py_bin="$(command -v python3.11)"
    elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
        py_bin="$(command -v python3)"
    else
        die "找不到 Python >= 3.11"
    fi
    ok "使用解释器: ${py_bin}"

    if [[ -x "${VENV_DIR}/bin/python" ]]; then
        log "venv 已存在: ${VENV_DIR}，将升级 pip 后重装包"
        "${VENV_DIR}/bin/python" -m pip install --upgrade --no-index pip 2>/dev/null || true
    else
        log "创建 venv: ${VENV_DIR}"
        # 先尝试带 pip 的 venv；失败则用 --without-pip 并从源 Python 引导 pip
        if ! "${py_bin}" -m venv "${VENV_DIR}" --copies 2>/dev/null; then
            warn "venv 创建（ensurepip）失败，改用 --without-pip 并手动引导 pip"
            "${py_bin}" -m venv "${VENV_DIR}" --copies --without-pip
            # 从源 Python 的 ensurepip 模块中提取 pip wheel 并安装到 venv
            local ensurepip_di
            ensurepip_dir="$("${py_bin}" -c 'import ensurepip, os; print(os.path.join(os.path.dirname(ensurepip.__file__), "_bundled"))' 2>/dev/null || true)"
            if [[ -n "${ensurepip_dir}" && -d "${ensurepip_dir}" ]]; then
                local pip_wheel
                pip_wheel="$(ls "${ensurepip_dir}"/pip-*.whl 2>/dev/null | head -1 || true)"
                if [[ -n "${pip_wheel}" ]]; then
                    log "  从 ${pip_wheel##*/} 引导 pip ..."
                    # 利用 wheel 内的 pip 模块自安装
                    "${VENV_DIR}/bin/python" "${pip_wheel}/pip" install --no-index --upgrade \
                        "${ensurepip_dir}"/pip-*.whl "${ensurepip_dir}"/setuptools-*.whl 2>/dev/null || true
                fi
            fi
            # 如果 ensurepip 引导也失败，尝试用源 Python 的 pip 直接安装到 venv
            if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
                warn "  ensurepip 引导失败，尝试用源 Python 的 pip 直接安装到 venv ..."
                local site_di
                site_dir="$("${VENV_DIR}/bin/python" -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)"
                if [[ -n "${site_dir}" ]]; then
                    "${py_bin}" -m pip install --no-index --target "${site_dir}" pip setuptools 2>/dev/null || true
                fi
            fi
        fi
        "${VENV_DIR}/bin/python" -m pip install --upgrade --no-index pip 2>/dev/null || true
    fi
    # 最终验证 pip 可用
    if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
        die "venv 中 pip 不可用，无法继续安装"
    fi
}

install_wheels() {
    log "从离线 wheel 安装 fw-context-mcp 及依赖 ..."
    if [[ ! -d "${WHEELS_DIR}" ]]; then
        die "未找到 wheels/ 目录"
    fi

    # ── 依赖安装：通过 requirements.txt hash 判断是否需要更新 ──
    # pip 本身已用 --upgrade-strategy only-if-needed 跳过版本足够的包，
    # 但 pip 仍需逐包检查，耗时较长。hash 一致时直接跳过整个步骤。
    local req_hash_file="${VENV_DIR}/.requirements_hash"
    local req_new_hash=""

    if [[ -f "${REQ_FILE}" ]]; then
        req_new_hash="$(sha256sum "${REQ_FILE}" | cut -d' ' -f1)"
        if [[ -f "${req_hash_file}" ]] && [[ "$(cat "${req_hash_file}")" == "${req_new_hash}" ]]; then
            ok "依赖未变化 (requirements.txt hash 一致)，跳过依赖安装"
        else
            log "安装依赖 (requirements.txt) ..."
            local pip_args=(
                install --no-index --find-links "${WHEELS_DIR}"
                --no-cache-dir --no-build-isolation
                --upgrade --upgrade-strategy only-if-needed
            )
            "${VENV_DIR}/bin/pip" "${pip_args[@]}" -r "${REQ_FILE}" \
                || die "依赖安装失败"
            echo "${req_new_hash}" > "${req_hash_file}"
            ok "依赖安装完成"
        fi
    fi

    # ── 项目 wheel 安装：通过 wheel 文件 hash 判断是否需要重装 ──
    # 版本号可能不变（如 0.24.0）但代码有更新，不能用版本号判断，
    # 改用 wheel 文件的 SHA256 hash：hash 一致说明代码未变化，跳过。
    local project_wheel
    project_wheel="$(ls "${WHEELS_DIR}"/fw_context_mcp-*.whl 2>/dev/null | head -1 || true)"
    if [[ -z "${project_wheel}" ]]; then
        warn "未找到 fw_context_mcp-*.whl，尝试从 sdist 安装"
        project_wheel="$(ls "${WHEELS_DIR}"/fw_context_mcp-*.tar.gz 2>/dev/null | head -1 || true)"
        [[ -n "${project_wheel}" ]] || die "未找到 fw-context-mcp 安装包"
    fi

    local wheel_hash_file="${VENV_DIR}/.project_wheel_hash"
    local wheel_new_hash
    wheel_new_hash="$(sha256sum "${project_wheel}" | cut -d' ' -f1)"

    if [[ -f "${wheel_hash_file}" ]] && [[ "$(cat "${wheel_hash_file}")" == "${wheel_new_hash}" ]]; then
        ok "项目 wheel 未变化 (hash 一致)，跳过安装: $(basename "${project_wheel}")"
    else
        log "安装项目 wheel: $(basename "${project_wheel}")"
        "${VENV_DIR}/bin/pip" install --no-index --find-links "${WHEELS_DIR}" \
            --no-cache-dir --no-build-isolation --upgrade --force-reinstall \
            "${project_wheel}" \
            || die "项目安装失败"
        echo "${wheel_new_hash}" > "${wheel_hash_file}"
        ok "fw-context-mcp 安装完成"
    fi
}

link_binaries() {
    mkdir -p "${LOCAL_BIN}"
    for bin_name in fw-context fw-context-mcp fw-cache-server fw-cache-admin; do
        local src="${VENV_DIR}/bin/${bin_name}"
        local dst="${LOCAL_BIN}/${bin_name}"
        if [[ ! -x "${src}" ]]; then
            warn "venv 中未找到 ${bin_name} (可能未启用对应 extras)"
            continue
        fi
        rm -f "${dst}"
        ln -s "${src}" "${dst}"
        ok "链接: ${dst} -> ${src}"
    done

    # 确保 ~/.local/bin 在 PATH 中
    case ":${PATH}:" in
        *":${LOCAL_BIN}:"*)
            ;;
        *)
            warn "${LOCAL_BIN} 不在 PATH 中。请添加以下内容到 ~/.bashrc 或 ~/.profile:"
            echo '    export PATH="${HOME}/.local/bin:${PATH}"'
            ;;
    esac
}

verify_install() {
    log "验证安装 ..."
    if [[ ! -x "${VENV_DIR}/bin/fw-context" ]]; then
        die "fw-context 命令未安装成功"
    fi
    "${VENV_DIR}/bin/fw-context" --help >/dev/null 2>&1 \
        && ok "fw-context --help 正常" \
        || warn "fw-context --help 异常，请检查"
    if "${VENV_DIR}/bin/python" -c 'import clang.cindex; import pysqlite3; import sqlite_vec; import watchfiles; import httpx; import mcp' 2>/dev/null; then
        ok "关键依赖导入成功"
    else
        warn "部分关键依赖导入失败，请检查 venv"
    fi
}

print_post_install_hint() {
    cat <<EOF

==================== 安装完成 ====================

请确保 ${LOCAL_BIN} 在你的 PATH 中。如未设置，请将以下内容加入 ~/.bashrc：

    export PATH="${LOCAL_BIN}:\$PATH"

然后执行:  source ~/.bashrc

常用命令:
  fw-context --help                 查看帮助
  fw-context init                   在固件项目目录中初始化配置
  fw-context index --build          构建语义索引（需要 compile_commands.json）
  fw-context status                 查看索引状态

详细文档请阅读: README-离线安装.md

EOF
}

case "${ACTION}" in
    verify)
        verify_bundle
        ;;
    uninstall)
        uninstall_fw_context
        ;;
    install)
        verify_bundle
        ensure_libc_compatible
        require_cmd tar "tar"
        require_cmd sha256sum "coreutils"
        if [[ ${INSTALL_PYTHON} -eq 1 ]]; then
            install_python
        fi
        create_venv
        install_wheels
        link_binaries
        verify_install
        print_post_install_hint
        ;;
esac
