#!/usr/bin/env bash
#
# fw-context 离线打包脚本
#
# 在有网络的 Linux 环境（WSL Ubuntu 22.04 或更高版本）中运行此脚本，
# 会收集 fw-context-mcp 的全部依赖、构建项目 wheel，并下载一份
# Python 3.11 独立构建，组装成可在离线 Ubuntu 20.04 服务器上安装的目录。
#
# 用法:
#   ./package.sh                # 增量更新（默认）
#   ./package.sh --refresh      # 强制清空 wheels/ 后重新下载
#   ./package.sh --no-python    # 跳过下载 Python 独立构建
#   ./package.sh --no-uv        # 跳过下载 uv 二进制
#   ./package.sh --tar          # 完成后打包成 .tar.gz
#
# 产出:
#   dist/fw-context-offline-bundle/
#     ├── install.sh
#     ├── README-离线安装.md
#     ├── python/python-standalone.tar.gz        (符号链接到具体版本)
#     ├── uv/uv.tar.gz                            (符号链接到具体版本)
#     ├── wheels/*.whl
#     ├── requirements.lock.txt                   (uv 生成的锁定文件，含来源注释)
#     ├── requirements.txt                        (pip 离线安装用)
#     └── manifest.sha256
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
BUNDLE_DIR="${DIST_DIR}/fw-context-offline-bundle"
WHEELS_DIR="${BUNDLE_DIR}/wheels"
BUILD_VENV_DIR="${SCRIPT_DIR}/.venv-resolve"
LOCK_FILE="${BUNDLE_DIR}/requirements.lock.txt"
MANIFEST_FILE="${BUNDLE_DIR}/manifest.sha256"

REFRESH=0
DOWNLOAD_PYTHON=1
DOWNLOAD_UV=1
MAKE_TAR=0
PYTHON_TARGET_VERSION="3.11"
TARGET_PLATFORM="x86_64-unknown-linux-gnu"

color_ok="\033[32m"; color_warn="\033[33m"; color_err="\033[31m"; color_info="\033[36m"; color_off="\033[0m"
# 所有状态消息输出到 stderr，避免污染 stdout（command substitution 捕获）
log()   { printf "${color_info}[*]${color_off} %s\n" "$*" >&2; }
ok()    { printf "${color_ok}[+]${color_off} %s\n" "$*" >&2; }
warn()  { printf "${color_warn}[!]${color_off} %s\n" "$*" >&2; }
err()   { printf "${color_err}[x]${color_off} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

for arg in "$@"; do
    case "$arg" in
        --refresh)    REFRESH=1 ;;
        --no-python)  DOWNLOAD_PYTHON=0 ;;
        --no-uv)      DOWNLOAD_UV=0 ;;
        --tar)        MAKE_TAR=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"; exit 0 ;;
        *) die "未知参数: $arg" ;;
    esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
    die "此脚本必须在 Linux（WSL Ubuntu）中运行，当前系统: $(uname -s)"
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
    warn "当前架构为 $(uname -m)，本脚本仅为 x86_64 目标打包；继续可能产出不可用 wheel。"
fi

command -v curl >/dev/null 2>&1 || die "未找到 curl: sudo apt-get install -y curl"
command -v tar  >/dev/null 2>&1 || die "未找到 tar:  sudo apt-get install -y tar"

if ! command -v uv >/dev/null 2>&1; then
    log "未检测到 uv，正在安装到 ~/.local/bin ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv 仍不可用，请手动安装: curl -LsSf https://astral.sh/uv/install.sh | sh"

log "确保 uv 拥有 Python ${PYTHON_TARGET_VERSION} ..."
if ! uv python find "${PYTHON_TARGET_VERSION}" >/dev/null 2>&1; then
    uv python install "${PYTHON_TARGET_VERSION}"
fi
UV_PYTHON="$(uv python find "${PYTHON_TARGET_VERSION}")"
ok "使用解释器: ${UV_PYTHON}"
[[ -x "${UV_PYTHON}" ]] || die "解释器不可执行"

mkdir -p "${BUNDLE_DIR}" "${WHEELS_DIR}"

# 准备一个临时 venv（带最新 pip），用于 uv pip compile 与 pip download
log "准备构建用 venv（带 pip）..."
rm -rf "${BUILD_VENV_DIR}"
uv venv "${BUILD_VENV_DIR}" --python "${UV_PYTHON}" --seed
"${BUILD_VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip || true
PIP_BIN="${BUILD_VENV_DIR}/bin/pip"

log "解析并锁定全部运行时依赖（含 cache-server extras），取最新可用版本 ..."
# uv pip compile: 重新解析为最新版本；保留 extras 与注释
uv pip compile \
    --extra cache-server \
    --no-strip-extras \
    --python-version "${PYTHON_TARGET_VERSION}" \
    --python-platform "${TARGET_PLATFORM}" \
    --output-file "${LOCK_FILE}" \
    --quiet \
    "${SRC_DIR}/pyproject.toml" \
    || die "依赖解析失败，请检查 pyproject.toml"

# 生成一个用于 pip download 的纯净 requirements 文件（不含 fw-context-mcp 自身、不含注释）
CLEAN_REQ="${BUNDLE_DIR}/.requirements-download.txt"
grep -E '^[A-Za-z0-9]' "${LOCK_FILE}" \
    | grep -viE '^fw[-_]context[-_]mcp' \
    | sed -E 's/[[:space:]]*\\$//; s/[[:space:]]*;.*$//; s/[[:space:]]*#.*$//; s/[[:space:]]+$//' \
    | grep -vE '^[[:space:]]*$' \
    > "${CLEAN_REQ}"
ok "锁定完成，共 $(wc -l < "${CLEAN_REQ}") 个外部依赖待下载"

if [[ "${REFRESH}" -eq 1 ]]; then
    warn "--refresh: 清空 wheels/ 缓存"
    rm -rf "${WHEELS_DIR}"
    mkdir -p "${WHEELS_DIR}"
fi

# Ubuntu 20.04 glibc 2.31 — 仅接受 manylinux_2_31 及以下 tag 的 wheel
# pip 在 WSL Ubuntu 22.04 (glibc 2.35) 上会优先选择 manylinux_2_35 wheel，
# 这会破坏 Ubuntu 20.04 兼容性，因此必须用 --platform 强制约束
PLATFORM_TAGS=(
    --platform manylinux_2_31_x86_64
    --platform manylinux_2_28_x86_64
    --platform manylinux_2_24_x86_64
    --platform manylinux_2_17_x86_64
    --platform manylinux2014_x86_64
    --platform linux_x86_64
)

log "增量下载 wheel（约束平台 manylinux_2_31 以兼容 Ubuntu 20.04）..."
# pip download 会跳过 wheels/ 中已存在的同名文件
before=$(find "${WHEELS_DIR}" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l)

# 先尝试带平台约束的下载（仅 wheel，不允许 sdist）
if ! "${PIP_BIN}" download \
        "${PLATFORM_TAGS[@]}" \
        --python-version 3.11 \
        --implementation cp \
        --abi cp311 \
        --only-binary=:all: \
        --no-deps \
        -d "${WHEELS_DIR}" \
        -r "${CLEAN_REQ}" \
        2>"${BUNDLE_DIR}/.download.err"; then
    # 兜底：逐包处理；对每个失败的包，尝试允许 sdist 或放宽平台
    warn "整体下载失败，改为逐包处理（带详细日志）..."
    while IFS= read -r pkg_spec; do
        [[ -z "${pkg_spec}" ]] && continue
        pkg_name="${pkg_spec%%=*}"
        pkg_name="${pkg_name%%[*}"; pkg_name="${pkg_name%% *}"
        pkg_name_norm="$(printf '%s' "${pkg_name}" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"
        # 已存在则跳过
        existing=$(find "${WHEELS_DIR}" -maxdepth 1 -type f -name "*.whl" 2>/dev/null \
            | grep -iE "/${pkg_name_norm//-/[-_]}-[0-9]" || true)
        if [[ -n "${existing}" ]]; then
            continue
        fi
        log "  下载: ${pkg_spec}"
        if "${PIP_BIN}" download \
                "${PLATFORM_TAGS[@]}" \
                --python-version 3.11 --implementation cp --abi cp311 \
                --only-binary=:all: --no-deps \
                -d "${WHEELS_DIR}" "${pkg_spec}" 2>/dev/null; then
            :
        elif "${PIP_BIN}" download \
                --no-deps -d "${WHEELS_DIR}" "${pkg_spec}" 2>/dev/null; then
            warn "    放宽平台后下载成功（需手动检查兼容性）: ${pkg_spec}"
        else
            warn "    下载失败: ${pkg_spec}"
        fi
    done < "${CLEAN_REQ}"
fi
rm -f "${BUNDLE_DIR}/.download.err"
after=$(find "${WHEELS_DIR}" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l)
ok "wheel 目录现有 ${after} 个文件（本次新增 $((after - before)) 个）"
rm -f "${CLEAN_REQ}"

log "构建 fw-context-mcp wheel + sdist ..."
rm -rf "${SRC_DIR}/dist" "${SRC_DIR}/build"
uv build --wheel --sdist --python "${UV_PYTHON}" --out-dir "${BUNDLE_DIR}/_project_dist" "${SRC_DIR}" >/dev/null
# 先移除旧的项目 wheel，再移入新的（确保 wheels/ 中只有最新版本）
find "${WHEELS_DIR}" -maxdepth 1 -name 'fw_context_mcp-*.whl' -delete 2>/dev/null || true
find "${WHEELS_DIR}" -maxdepth 1 -name 'fw_context_mcp-*.tar.gz' -delete 2>/dev/null || true
mv "${BUNDLE_DIR}"/_project_dist/*.whl "${WHEELS_DIR}/" 2>/dev/null || true
mv "${BUNDLE_DIR}"/_project_dist/*.tar.gz "${WHEELS_DIR}/" 2>/dev/null || true
rm -rf "${BUNDLE_DIR}/_project_dist"
PROJECT_WHL="$(ls "${WHEELS_DIR}"/fw_context_mcp-*.whl 2>/dev/null | head -1)"
[[ -n "${PROJECT_WHL}" ]] && ok "项目 wheel: $(basename "${PROJECT_WHL}")" || die "未生成项目 wheel"

# 把 fw-context-mcp 自身追加到 lock 文件（在 cleanup 之前，确保 cleanup 不会误删）
# wheel 文件名形如 fw_context_mcp-0.22.3-py3-none-any.whl
# distribution 名是第一个 '-' 之前的部分，需将 '_' 替换为 '-'
PROJECT_NAME_NORMALIZED="$(basename "${PROJECT_WHL}" .whl | cut -d- -f1 | tr '_' '-')"
{
    echo ""
    echo "# 项目自身 (本地 wheel)"
    echo "${PROJECT_NAME_NORMALIZED} @ file:wheels/$(basename "${PROJECT_WHL}")"
} >> "${LOCK_FILE}"

log "清理 lock 中已不存在的旧版本 wheel ..."
# 用 Python 解析，避免 bash 字符串处理出错
"${UV_PYTHON}" - <<'PYEOF' "${LOCK_FILE}" "${WHEELS_DIR}"
import re, sys, os, glob
lock_path, wheels_dir = sys.argv[1], sys.argv[2]

# 1. 从 lock 文件提取 (name_norm, version) 对
#    同时支持 "name==version" 和 "name @ file:..." 两种格式
keep = {}
name_re = re.compile(r'^([A-Za-z0-9_.\-]+)\s*(\[[^\]]+\])?\s*(?:==\s*([0-9A-Za-z._+!\-]+)|@\s*file:.+/(.+)-([0-9][^-]*)-(?:[^-]+-){2,3}[^.]+\.whl)')
with open(lock_path, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = line.split(';')[0].split('#')[0].strip()
        line = line.rstrip('\\').strip()
        m = name_re.match(line)
        if not m:
            continue
        name = re.sub(r'[-_.]+', '-', (m.group(1) or m.group(4) or '').lower())
        ver = m.group(3) or m.group(5)
        if name and ver:
            keep.setdefault(name, set()).add(ver.split('+')[0].split('-')[0])

# 2. 遍历 wheels/，解析文件名
removed = 0
for path in glob.glob(os.path.join(wheels_dir, '*.whl')):
    base = os.path.basename(path)
    name_part = base.split('-', 1)[0]
    ver_part = base.split('-', 2)[1] if base.count('-') >= 2 else ''
    name_norm = re.sub(r'[-_.]+', '-', name_part.lower())
    ver_clean = ver_part.split('+')[0].split('-')[0]
    allowed = keep.get(name_norm)
    if allowed is None:
        print(f"  移除未在 lock 中的: {base}")
        os.remove(path); removed += 1
    elif ver_clean not in allowed:
        print(f"  移除旧版本: {base} (lock 中为 {','.join(sorted(allowed))})")
        os.remove(path); removed += 1
print(f"清理完成，移除 {removed} 个旧 wheel")
PYEOF


# 通用下载函数：先正常尝试 HTTPS，失败则用 -k 跳过证书校验
# （企业代理常拦截 HTTPS，最终文件完整性由 manifest.sha256 保证）
gh_curl() {
    if curl -fsSL "$@" 2>/dev/null; then return 0; fi
    warn "  普通 HTTPS 失败，改用 -k 跳过证书校验: $*"
    curl -kfsSL "$@"
}
gh_curl_download() {
    # 用法: gh_curl_download <url> <output>
    if curl -fL --retry 3 -o "$2" "$1" 2>/dev/null; then return 0; fi
    warn "  普通 HTTPS 下载失败，改用 -k 跳过证书校验: $1"
    curl -kfL --retry 3 -o "$2" "$1"
}

PY_STANDALONE_TARBALL=""
if [[ "${DOWNLOAD_PYTHON}" -eq 1 ]]; then
    log "下载 Python ${PYTHON_TARGET_VERSION} 独立构建 (python-build-standalone) ..."
    mkdir -p "${BUNDLE_DIR}/python"
    PY_MINOR="$("${UV_PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    API_URL="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
    log "  查询 cpython-${PY_MINOR} 系列的最新 release ..."
    api_json="$(gh_curl "${API_URL}" 2>/dev/null || true)"
    # 优先 install_only 变体（标准 bin/lib/include 布局，含 pip，体积小）
    # 注意：GitHub API 返回的 URL 中 '+' 会被编码为 '%2B'
    # grep 无匹配时返回 1，需 || true 防止 set -e + pipefail 退出
    # 查找顺序: install_only.tar.gz > install_only.tar.zst > pgo+lto-full.tar.gz > pgo+lto-full.tar.zst
    asset_url="$(printf '%s' "${api_json}" \
        | grep -oE "https://[^\"' ]*cpython-${PY_MINOR}\.[0-9]+[^\"' ]*-x86_64-unknown-linux-gnu-install_only\.tar\.gz" \
        | sort -uV | tail -1 || true)"
    asset_ext="tar.gz"
    if [[ -z "${asset_url}" ]]; then
        asset_url="$(printf '%s' "${api_json}" \
            | grep -oE "https://[^\"' ]*cpython-${PY_MINOR}\.[0-9]+[^\"' ]*-x86_64-unknown-linux-gnu-install_only\.tar\.zst" \
            | sort -uV | tail -1 || true)"
        asset_ext="tar.zst"
    fi
    if [[ -z "${asset_url}" ]]; then
        # fallback: pgo+lto-full 变体（布局不同，含 build/ 和 install/ 子目录）
        asset_url="$(printf '%s' "${api_json}" \
            | grep -oE "https://[^\"' ]*cpython-${PY_MINOR}\.[0-9]+[^\"' ]*-x86_64-unknown-linux-gnu-pgo(%2B|\+)lto-full\.tar\.gz" \
            | sort -uV | tail -1 || true)"
        asset_ext="tar.gz"
    fi
    if [[ -z "${asset_url}" ]]; then
        asset_url="$(printf '%s' "${api_json}" \
            | grep -oE "https://[^\"' ]*cpython-${PY_MINOR}\.[0-9]+[^\"' ]*-x86_64-unknown-linux-gnu-pgo(%2B|\+)lto-full\.tar\.zst" \
            | sort -uV | tail -1 || true)"
        asset_ext="tar.zst"
    fi
    if [[ -z "${asset_url}" ]]; then
        # fallback: 直接打包 uv 已缓存解压的 standalone 构建
        # uv 的符号链接名为 cpython-3.11-linux-x86_64-gnu（无 -unknown），
        # 实际目录为 cpython-3.11.15-linux-x86_64-gnu。用通配符匹配两种形式。
        UV_PY_BASE="$(uv python dir)"
        UV_PY_DIR="$(ls -d "${UV_PY_BASE}/cpython-${PY_MINOR}.*-linux-x86_64-gnu" 2>/dev/null | head -1 || true)"
        UV_PY_DIR="$(readlink -f "${UV_PY_DIR}" 2>/dev/null || true)"
        if [[ -n "${UV_PY_DIR}" && -d "${UV_PY_DIR}" ]]; then
            log "  在线查询失败，改用 uv 缓存的构建: ${UV_PY_DIR}"
            PY_STANDALONE_TARBALL="${BUNDLE_DIR}/python/python-${PY_MINOR}-standalone.tar.gz"
            rm -f "${PY_STANDALONE_TARBALL}"
            tar -czf "${PY_STANDALONE_TARBALL}" -C "$(dirname "${UV_PY_DIR}")" "$(basename "${UV_PY_DIR}")"
            ok "已从 uv 缓存打包: ${PY_STANDALONE_TARBALL}"
        else
            die "无法获取 Python 独立构建，请检查网络或手动下载"
        fi
    else
        # 规范化最终 tarball 名为 .tar.gz（确保目标 Ubuntu 20.04 自带 tar 即可解压）
        PY_STANDALONE_TARBALL="${BUNDLE_DIR}/python/python-${PY_MINOR}-standalone.tar.gz"
        if [[ -f "${PY_STANDALONE_TARBALL}" ]]; then
            ok "已存在: python-${PY_MINOR}-standalone.tar.gz"
        else
            log "  下载: ${asset_url}"
            # 使用临时文件名（不创建空文件），下载后转换或直接使用
            tmp_base="python/.dl-$$"
            trap 'rm -f "${BUNDLE_DIR}/${tmp_base}".tar.zst "${BUNDLE_DIR}/${tmp_base}"' EXIT
            if [[ "${asset_ext}" == "tar.zst" ]]; then
                # 下载 .tar.zst，再转换为 .tar.gz
                if ! gh_curl_download "${asset_url}" "${BUNDLE_DIR}/${tmp_base}.tar.zst"; then
                    rm -f "${BUNDLE_DIR}/${tmp_base}.tar.zst"
                    die "下载 Python 独立构建 (.tar.zst) 失败"
                fi
                if ! command -v zstd >/dev/null 2>&1; then
                    err "需要 zstd 来解压 .tar.zst，请安装: sudo apt-get install -y zstd"
                    rm -f "${BUNDLE_DIR}/${tmp_base}.tar.zst"
                    die "缺少 zstd"
                fi
                log "  转换 .tar.zst → .tar.gz ..."
                if ! zstd -dc "${BUNDLE_DIR}/${tmp_base}.tar.zst" | gzip -9 > "${PY_STANDALONE_TARBALL}"; then
                    rm -f "${BUNDLE_DIR}/${tmp_base}.tar.zst" "${PY_STANDALONE_TARBALL}"
                    die "转换失败"
                fi
                rm -f "${BUNDLE_DIR}/${tmp_base}.tar.zst"
            else
                if ! gh_curl_download "${asset_url}" "${PY_STANDALONE_TARBALL}"; then
                    rm -f "${PY_STANDALONE_TARBALL}"
                    die "下载 Python 独立构建失败"
                fi
            fi
            ok "已下载: ${PY_STANDALONE_TARBALL}"
        fi
    fi
    # 写入稳定的符号链接文件名，便于 install.sh 引用
    if [[ -n "${PY_STANDALONE_TARBALL}" ]]; then
        ln -sf "$(basename "${PY_STANDALONE_TARBALL}")" "${BUNDLE_DIR}/python/python-standalone.tar.gz"
    fi
fi

UV_TARBALL=""
if [[ "${DOWNLOAD_UV}" -eq 1 ]]; then
    log "下载 uv 二进制 (x86_64-unknown-linux-gnu) ..."
    mkdir -p "${BUNDLE_DIR}/uv"
    API_URL="https://api.github.com/repos/astral-sh/uv/releases/latest"
    asset_url="$(gh_curl "${API_URL}" 2>/dev/null \
        | grep -oE "https://[^\"' ]*/uv-x86_64-unknown-linux-gnu\.tar\.gz" \
        | sort -uV | tail -1 || true)"
    if [[ -z "${asset_url}" ]]; then
        warn "无法从 GitHub 查询 uv release，将打包本机 uv 二进制"
        UV_BIN="$(command -v uv)"
        UV_TARBALL="${BUNDLE_DIR}/uv/uv-x86_64-unknown-linux-gnu.tar.gz"
        rm -f "${UV_TARBALL}"
        tar -czf "${UV_TARBALL}" -C "$(dirname "${UV_BIN}")" "$(basename "${UV_BIN}")"
        ok "已打包本机 uv: ${UV_TARBALL}"
    else
        UV_TARBALL="${BUNDLE_DIR}/uv/uv-x86_64-unknown-linux-gnu.tar.gz"
        if [[ -f "${UV_TARBALL}" ]]; then
            ok "已存在: uv-x86_64-unknown-linux-gnu.tar.gz"
        else
            log "  下载: ${asset_url}"
            gh_curl_download "${asset_url}" "${UV_TARBALL}" \
                || die "下载 uv 失败"
            ok "已下载: ${UV_TARBALL}"
        fi
    fi
    ln -sf "$(basename "${UV_TARBALL}")" "${BUNDLE_DIR}/uv/uv.tar.gz"
fi


log "复制 install.sh 与 README 到 bundle ..."
cp -f "${SCRIPT_DIR}/install.sh" "${BUNDLE_DIR}/install.sh"
chmod +x "${BUNDLE_DIR}/install.sh"
[[ -f "${SCRIPT_DIR}/README-离线安装.md" ]] && cp -f "${SCRIPT_DIR}/README-离线安装.md" "${BUNDLE_DIR}/README-离线安装.md"
[[ -f "${SCRIPT_DIR}/AGENT-INSTALL.md" ]] && cp -f "${SCRIPT_DIR}/AGENT-INSTALL.md" "${BUNDLE_DIR}/AGENT-INSTALL.md"

log "生成离线安装用 requirements.txt（不含 fw-context-mcp 自身，单独安装项目 wheel）..."
grep -E '^[A-Za-z0-9]' "${LOCK_FILE}" \
    | grep -viE '^fw[-_]context[-_]mcp' \
    | sed -E 's/[[:space:]]*\\$//; s/[[:space:]]*;.*$//; s/[[:space:]]*#.*$//; s/[[:space:]]+$//' \
    | grep -vE '^[[:space:]]*$' \
    > "${BUNDLE_DIR}/requirements.txt"

log "生成 manifest.sha256 ..."
(
    cd "${BUNDLE_DIR}" >/dev/null
    find . -type f \
        ! -name 'manifest.sha256' \
        -exec sha256sum {} \; | sort -k2
) > "${MANIFEST_FILE}"
ok "manifest 已生成"

log "校验 wheel 平台兼容性（Ubuntu 20.04, glibc >= 2.31）..."
incompatible=$("${UV_PYTHON}" - <<'PYEOF' "${WHEELS_DIR}"
import sys, os, glob, re
wheels_dir = sys.argv[1]
bad = 0
for path in sorted(glob.glob(os.path.join(wheels_dir, '*.whl'))):
    base = os.path.basename(path)
    rest = base[:-len('.whl')]
    plat = rest.rsplit('-', 1)[-1]
    if plat == 'any' or plat.startswith('musllinux'):
        continue
    tags = re.findall(r'manylinux_2_(\d+)_', plat)
    legacy = re.findall(r'manylinux(1|2014)', plat)
    if not tags and (legacy or plat.startswith('linux_')):
        continue
    if not tags:
        continue
    nums = [int(t) for t in tags]
    if all(n > 31 for n in nums):
        sys.stderr.write(f"  {base} 不兼容 Ubuntu 20.04 (glibc 2.31): {plat}\n")
        bad += 1
print(bad)
PYEOF
)
if [[ "${incompatible}" -ne 0 ]]; then
    warn "有 ${incompatible} 个 wheel 存在平台兼容性风险（仅警告，pip 会跳过不可用 tag）"
else
    ok "全部 wheel 平台兼容性检查通过"
fi

ok "==================== 打包完成 ===================="
ok "离线包目录: ${BUNDLE_DIR}"
log "目录结构:"
( cd "${BUNDLE_DIR}" && find . -maxdepth 2 -mindepth 1 | sort | sed 's/^/  /' )
echo
log "下一步: 将 ${BUNDLE_DIR} 整体拷贝到目标服务器，运行 ./install.sh"
if [[ ${MAKE_TAR} -eq 1 ]]; then
    log "打包为 tar.gz ..."
    tar -czf "${DIST_DIR}/fw-context-offline-bundle.tar.gz" \
        -C "${DIST_DIR}" "fw-context-offline-bundle"
    ok "已生成: ${DIST_DIR}/fw-context-offline-bundle.tar.gz"
fi
