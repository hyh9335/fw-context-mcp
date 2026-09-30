# Agent 安装指令：fw-context-mcp 离线安装

> **本文件供 AI agent 自动读取执行。** 你（agent）的目标是在当前 Linux 服务器上完成 fw-context-mcp 的离线安装，并在安装成功后报告结果。请严格按照以下步骤执行，不要跳过任何检查。
>
> **核心原则**：
> - **用户级安装，不使用 sudo**：所有文件装在用户家目录下（`~/.fw-context/` 和 `~/.local/bin/`），无需 root 权限
> - 所有命令验证使用**绝对路径**（`~/.local/bin/` 或 `~/.fw-context/.venv/bin/`），不依赖 PATH 是否正确配置
> - 遇到不确定的情况时，优先检查退出码而非输出文本
> - 不要无限等待任何命令；对可能阻塞的命令使用 `timeout` 包裹

---

## 0. 概述

你即将安装 **fw-context-mcp** —— 一个面向嵌入式 C/C++ 固件的语义代码智能 MCP 服务器。

离线包已包含全部所需文件（Python 解释器、wheel 依赖、安装脚本），**无需联网**，**无需 root 权限**。

安装方式：通过 `install.sh --user` 脚本完成用户级安装，脚本会自动处理 Python 解释器部署、venv 创建、依赖安装和符号链接创建。

安装位置：
- Python 解释器：`~/.fw-context/python/python3.11/`
- venv：`~/.fw-context/.venv/`
- 命令符号链接：`~/.local/bin/`（fw-context, fw-context-mcp, fw-cache-server, fw-cache-admin）

---

## 1. 前置条件检查

在执行安装前，**必须**逐项检查以下条件。如果任何一项不满足，停止安装并报告具体原因。

### 1.1 操作系统与架构

```bash
uname -s   # 必须输出 Linux
uname -m   # 必须输出 x86_64
```

- 如果操作系统不是 Linux，**停止**并报告："不支持的平台: <输出值>，仅支持 Linux x86_64"
- 如果架构不是 x86_64，**停止**并报告："不支持的架构: <输出值>，仅支持 x86_64"

### 1.2 必要的系统命令

```bash
for cmd in bash tar sha256sum ldd awk find mktemp readlink timeout head; do
    command -v "$cmd" >/dev/null 2>&1 || echo "MISSING: $cmd"
done
```

- 如果输出任何 `MISSING:`，**停止**并报告缺少的命令（提示用户联系管理员安装，例如 "缺少命令: awk，请联系管理员安装 gawk"）
- `timeout` 命令用于后续 MCP 服务器启动检查，不可缺失

### 1.3 glibc 版本

```bash
ldd --version | head -1 | awk '{print $NF}'
```

- glibc 版本建议 >= 2.31（对应 Ubuntu 20.04）
- 如果低于 2.31，发出警告但**不停止**（install.sh 自身仅 warn 不 abort，实际 wheel 按 manylinux_2_17 标准打包，glibc >= 2.17 即可运行）
- 如果版本解析失败（如 musl/Alpine 上 `ldd --version` 格式不同），发出警告并继续

### 1.4 磁盘空间与家目录可写性

检查实际写入位置的分区：

```bash
df -h ~ /tmp
```

- `~`（home 目录，venv、Python 和符号链接位置）至少需要 500 MB（持久）
- `/tmp`（Python 解压临时目录）至少需要 200 MB（临时，解压后即清理）
- 如果空间不足，**停止**并报告："磁盘空间不足: <分区> 可用 <空间>"

检查家目录可写性（NFS root-squash、quota 限制、只读文件系统会导致安装失败）：

```bash
test -w ~ || echo "STOP: 家目录不可写"
```

- 如果输出 "STOP: 家目录不可写"，**停止**并报告："家目录不可写，无法进行用户级安装。请检查 NFS 挂载选项或磁盘配额"

### 1.5 离线包完整性预检

确认你当前所在的目录就是离线包根目录（应包含 `install.sh`、`wheels/`、`python/` 等文件）：

```bash
# 检查必需文件存在
for f in install.sh wheels python manifest.sha256 requirements.txt; do
    test -e "$f" || echo "MISSING: $f"
done

# 检查 manifest 非空（空 manifest 会导致校验假通过）
test -s manifest.sha256 || echo "MISSING: manifest.sha256 为空或不存在"

# 检查 Python tarball 符号链接可达（符号链接断裂时 install 会失败）
readlink -e python/python-standalone.tar.gz >/dev/null 2>&1 \
    || echo "WARN: python/python-standalone.tar.gz 不可达，安装时将尝试使用系统 Python"

# 检查项目 wheel 存在
ls wheels/fw_context_mcp-*.whl >/dev/null 2>&1 \
    || echo "MISSING: wheels/fw_context_mcp-*.whl"
```

- 如果输出任何 `MISSING:`，**停止**并报告缺失的文件
- 如果仅输出 `WARN:`（Python tarball 不可达），可以继续，但后续安装可能使用系统 Python

### 1.6 修复文件权限与行尾

从 Windows 拷贝的文件可能缺少执行权限或带有 CRLF 行尾，需预先修复：

```bash
# 确保安装脚本有执行权限
chmod +x install.sh

# 检测并修复 CRLF 行尾（会导致 "bad interpreter" 错误）
# 使用 grep 而非 file 命令，避免对 file 工具的依赖
if grep -q $'\r' install.sh 2>/dev/null; then
    sed -i 's/\r$//' install.sh
fi
```

### 1.7 解析期望版本号

从 wheel 文件名解析版本号，用于后续验证（不要硬编码版本号）：

```bash
# 从 wheel 文件名提取版本：fw_context_mcp-0.24.0-py3-none-any.whl → 0.24.0
# 正则兼容预发布版本（如 0.24.0rc1）
EXPECTED_VERSION=$(ls wheels/fw_context_mcp-*.whl 2>/dev/null \
    | head -1 | sed -E 's/.*fw_context_mcp-([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.]*).*/\1/')
echo "期望版本: ${EXPECTED_VERSION}"
```

- 如果 `EXPECTED_VERSION` 为空，**停止**并报告："无法从 wheel 文件名解析版本号"

---

## 2. 校验离线包完整性

使用 install.sh 的 `--verify` 模式校验所有文件未被传输损坏。注意：诊断信息输出到 **stderr**，需合并捕获；以**退出码**为准。

```bash
./install.sh --verify 2>&1; VERIFY_RC=$?
echo "verify exit code: ${VERIFY_RC}"
```

- 退出码为 0：校验通过，继续下一步
- 退出码非 0：校验失败，**停止**并报告完整的错误输出，告知用户离线包在传输过程中损坏，需要重新拷贝

---

## 3. 执行安装

### 3.1 执行用户级安装

使用 `--user` 参数执行用户级安装（无需 sudo），包裹 timeout 防止意外挂起：

```bash
timeout 300 ./install.sh --user 2>&1; INSTALL_RC=$?
echo "install exit code: ${INSTALL_RC}"
```

- 退出码为 0：安装成功，继续验证
- 退出码为 124：安装超时（5 分钟），**停止**并报告
- 退出码非 0：安装失败，按 §3.3 诊断

> **注意**：始终使用 `--user` 模式。如果错误信息提及 `/opt`，说明你可能遗漏了 `--user` 参数（`--user` 模式下所有写入均在 `$HOME` 下）。**禁止使用 sudo**。

### 3.2 安装过程说明

安装脚本的核心步骤依次为：
1. 校验离线包完整性（manifest.sha256）
2. 检查 glibc 兼容性和必要命令
3. 解压 Python 3.11 独立构建到 `~/.fw-context/python/python3.11/`（用户级，无需 root）
4. 在 `~/.fw-context/.venv` 创建虚拟环境（如已存在则升级 pip 后复用）
5. 用 `pip install --no-index --find-links wheels/ -r requirements.txt` 离线安装全部依赖（若 requirements.txt hash 与上次一致则跳过）
6. 用 `pip install --no-index --force-reinstall` 安装项目 wheel（fw_context_mcp-*.whl）（若 wheel hash 与上次一致则跳过）
7. 创建符号链接 `~/.local/bin/fw-context`、`fw-context-mcp`、`fw-cache-server`、`fw-cache-admin`
8. 自动验证 `fw-context --help` 可执行，以及关键依赖（clang.cindex、pysqlite3、sqlite_vec、watchfiles、httpx、mcp）可导入

### 3.3 安装失败诊断与恢复

根据错误信息进行诊断。注意区分"install.sh 输出"与"OS/动态链接器输出"：

| 错误关键字 | 来源 | 可能原因 | 恢复操作 |
|-----------|------|---------|---------|
| `完整性校验失败` | install.sh | 离线包损坏 | 重新拷贝离线包（**不可重试**） |
| `未找到 fw-context-mcp 安装包` | install.sh | wheel 缺失 | 重新拷贝 `wheels/` 目录（**不可重试**） |
| `缺少命令:` | install.sh | 系统缺少必要命令 | 联系管理员安装对应命令后重试 |
| `既没有 Python 独立构建包，系统的 Python 也低于 3.11` | install.sh | Python tarball 缺失且系统 Python < 3.11 | 重新拷贝 `python/` 目录（**不可重试**） |
| `找不到 Python >= 3.11` | install.sh | Python 安装失败或路径异常 | `rm -rf ~/.fw-context/.venv ~/.fw-context/python` 后重试 |
| `解压 Python 失败` | install.sh | Python tarball 损坏 | 重新拷贝 `python/` 目录（**不可重试**） |
| `解压后未找到 python3.11 可执行文件` | install.sh | Python tarball 内容不完整 | 重新拷贝 `python/` 目录（**不可重试**） |
| `依赖安装失败` | install.sh | wheel 损坏或平台不兼容 | 重新拷贝 `wheels/` 目录（**不可重试**） |
| `项目安装失败` | install.sh | 项目 wheel 损坏 | 重新拷贝 `wheels/` 目录（**不可重试**） |
| `fw-context 命令未安装成功` | install.sh | 安装不完整 | `rm -rf ~/.fw-context/.venv` 后重试 |
| `venv 中 pip 不可用` | install.sh | Python 损坏或 venv 创建失败 | `rm -rf ~/.fw-context/.venv` 后重试 |
| `Permission denied` | OS/shell | install.sh 无执行权限或家目录不可写 | `chmod +x install.sh` 后重试；检查家目录权限 |
| `bad interpreter` | OS/shell | CRLF 行尾 | `sed -i 's/\r$//' install.sh` 后重试 |
| `No space left` | OS | 磁盘空间不足 | 清理磁盘后重试 |
| `GLIBC_2.xx not found` | 动态链接器 | glibc 版本过低 | 更换到 Ubuntu 20.04+ 服务器（**不可重试**） |
| `sudo: command not found` | OS | 家目录不可写触发了 install.sh 的 sudo 回退路径 | 检查家目录可写性（§1.4）；不可重试，需修复家目录权限 |
| `gzip:` 或 `tar:` | OS | Python tarball 损坏（非 gzip 格式或截断） | 重新拷贝 `python/` 目录（**不可重试**） |
| `is not a supported wheel` | pip | wheel 平台不兼容当前系统 | 更换到 x86_64 Linux 服务器（**不可重试**） |
| `timed out` | timeout | 安装过程卡住 | `rm -rf ~/.fw-context/.venv` 后重试 |

**重试策略**：
- **不可重试错误**（完整性校验失败、wheel 缺失、glibc 过低）：停止并报告
- **可重试错误**（venv 损坏、权限问题、磁盘空间修复后）：重试前先清理 `rm -rf ~/.fw-context/.venv`
- 最多重试 2 次；如果 2 次后仍失败，停止并向用户报告完整错误日志

**中断恢复**：如果上一次安装被中断（未正常退出），重试前**必须**清理残留 venv：
```bash
rm -rf ~/.fw-context/.venv
```

---

## 4. 验证安装成功

安装完成后，**必须**执行以下全部验证步骤。所有步骤通过后才算安装成功。

> **重要**：以下验证命令全部使用**绝对路径**，不依赖 PATH 是否正确配置。

### 4.1 PATH 配置（为后续使用做准备）

检查 `~/.local/bin` 是否在 PATH 中：

```bash
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) echo "PATH OK" ;;
    *) echo "PATH MISSING" ;;
esac
```

如果输出 "PATH MISSING"，执行以下操作：
1. 在当前 shell 中**立即**导出（不依赖 source ~/.bashrc，因为非交互 shell 中 ~/.bashrc 会提前 return）：

```bash
export PATH="${HOME}/.local/bin:${PATH}"
hash -r
```

2. 将以下内容追加到 `~/.bashrc`（为未来交互式 shell 做准备，避免重复追加）：

```bash
grep -q 'HOME/.local/bin' ~/.bashrc 2>/dev/null \
    || echo 'export PATH="${HOME}/.local/bin:${PATH}"' >> ~/.bashrc
```

### 4.2 版本检查

```bash
~/.local/bin/fw-context --version; VERSION_RC=$?
echo "version exit code: ${VERSION_RC}"
```

- 退出码必须为 0
- 输出必须包含 `EXPECTED_VERSION`（§1.7 中解析的版本号）
- 如果命令不存在、退出码非 0 或输出版本不匹配，安装失败

### 4.3 帮助检查

```bash
~/.local/bin/fw-context --help >/dev/null 2>&1
echo "help exit code: $?"
```

- 退出码必须为 0
- 输出应包含 `init`、`index`、`status` 等子命令

### 4.4 符号链接检查

验证 4 个符号链接存在**且目标可执行**（`test -x` 会跟随符号链接，断链时返回非零）：

```bash
for bin in fw-context fw-context-mcp fw-cache-server fw-cache-admin; do
    if test -x "${HOME}/.local/bin/${bin}"; then
        echo "${bin}: OK"
    else
        echo "${bin}: BROKEN or MISSING"
    fi
done
```

- 4 个全部输出 OK 才算通过
- 如果输出 BROKEN，说明符号链接断裂（venv 中的目标不存在），安装失败

### 4.5 依赖导入检查

```bash
~/.fw-context/.venv/bin/python -c "import clang.cindex, pysqlite3, sqlite_vec, watchfiles, httpx, mcp; print('core deps OK')"
```

- 输出必须为 `core deps OK`
- 这 6 个是核心依赖（与 install.sh 自身的 verify_install 一致）

额外验证 cache-server 扩展依赖（用于 `fw-cache-server`，已包含在 requirements.txt 中，安装成功后应可用）：

```bash
~/.fw-context/.venv/bin/python -c "import fastapi, uvicorn, asyncpg; print('cache-server deps OK')" 2>/dev/null \
    && echo "cache-server extras: 可用" \
    || echo "cache-server extras: 不可用（安装不完整，requirements.txt 中的依赖未全部安装）"
```

- 如果核心依赖导入失败，安装不完整
- 如果 cache-server 依赖不可用，说明 requirements.txt 中的依赖未完整安装，应视为安装不完整（按 §3.3 中 `依赖安装失败` 处理）

### 4.6 MCP 服务器启动检查

使用 `timeout` + `</dev/null`（关闭 stdin 使服务器快速 EOF）+ 直接捕获退出码（不通过管道）：

```bash
timeout 30 ~/.local/bin/fw-context-mcp </dev/null >/tmp/mcp_check.out 2>&1
MCP_RC=$?
echo "mcp exit code: ${MCP_RC}"
echo "--- output ---"
head -10 /tmp/mcp_check.out
```

判断标准：
- 退出码为 124（timeout 终止）：**正常** —— 服务器在等待 stdin 输入，说明已成功启动
- 退出码为 0：**正常** —— stdin 关闭后服务器自行退出
- 输出包含 `ModuleNotFoundError`、`ImportError`、`Traceback`、`Segmentation fault`：**失败**
- 输出包含 `Project not initialized` 或 `INFO`：**正常** —— 说明服务器已启动并检测到当前目录未初始化
- 首次启动可能较慢（加载 libclang 等 native 扩展），30 秒 timeout 足够

清理临时文件：
```bash
rm -f /tmp/mcp_check.out
```

---

## 5. 安装成功报告

所有验证步骤通过后，向用户输出以下格式的报告：

```
安装成功：fw-context-mcp <版本号>

安装路径:
  Python: ~/.fw-context/python/python3.11/
  venv:   ~/.fw-context/.venv/
  命令:   ~/.local/bin/fw-context, fw-context-mcp, fw-cache-server, fw-cache-admin

验证结果:
  [x] 版本: <版本号>
  [x] 帮助: 正常
  [x] 符号链接: 4/4 可执行
  [x] 核心依赖导入: 正常
  [x] MCP 服务器: 正常启动

下一步:
  cd /path/to/your/firmware/project
  fw-context init
  fw-context index --build
```

如果 cache-server 依赖也可用，在报告中追加：
```
  [x] cache-server 扩展: 可用
```

---

## 6. 卸载（如需要）

如果用户要求卸载：

```bash
./install.sh --uninstall --user
```

这会移除：
- `~/.local/bin/` 下的 4 个符号链接
- `~/.fw-context/`（含 venv、Python 用户级安装和缓存数据）

> **说明**：卸载基于 `$HOME` 清理，`--user` 参数保持与安装时一致。即使不传 `--user`，`~/.fw-context/` 也会被无条件清理；`--user` 仅影响 `PREFIX` 变量（决定是否检查 `/opt/fw-context/python3.11`），对用户级安装无影响。

卸载后验证（检查命令是否真正不可用，而非仅不在 PATH 中）：
```bash
test -x ~/.local/bin/fw-context && echo "卸载失败: 命令仍存在" || echo "卸载成功"
```

---

## 7. 注意事项

- **用户级安装**：所有文件装在用户家目录下，不使用 sudo，不需要 root 权限
- 安装是**幂等**的：重复运行不会造成问题。已有 venv 会复用（仅升级 pip）。**跳过逻辑**：依赖按 `requirements.txt` 的 SHA256 hash 判断，项目 wheel 按 wheel 文件 hash 判断——hash 与上次一致则跳过该步骤，不一致则执行安装并更新 hash。首次安装无 hash 文件，全部正常安装
- 安装脚本使用 `set -Eeuo pipefail`，任何命令失败都会立即终止
- Python 独立构建是 python-build-standalone 的 `install_only` 变体，自带 pip
- wheel 按 manylinux_2_17 或更低标准打包（兼容 glibc >= 2.17，Ubuntu 20.04 的 glibc 2.31 完全满足）
- 如果服务器已有 Python >= 3.11，可删除 `python/python-standalone.tar.gz`，安装脚本会自动检测并使用系统 Python
- `libclang` PyPI wheel 自带 `libclang.so`，无需安装系统 `libclang-dev`
- fw-context 解析宏展开时可能调用外部 `clang` 命令（非必需，仅影响宏查询功能；可在配置中设置 `expand_macros = false` 关闭）
- `~/.fw-context/.venv/` 是权威安装位置，所有验证应优先使用此路径下的文件
- 如果依赖导入失败但 pip 可用（版本号一致但文件损坏），删除 `~/.fw-context/.venv` 后重新运行安装脚本
