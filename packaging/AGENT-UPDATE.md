# Agent 更新指令：fw-context-mcp 离线更新

> **本文件供 AI agent 自动读取执行。** 你（agent）的目标是将当前 Linux 服务器上已安装的 fw-context-mcp 更新到离线包中的新版本，并在更新成功后报告结果。请严格按照以下步骤执行，不要跳过任何检查。
>
> **核心原则**：
> - **用户级安装，不使用 sudo**：所有文件在用户家目录下（`~/.fw-context/` 和 `~/.local/bin/`）
> - 所有命令验证使用**绝对路径**，不依赖 PATH 是否正确配置
> - 遇到不确定的情况时，优先检查退出码而非输出文本
> - 不要无限等待任何命令；对可能阻塞的命令使用 `timeout` 包裹
> - **配置保护**：`~/.fw-context/config.toml` 不会被覆盖，用户自定义设置完全保留
> - **索引保护**：`~/.fw-context/` 下的 `.db` 索引文件不会被删除，更新后通常无需重建

---

## 0. 概述

你即将**更新**已安装的 **fw-context-mcp** —— 一个面向嵌入式 C/C++ 固件的语义代码智能 MCP 服务器。

离线包已包含全部所需文件（Python 解释器、wheel 依赖、安装脚本），**无需联网**，**无需 root 权限**。

更新方式：通过 `install.sh --user` 脚本完成。脚本内置**跳过逻辑**：
- **依赖**：计算 `requirements.txt` 的 SHA256 hash，与上次安装时保存的 hash 对比。一致则跳过，不一致则执行安装
- **项目 wheel**：计算 wheel 文件的 SHA256 hash，与上次保存的 hash 对比。一致则跳过，不一致则执行重装
- **Python 解释器**：如已安装则跳过

因此，更新过程通常比首次安装快得多——只有真正变化的组件才会被重新安装。

---

## 1. 前置条件检查

### 1.1 确认已安装旧版本

```bash
OLD_VERSION=""
if [ -x "$HOME/.local/bin/fw-context" ]; then
    OLD_VERSION="$("$HOME/.local/bin/fw-context" --version 2>/dev/null | head -1)"
fi
echo "当前版本: ${OLD_VERSION:-未安装}"
```

- 如果输出"未安装"，说明 fw-context-mcp 尚未安装，**这不是更新场景**。停止并告知用户："fw-context-mcp 尚未安装，请使用全新安装流程（参考 AGENT-INSTALL.md）"
- 如果输出版本号，记录为 `OLD_VERSION`，继续

### 1.2 确认离线包存在

```bash
test -f fw-context-offline-bundle.tar.gz || echo "MISSING: fw-context-offline-bundle.tar.gz"
```

- 如果输出 `MISSING`，**停止**并报告："未找到离线包 fw-context-offline-bundle.tar.gz"

### 1.3 操作系统与架构

```bash
uname -s   # 必须输出 Linux
uname -m   # 必须输出 x86_64
```

- 不满足则**停止**并报告

---

## 2. 解析新版本号

从离线包中提取新版本号，用于后续对比和报告：

```bash
# 清理可能残留的旧解压目录
rm -rf fw-context-offline-bundle

tar xzf fw-context-offline-bundle.tar.gz
test -d fw-context-offline-bundle || { echo "ERROR: 解压失败"; exit 1; }

NEW_VERSION=""
# 从 wheel 文件名提取版本号: fw_context_mcp-0.25.8-py3-none-any.whl → 0.25.8
NEW_VERSION="$(ls fw-context-offline-bundle/wheels/fw_context_mcp-*.whl 2>/dev/null \
    | head -1 | sed 's/.*fw_context_mcp-\([0-9.]*\)-.*/\1/')"
echo "新版本: ${NEW_VERSION}"

if [ -z "$NEW_VERSION" ]; then
    echo "ERROR: 无法从 wheel 文件名解析版本号"
    exit 1
fi
```

**版本对比**：
- 如果 `OLD_VERSION` 中的版本号与 `NEW_VERSION` **不同**（如旧版 `0.24.0`、新版 `0.25.8`），说明确实需要更新，继续
- 如果版本号**相同**，说明已是最新版本。仍可继续执行（跳过逻辑会自动处理），但报告中注明"版本未变化"

---

## 3. 校验离线包完整性

```bash
cd fw-context-offline-bundle
./install.sh --verify
VERIFY_RC=$?
cd ..
echo "verify exit code: ${VERIFY_RC}"
```

- 退出码为 0：校验通过，继续
- 退出码非 0：**停止**并报告："离线包完整性校验失败，请重新拷贝离线包"

---

## 4. 执行更新

```bash
cd fw-context-offline-bundle
timeout 300 ./install.sh --user
INSTALL_RC=$?
cd ..
echo "install exit code: ${INSTALL_RC}"
```

- 退出码为 0：安装成功，继续验证
- 退出码非 0：**停止**并报告错误信息（附上 install.sh 的完整输出）

### 4.1 观察跳过日志

安装过程中，注意观察以下日志输出（跳过逻辑正常工作的表现）：

**预期会跳过的部分**（如果对应文件未变化）：
- `Python 已安装于 ...，跳过` — Python 解释器已存在
- `依赖未变化 (requirements.txt hash 一致)，跳过依赖安装` — 依赖列表未变
- `项目 wheel 未变化 (hash 一致)，跳过安装` — 项目代码未变（版本相同时）

**预期会执行的部分**（版本升级时的正常表现）：
- `安装依赖 (requirements.txt) ...` — 依赖列表有变化（如新增了包）
- `安装项目 wheel: fw_context_mcp-<新版本>-py3-none-any.whl` — 项目代码有更新
- `fw-context-mcp 安装完成` — 安装成功

> **注意**：无论跳过还是执行，只要退出码为 0 就算成功。

### 4.2 安装失败处理

如果安装失败，常见原因：

| 错误信息 | 原因 | 处理方式 |
|---------|------|---------|
| `完整性校验失败` | 离线包文件损坏 | 重新拷贝离线包（**不可重试**） |
| `No space left` | 磁盘空间不足 | 清理磁盘后重试 |
| `bad interpreter` | CRLF 行尾 | `sed -i 's/\r$//' install.sh` 后重试 |
| `timed out` | 安装过程卡住 | `rm -rf ~/.fw-context/.venv` 后重试（会丢失已安装依赖，需完整重装） |
| `ImportError` / `ModuleNotFoundError`（更新后） | 旧 venv 中残留包与新版本冲突 | `rm -rf ~/.fw-context/.venv` 后重新运行 `install.sh --user`（完整重建 venv） |

**重试策略**：
- 最多重试 2 次
- 重试前清理 venv：`rm -rf ~/.fw-context/.venv`（注意：这会导致下次安装从头开始，跳过逻辑失效）
- 如果 2 次后仍失败，停止并向用户报告完整错误日志

> **跨大版本更新提示**：从 0.22.x/0.23.x 等较旧版本更新时，旧 venv 中可能残留新版本不需要的包。通常不影响功能，但如果更新后遇到 `ImportError`，删除 venv 后重新安装即可彻底解决。

---

## 5. 验证更新成功

### 5.1 版本检查

```bash
~/.local/bin/fw-context --version; VERSION_RC=$?
echo "version exit code: ${VERSION_RC}"
```

- 退出码必须为 0
- 输出必须包含 `NEW_VERSION`（§2 中解析的版本号）
- 如果版本不匹配，更新失败

### 5.2 帮助检查

```bash
~/.local/bin/fw-context --help >/dev/null 2>&1
echo "help exit code: $?"
```

- 退出码必须为 0

### 5.3 符号链接检查

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

### 5.4 依赖导入检查

```bash
~/.fw-context/.venv/bin/python -c "import clang.cindex, pysqlite3, sqlite_vec, watchfiles, httpx, mcp, tomli_w; print('core deps OK')"
```

- 输出必须为 `core deps OK`
- `tomli_w` 是 0.25.x 新增依赖，§5.7 的配置修复依赖此包

```bash
~/.fw-context/.venv/bin/python -c "import fastapi, uvicorn, asyncpg; print('cache-server deps OK')" 2>/dev/null \
    && echo "cache-server extras: 可用" \
    || echo "cache-server extras: 不可用"
```

### 5.5 MCP 服务器启动检查

```bash
timeout 30 ~/.local/bin/fw-context-mcp </dev/null >/tmp/mcp_check.out 2>&1
MCP_RC=$?
echo "mcp exit code: ${MCP_RC}"
head -10 /tmp/mcp_check.out
rm -f /tmp/mcp_check.out
```

- 退出码为 124（timeout）或 0：**正常**
- 输出包含 `ModuleNotFoundError`、`ImportError`、`Traceback`：**失败**
- 输出包含 `Project not initialized` 或 `No project configured`：**正常**（当前目录未初始化项目，不影响 MCP 服务器本身的完整性。只要没有 ImportError/Traceback 即可）

### 5.6 配置文件检查

确认用户配置未被破坏：

```bash
if [ -f "$HOME/.fw-context/config.toml" ]; then
    echo "config.toml: 存在（已保留）"
    # 验证配置可正常加载
    ~/.fw-context/.venv/bin/python -c "
from fw_context_mcp.config.settings import load
cfg = load()
print(f'llm.enabled={cfg.llm.enabled}')
print(f'llm.ollama_url={cfg.llm.ollama_url}')
print(f'llm.embed_model={cfg.llm.embed_model}')
print(f'llm.model={cfg.llm.model}')
print(f'llm.chat_api_base={cfg.llm.chat_api_base}')
" 2>&1
else
    echo "config.toml: 不存在（首次使用或未配置）"
fi
```

如果配置加载失败（输出包含 `Traceback` 或 `Error`），可能是旧版 config.toml 格式与新版本不兼容。记录错误信息，在报告中标注 `[!] 配置加载失败`。

如果 config.toml 不存在（首次使用或未配置），§5.7 的自动修复会在需要时创建它。

### 5.7 Embedding 模型兼容性检查（关键）

> **背景**：从 0.25.x 起，`embed_model` 的默认值从 `"mxbai-embed-large:latest"` 变为 `""`（空字符串 = 自动检测）。在 CPU 服务器上，自动检测会选择 `qwen3-embedding:0.6b`，但离线服务器上安装的模型是 `mxbai-embed-large:latest`。如果 config.toml 没有显式设置 `embed_model`，更新后 semantic_search 会因模型不存在而失败。
>
> **本步骤检查并自动修复此问题**，使用项目自带的 `_toml_editor.set_key()` 修改配置（与 `fw-context init` 使用相同机制）。

**运行检查脚本**：

```bash
~/.fw-context/.venv/bin/python <<'PYEOF'
import os, tomllib, logging
from pathlib import Path
from fw_context_mcp.config.settings import load
from fw_context_mcp.config._toml_editor import set_key

# 抑制 load() 在 config 损坏时打印的 traceback（我们会自行检测并处理）
logging.disable(logging.CRITICAL)

# 1. 读取 resolved embed_model（load 会调用 resolve_embed_model）
cfg = load()
resolved_model = cfg.llm.embed_model or ""
print(f"resolved embed_model: '{resolved_model}'")

# 2. 读取 raw config.toml 检查 embed_model 是否显式设置
config_path = Path(os.path.expanduser("~/.fw-context/config.toml"))
embed_explicitly_set = False
config_parseable = True  # config.toml 是否可正常解析
if config_path.exists():
    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        embed_explicitly_set = "embed_model" in raw.get("llm", {})
    except Exception as e:
        config_parseable = False
        print(f"WARNING: config.toml 解析失败: {e}")
        print("  跳过自动修复（set_key 会清空无法解析的配置文件）")
print(f"embed_model explicitly set in config.toml: {embed_explicitly_set}")
print(f"config.toml parseable: {config_parseable}")

# 3. 检查 resolved model 是否已安装（通过文件系统 manifest，不依赖 serve 运行）
def model_installed(model_name_tag):
    """Check if model manifest exists on disk."""
    if not model_name_tag:
        return False
    name, _, tag = model_name_tag.partition(":")
    tag = tag or "latest"
    manifest = Path.home() / ".ollama/models/manifests/registry.ollama.ai/library" / name / tag
    return manifest.exists()

resolved_installed = model_installed(resolved_model)
mxbai_installed = model_installed("mxbai-embed-large:latest")
print(f"resolved model installed on disk: {resolved_installed}")
print(f"mxbai-embed-large installed on disk: {mxbai_installed}")

# 4. 决定是否需要修复
if resolved_installed:
    print("RESULT: embed_model OK (model is installed)")
elif not config_parseable:
    # config.toml 损坏——不能调 set_key（会清空文件），让用户手动处理
    print("WARNING: config.toml 无法解析，跳过自动修复")
    print("  请手动修复 config.toml 后设置 embed_model = \"mxbai-embed-large:latest\"")
elif embed_explicitly_set:
    # 用户显式设置了一个未安装的模型——尊重用户选择，仅警告
    print(f"WARNING: embed_model='{resolved_model}' is explicitly set but not installed on disk")
    print("  User explicitly configured this model — not overriding.")
    print("  If this is unintended, run:")
    print("    fw-context configure-llm  # or manually edit ~/.fw-context/config.toml")
elif mxbai_installed:
    # embed_model 未显式设置（使用默认/自动检测），且自动检测的模型未安装，
    # 但 mxbai-embed-large 已安装 → 自动修复
    set_key(config_path, "llm", "embed_model", "mxbai-embed-large:latest")
    print(f"AUTOFIX: embed_model set to 'mxbai-embed-large:latest' in {config_path}")
    print("  (was using auto-detect which selected an unavailable model)")
else:
    print("WARNING: No embedding model available on disk")
    print("  Install the Ollama offline bundle (contains mxbai-embed-large)")
PYEOF
```

> **安全保证**：
> - 如果 `embed_model` 在 config.toml 中**显式设置**了，即使模型未安装也**不会覆盖**（尊重用户选择，仅警告）
> - 只有当 `embed_model` **未显式设置**（使用默认/自动检测）且自动检测的模型未安装时，才自动修复
> - 修复使用项目自带的 `set_key()` 函数（与 `fw-context init` 使用相同机制），原子写入，不会产生损坏的配置文件
> - 模型安装检查通过**文件系统 manifest 路径**，不依赖 Ollama serve 是否运行
> - **已知限制**：`set_key()` 使用 `tomli_w` 重写 TOML 文件，**不保留注释**。所有 key-value 配置值会保留，但 `#` 注释行会丢失。这与 `fw-context init` 的行为一致。如果 config.toml 中有重要注释，建议在更新前手动备份：`cp ~/.fw-context/config.toml ~/.fw-context/config.toml.bak`

### 5.8 Ollama 状态检查（如已安装）

更新 fw-context 不影响 Ollama，但确认 embedding 功能仍可用：

```bash
if [ -x "$HOME/.local/bin/ollama" ]; then
    echo "ollama: $("$HOME/.local/bin/ollama" --version 2>/dev/null | head -1)"
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "ollama serve: 运行中"

        # 快速验证 embedding 请求可用（--max-time 30 防止模型首次加载卡住）
        EMBED_TEST=$(curl -sf --max-time 30 -H "Content-Type: application/json" http://localhost:11434/api/embed \
            -d '{"model":"mxbai-embed-large:latest","input":"test"}' 2>/dev/null \
            | grep -o '"embedding":\[' | head -1)
        if [ -n "$EMBED_TEST" ]; then
            echo "embedding 请求: 成功"
        else
            echo "embedding 请求: 失败（模型可能未加载）"
        fi
    else
        echo "ollama serve: 未运行（需手动启动: nohup ollama serve > ~/.ollama/ollama.log 2>&1 &）"
    fi
else
    echo "ollama: 未安装"
fi
```

---

## 6. 更新成功报告

所有验证步骤通过后，向用户输出以下格式的报告：

```
更新成功：fw-context-mcp

版本变更:
  fw-context-mcp: <旧版本> → <新版本>

配置状态:
  ~/.fw-context/config.toml: 已保留（用户自定义设置未受影响）

验证结果:
  [x] 版本: <新版本>
  [x] 帮助: 正常
  [x] 符号链接: 4/4 可执行
  [x] 核心依赖导入: 正常
  [x] cache-server 扩展: <可用 / 不可用>
  [x] MCP 服务器: 正常启动
  [x] Ollama: <版本> (<运行中 / 未运行>)
  [x] 配置文件: 已保留
  [x] Embedding 模型: mxbai-embed-large:latest (<已配置 / 已自动修复 / 需处理>)

说明:
  - 配置文件已保留，用户自定义设置未受影响
  - <如自动修复了 embed_model，注明: embed_model 已自动设置为 mxbai-embed-large:latest>
  - 已有索引数据保留，通常无需重建
  - 如 MCP 工具行为异常，可能是旧索引与新版本不兼容，可运行 fw-context index --build 重建
```

如果版本未变化（旧版本 == 新版本），在报告中注明：
```
说明:
  - 版本未变化，离线包与当前安装版本一致
  - 安装脚本已执行（跳过逻辑已处理），环境状态确认正常
```

### 6.1 部分失败报告

如果某些验证步骤失败，仍输出报告但标注失败项：

```
更新完成（有警告）：fw-context-mcp

版本变更:
  fw-context-mcp: <旧版本> → <新版本>

验证结果:
  [x] 版本: <新版本>
  [x] 符号链接: 4/4 可执行
  [!] <失败项>: <错误信息>
  [x] ...

需要处理:
  1. <失败项的修复建议>
```

---

## 7. 更新后注意事项

### 7.1 重启 MCP 服务

如果 MCP 服务器正在运行（如通过 Claude Desktop、Cursor 等客户端注册），更新后需要**重启 MCP 服务**才能加载新代码：

```bash
# 查看是否有运行中的 fw-context-mcp 进程
pgrep -fa fw-context-mcp

# 如果有，需要重启对应的 AI 工具客户端，或重新注册 MCP 服务器
fw-context mcp register
```

### 7.2 索引兼容性

- 更新 fw-context-mcp **不会删除**已有索引数据（`~/.fw-context/` 下的 `.db` 文件）
- **Schema 自动迁移**：新版本首次连接旧索引时，`ensure_schema()` 会自动运行 schema migration（加列、数据迁移），无需手动干预
- **0.30.x 索引内容语义变化**：从 0.30.0 起，`files.content` 和 `symbols.source` 存储的是 **#if 分支过滤后**的活跃代码（通过 `build_configs.row_format` 标记）。旧版本构建的索引会被 `check_structural_staleness` 自动检测为 stale，MCP 服务器会触发后台重建。**无需手动干预**，但更新后首次查询可能有重建延迟；如需立即重建：

```bash
cd /path/to/your/firmware/project
fw-context index --build
fw-context index --embeddings
```

- **Embedding 兼容性**：如果 `embed_model` 未变化（仍为 `mxbai-embed-large:latest`），旧 embeddings 数据仍然可用，无需重新生成
- 如果 `embed_model` 从 mxbai-embed-large 变成了其他模型（如 qwen3-embedding），旧 embeddings 会因维度不匹配而失效，需要重新生成：

```bash
cd /path/to/your/firmware/project
fw-context index --embeddings   # 仅重新生成 embeddings，不重建符号索引
```

- 如果遇到其他异常（schema 迁移后数据不完整等），可完全重建索引：

```bash
cd /path/to/your/firmware/project
fw-context index --build
fw-context index --embeddings
```

### 7.3 配置项变化（0.24.x → 0.30.x）

新版本对配置有以下变化，`config.toml` 中缺失的字段会使用新默认值，**无需手动迁移**：

#### LLM 配置（`[llm]`）

| 字段 | 0.24.x | 0.27.x | 说明 |
|------|--------|--------|------|
| `embed_model` | `"mxbai-embed-large:latest"` | `""` (自动检测) | **已由 §5.7 自动修复**。空值会在 CPU 上选 qwen3-embedding:0.6b，但离线服务器只有 mxbai-embed-large |
| `embed_dim` | (不存在) | `None` (自动) | 新增字段，通常无需设置 |
| `reranker_model` | (不存在) | `None` | 新增字段，用于 LLM 重排序，可选 |
| `ollama_max_concurrent` | (不存在) | `1` | 新增字段，控制 Ollama 并发请求数，默认 1（串行） |
| `auto_pull` | (不存在) | `False` | 新增字段，控制是否自动拉取未安装的模型。离线环境保持 `False` |

#### 构建配置（`[build]`）

| 字段 | 0.24.x | 0.27.x | 说明 |
|------|--------|--------|------|
| `compile_commands` | `compile_commands.json` | `.fw-context/build/compile_commands.json` | 默认路径改为 gitignored 子目录。**已初始化的项目不受影响**（显式设置的值会保留） |
| `timeout` | `600` | `7200` | 构建超时从 10 分钟增至 2 小时 |
| `[[build.variants]]` | (不存在) | (新增) | 多构建变体支持（Zephyr sysbuild、多板子）。可选，不配置则行为不变 |
| `source_dir` / `sysbuild` | (不存在) | (新增) | Zephyr sysbuild 支持，可选 |

#### 调用图配置（`[call_graph]`，新增 section）

| 字段 | 说明 |
|------|------|
| `[call_graph.dispatch_bridges]` | 自定义 RTOS 调度桥接映射。内置 mbed-os/Zephyr/FreeRTOS 默认值，通常无需配置 |

如需查看当前完整配置：

```bash
~/.fw-context/.venv/bin/python -c "
from fw_context_mcp.config.settings import load
cfg = load()
print(f'llm.enabled={cfg.llm.enabled}')
print(f'llm.ollama_url={cfg.llm.ollama_url}')
print(f'llm.embed_model={cfg.llm.embed_model}')
print(f'llm.model={cfg.llm.model}')
print(f'llm.chat_api_base={cfg.llm.chat_api_base}')
print(f'llm.stream={cfg.llm.stream}')
print(f'llm.reranker_model={getattr(cfg.llm, \"reranker_model\", None)}')
print(f'llm.auto_pull={getattr(cfg.llm, \"auto_pull\", None)}')
print(f'build.timeout={cfg.build.timeout}')
print(f'build.compile_commands={cfg.build.compile_commands}')
"
```

### 7.4 Ollama 无需更新

本次更新仅涉及 fw-context-mcp，Ollama 二进制和模型文件不受影响，无需重新安装或重启 serve。
