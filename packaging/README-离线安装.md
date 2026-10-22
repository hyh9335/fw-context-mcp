# fw-context 离线安装说明

本文档介绍如何在**离线 Ubuntu 20.04** 服务器上安装 fw-context-mcp。

离线包内已包含 fw-context-mcp 自身、其全部 Python 依赖、一份 Python 3.11 独立构建
（python-build-standalone），以及 uv 二进制（可选），无需联网即可完成安装。

---

## 1. 离线包内容

```
fw-context-offline-bundle/
├── install.sh                       离线安装/卸载/校验脚本
├── README-离线安装.md               本文档
├── requirements.txt                 pip 离线安装用清单
├── requirements.lock.txt            uv 锁定的依赖来源与版本（含注释）
├── manifest.sha256                  全包文件 SHA256 校验清单
├── python/
│   ├── python-standalone.tar.gz      → 符号链接到具体版本 tarball
│   └── cpython-3.11.x-...tar.gz      Python 3.11 独立构建
├── uv/
│   ├── uv.tar.gz                    → 符号链接到具体版本 tarball
│   └── uv-x86_64-...tar.gz          uv 二进制（备用）
└── wheels/                          全部 Python wheel 文件
    ├── fw_context_mcp-0.22.x-py3-none-any.whl
    ├── httpx-*.whl
    ├── libclang-*.whl
    ├── mcp-*.whl
    ├── pysqlite3-*.whl
    ├── sqlite_vec-*.whl
    ├── watchfiles-*.whl
    ├── fastapi-*.whl                (cache-server extras)
    ├── uvicorn-*.whl
    ├── asyncpg-*.whl
    └── ... (其余传递依赖)
```

> **注意**：`uv/` 目录在默认安装流程中不会被使用（直接调用 Python 解释器即可），
> 仅当希望使用 uv 管理 Python 环境时作为备选。

---

## 2. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 20.04 (Focal) 或更高版本；其他基于 glibc 2.31+ 的发行版也可 |
| 架构 | x86_64 |
| glibc | ≥ 2.31（Ubuntu 20.04 默认 2.31） |
| 磁盘 | 约 500 MB（其中 Python 独立构建 ~120 MB，wheels ~150 MB，安装后 venv ~200 MB） |
| 权限 | 默认安装到 `/opt/fw-context/python3.11`，需要 sudo；venv 装在 `~/.fw-context/.venv` |
| 系统包 | `tar`、`coreutils` (sha256sum)、`ldd` —— Ubuntu 20.04 默认已带 |

### 关于系统级 libclang

`libclang` 的 PyPI wheel 自带了 `libclang.so` 二进制，**无需**再安装系统的 `libclang-dev`。

但 fw-context 在解析宏展开时会调用外部 `clang` 命令。如果你的固件项目需要此功能，
请在目标服务器上额外安装系统 clang（离线情况下需要自行准备 deb 包）：

```bash
# 在有网的机器上下载 deb 包及依赖
apt-get download clang libclang-cpp-dev libllvm-NN-dev  # NN 对应 clang 版本
# 拷贝到目标服务器后
sudo dpkg -i *.deb
```

如果不需要宏展开查询，可以跳过此步骤，fw-context 主体功能（符号索引、调用关系、
FTS5 全文检索、向量检索等）不依赖系统 clang。

---

## 3. 安装步骤

### 3.1 拷贝离线包到服务器

将整个 `fw-context-offline-bundle/` 目录拷贝到目标服务器，例如 `/home/<user>/fw-context-offline-bundle/`。
可用 U 盘、scp、rsync 等任何方式。

### 3.2 校验完整性（可选但推荐）

```bash
cd /path/to/fw-context-offline-bundle
./install.sh --verify
```

这会根据 `manifest.sha256` 校验离线包中所有文件是否在传输过程中损坏。

### 3.3 执行安装

```bash
sudo ./install.sh
```

安装过程会：

1. 解压 Python 3.11 独立构建到 `/opt/fw-context/python3.11/`（需要 sudo）
2. 在 `~/.fw-context/.venv` 创建虚拟环境
3. 用 `pip install --no-index --find-links wheels/` 离线安装 fw-context-mcp 及其全部依赖
4. 把 `fw-context`、`fw-context-mcp`、`fw-cache-server`、`fw-cache-admin` 符号链接到 `~/.local/bin/`
5. 自动验证关键依赖能否正常导入

### 3.4 配置 PATH

安装完成后，确保 `~/.local/bin` 在 PATH 中。把下面这行加入 `~/.bashrc`：

```bash
export PATH="${HOME}/.local/bin:${PATH}"
```

然后执行 `source ~/.bashrc` 使其生效。

### 3.5 验证安装

```bash
fw-context --help
fw-context --version    # 应输出 0.22.3 或更高版本
```

---

## 4. 自定义安装

### 4.1 自定义安装前缀

如果不希望装到 `/opt/fw-context`，可指定前缀：

```bash
sudo ./install.sh --prefix /opt/my-python
# Python 将装到 /opt/my-python/python3.11/
```

### 4.2 用户级安装（无需 sudo）

```bash
./install.sh --user
```

此时 Python 独立构建装到 `~/.fw-context/python/python3.11/`，无需 root 权限。

> 注意：用户级安装时，`--user` 标志同时改变了 Python 安装路径和 venv 位置约定。
> venv 仍然位于 `~/.fw-context/.venv`。

### 4.3 使用系统已有的 Python 3.11

如果目标服务器已安装 Python ≥ 3.11（例如通过 deadsnakes PPA 或其他方式），
可以直接删除离线包中的 `python/python-standalone.tar.gz`，安装脚本会自动检测并使用
系统 Python。

```bash
# 删掉 Python 独立构建，强制使用系统 Python
rm python/python-standalone.tar.gz
sudo ./install.sh
```

---

## 5. 卸载

```bash
./install.sh --uninstall
```

会移除：
- `~/.local/bin/` 下的 fw-context 系列符号链接
- `~/.fw-context/`（含 venv 与所有缓存数据）
- `/opt/fw-context/python3.11/`（需要 sudo）

如需彻底清理 `/opt/fw-context/`，手动执行：

```bash
sudo rm -rf /opt/fw-context
```

---

## 6. 使用指南

### 6.1 在固件项目中初始化

```bash
cd /path/to/your/firmware/project
fw-context init
```

这会在项目目录下创建 `.fw-context/config.toml`。具体配置项参考上游文档
`docs/configuration.md`（需要联网获取，或随源代码仓库携带）。

### 6.2 构建语义索引

```bash
fw-context index --build
```

该命令会调用你项目的构建系统（Zephyr / PlatformIO / CMake / Makefile 等）生成
`compile_commands.json`，再用 libclang 解析生成语义索引。

### 6.3 启用 MCP 服务（给 AI 助手用）

fw-context 提供两个 MCP 服务器入口：

| 命令 | 用途 |
|------|------|
| `fw-context-mcp` | 主 MCP 服务器（stdio 模式），供 Claude Code、OpenCode 等 AI 助手调用 |
| `fw-context` | CLI 入口，用于初始化、索引、状态查询 |

在 AI 助手配置文件中添加 MCP server，例如 Claude Code 的 `.mcp.json`：

```json
{
  "mcpServers": {
    "fw-context": {
      "command": "fw-context-mcp",
      "args": []
    }
  }
}
```

或在 Claude Code 项目根目录执行 `fw-context init`，它会自动检测并写入配置。

### 6.4 启用缓存服务器（可选）

如果你的项目很大或多台机器共享索引，可以启动 cache-server：

```bash
fw-cache-server --help     # 查看启动参数
fw-cache-admin --help      # 管理工具
```

cache-server 依赖 fastapi / uvicorn / asyncpg，已包含在离线包中。

### 6.5 Ollama（可选，用于智能搜索）

如果希望使用 `smart_search` / `explain_symbol` 工具，需要额外部署 Ollama。
Ollama 体积较大（数 GB），未包含在本离线包中。请参考
[Ollama 官方文档](https://ollama.com) 离线安装。如果不需要 LLM 增强，
可在 `.fw-context/config.toml` 中设置：

```toml
[llm]
enabled = false
```

---

## 7. 常见问题

### Q1. 安装时报 `glibc 版本过低`？

离线包内 wheel 默认按 manylinux_2_17 (glibc 2.17) 标准打包，Ubuntu 20.04 的 glibc 2.31 完全满足。
若个别 wheel 使用了较新的 manylinux tag，脚本会发出警告但不阻断安装。
如运行时出现 `GLIBC_2.xx not found`，请反馈具体 wheel 名以补充打包。

### Q2. 启动时报 `libclang.so: cannot open shared object file`？

检查 venv 内的 `site-packages/clang/native/libclang.so` 是否存在：

```bash
ls ~/.fw-context/.venv/lib/python3.11/site-packages/clang/native/
```

若缺失，可能是 libclang wheel 未正确安装，重跑 `sudo ./install.sh` 即可。

### Q3. 索引构建时报 `clang.cindex: libclang` 错误？

这通常是系统级 clang 缺失导致宏展开失败。安装系统 clang（见第 2 节），或
在 `.fw-context/config.toml` 中关闭宏展开：

```toml
[index]
expand_macros = false
```

### Q4. 如何更新到新版本？

从有网络的机器上重新运行打包脚本 `packaging/package.sh`，会增量更新 wheels
（仅下载新版本），然后整体替换服务器上的离线包目录，再次运行 `./install.sh` 即可。

### Q5. 如何在多用户服务器上共享安装？

默认 venv 装在用户家目录 `~/.fw-context/`，每个用户独立。如希望全局共享，
可将 venv 放到 `/opt/fw-context/venv`，符号链接由各用户自行创建。手动操作：

```bash
sudo /opt/fw-context/python3.11/bin/python3.11 -m venv /opt/fw-context/venv --copies
sudo /opt/fw-context/venv/bin/pip install --no-index --find-links wheels/ \
    -r requirements.txt wheels/fw_context_mcp-*.whl
# 各用户：
ln -s /opt/fw-context/venv/bin/fw-context ~/.local/bin/fw-context
ln -s /opt/fw-context/venv/bin/fw-context-mcp ~/.local/bin/fw-context-mcp
```

---

## 8. 打包工具链说明（维护者参考）

离线包由 `packaging/package.sh` 在 WSL Ubuntu 22.04 中生成。打包流程：

1. 使用 `uv pip compile` 锁定 `pyproject.toml` 的全部运行时依赖（含 `cache-server` extras）为最新版本
2. 使用 `uv pip download` 增量下载 wheel 到 `wheels/`（已存在的同名 wheel 自动跳过）
3. 清理 `wheels/` 中已不在 lock 文件中的旧版本 wheel
4. `uv build` 构建 fw-context-mcp 自身的 wheel + sdist
5. 从 GitHub 下载 Python 3.11 独立构建（python-build-standalone）和 uv 二进制
6. 生成 `manifest.sha256` 与 `requirements.txt`

重新打包命令：

```bash
cd /path/to/fw-context-mcp/packaging
./package.sh              # 增量更新
./package.sh --refresh    # 强制重新下载所有 wheel
./package.sh --tar        # 同时产出 .tar.gz 便于传输
```

如需修改依赖范围（例如不装 cache-server extras），编辑 `package.sh` 中的
`--extra cache-server` 参数即可。
