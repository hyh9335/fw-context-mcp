# Agent 安装指令：fw-context-mcp + Ollama 离线安装/更新（总入口）

> **本文件供 AI agent 自动读取执行。** 你（agent）的目标是在当前 Linux 服务器上完成 fw-context-mcp 和 Ollama 的离线安装或更新，并在完成后报告结果。
>
> **核心原则**：
> - **用户级安装，不使用 sudo**：所有文件装在用户家目录下，无需 root 权限
> - **支持全新安装和更新**：本指令会自动检测已安装的组件，两种场景都适用
> - **配置保护**：更新时保留用户现有配置，仅补充缺失的必需字段
> - 所有命令验证使用**绝对路径**，不依赖 PATH 是否正确配置
> - 遇到不确定的情况时，优先检查退出码而非输出文本
> - 不要无限等待任何命令；对可能阻塞的命令使用 `timeout` 包裹
> - **两个子提示词位于各自离线包解压后的目录内**，按本指令引用执行

---

## 0. 概述

你即将安装/更新两个组件：

1. **fw-context-mcp** —— 面向嵌入式 C/C++ 固件的语义代码智能 MCP 服务器
2. **Ollama** —— 本地 LLM 运行时，用于 embedding 语义搜索

离线包已包含全部所需文件，**无需联网**，**无需 root 权限**。

### 安装顺序

**先 fw-context-mcp，后 Ollama**。Ollama 是 embedding 功能的前提，但安装本身无依赖关系。Ollama 安装后需启动 `ollama serve` 服务。

### 安装位置（全部用户级）

| 组件 | 路径 |
|------|------|
| fw-context-mcp Python | `~/.fw-context/python/python3.11/` |
| fw-context-mcp venv | `~/.fw-context/.venv/` |
| fw-context-mcp 命令 | `~/.local/bin/`（fw-context, fw-context-mcp, fw-cache-server, fw-cache-admin） |
| fw-context-mcp 配置 | `~/.fw-context/config.toml` |
| Ollama 二进制 | `~/.local/bin/ollama` |
| Ollama lib | `~/.local/lib/ollama/`（llama-server + CPU 库） |
| Ollama 模型 | `~/.ollama/models/` |
| Ollama 日志 | `~/.ollama/ollama.log` |

### 离线包内容

当前工作目录应包含 3 个文件：

| 文件 | 说明 |
|------|------|
| `AGENT-INSTALL-ALL.md` | 本文件（总入口） |
| `fw-context-offline-bundle.tar.gz` | fw-context-mcp 离线包（解压后含 `AGENT-INSTALL.md` + `install.sh` + wheels + Python） |
| `ollama-offline-bundle.tar.gz` | Ollama 离线包（解压后含 `AGENT-INSTALL-OLLAMA.md` + `install-ollama.sh` + 模型） |

---

## 1. 前置条件检查

在执行安装前，**必须**逐项检查以下条件。如果任何一项不满足，停止并报告具体原因。

### 1.1 操作系统与架构

```bash
uname -s   # 必须输出 Linux
uname -m   # 必须输出 x86_64
```

- 如果操作系统不是 Linux，**停止**并报告："不支持的平台: <输出值>，仅支持 Linux x86_64"
- 如果架构不是 x86_64，**停止**并报告："不支持的架构: <输出值>，仅支持 x86_64"

### 1.2 必要的系统命令

```bash
for cmd in bash tar sha256sum curl ldd awk find mktemp readlink timeout head; do
    command -v "$cmd" >/dev/null 2>&1 || echo "MISSING: $cmd"
done
```

- 如果输出任何 `MISSING:`，**停止**并报告缺少的命令

### 1.3 glibc 版本

```bash
ldd --version | head -1 | awk '{print $NF}'
```

- glibc 版本建议 >= 2.31（对应 Ubuntu 20.04）
- 如果低于 2.31，发出警告但**不停止**（实际 wheel 按 manylinux_2_17 标准打包，glibc >= 2.17 即可运行）

### 1.4 磁盘空间与家目录可写性

```bash
df -h ~ /tmp
test -w ~ || echo "STOP: 家目录不可写"
```

- 家目录至少需要 2 GB（fw-context ~500MB + Ollama+模型 ~1.5GB）
- `/tmp` 至少需要 200 MB（临时解压用）
- 如果空间不足或家目录不可写，**停止**并报告

### 1.5 离线包文件存在性

```bash
for f in AGENT-INSTALL-ALL.md fw-context-offline-bundle.tar.gz ollama-offline-bundle.tar.gz; do
    test -f "$f" || echo "MISSING: $f"
done
```

- 如果输出任何 `MISSING:`，**停止**并报告缺失的文件

---

## 2. 检测已安装组件

在执行安装前，**必须**检测当前服务器上是否已安装相关组件。这决定后续是全新安装还是更新场景。

```bash
# ── 检测 fw-context-mcp ──
FW_INSTALLED=0
FW_OLD_VERSION=""
if [ -x "$HOME/.local/bin/fw-context" ]; then
    FW_OLD_VERSION="$("$HOME/.local/bin/fw-context" --version 2>/dev/null | head -1)"
    if [ -n "$FW_OLD_VERSION" ]; then
        FW_INSTALLED=1
        echo "fw-context 已安装: ${FW_OLD_VERSION}"
    fi
fi

# ── 检测 Ollama 二进制 ──
OLLAMA_INSTALLED=0
OLLAMA_OLD_VERSION=""
if [ -x "$HOME/.local/bin/ollama" ]; then
    OLLAMA_OLD_VERSION="$("$HOME/.local/bin/ollama" --version 2>/dev/null | head -1)"
    if [ -n "$OLLAMA_OLD_VERSION" ]; then
        OLLAMA_INSTALLED=1
        echo "ollama 已安装: ${OLLAMA_OLD_VERSION}"
    fi
fi

# ── 检测 Ollama serve 运行状态 ──
OLLAMA_SERVE_RUNNING=0
OLLAMA_SERVE_PID=""
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_SERVE_RUNNING=1
    OLLAMA_SERVE_PID="$(pgrep -f 'ollama serve' | head -1)"
    echo "ollama serve 运行中 (pid=${OLLAMA_SERVE_PID})"
fi

# ── 检测模型是否已安装（用于跳过 639MB 复制） ──
MODEL_INSTALLED=0
if [ -f "$HOME/.ollama/models/manifests/registry.ollama.ai/library/mxbai-embed-large/latest" ]; then
    MODEL_INSTALLED=1
    echo "mxbai-embed-large 模型已安装"
fi

# ── 检测 config.toml 是否存在 ──
CONFIG_EXISTS=0
if [ -f "$HOME/.fw-context/config.toml" ]; then
    CONFIG_EXISTS=1
    echo "config.toml 已存在"
fi

# ── 判断场景 ──
SCENARIO="fresh"
if [ "$FW_INSTALLED" -eq 1 ] || [ "$OLLAMA_INSTALLED" -eq 1 ]; then
    SCENARIO="update"
    echo "场景: 更新"
else
    echo "场景: 全新安装"
fi
```

**记录以上所有检测值**，后续步骤会使用，最终报告也会引用。

---

## 3. 清理旧残留并解压离线包

### 3.1 清理旧残留目录

之前可能在当前目录或用户根目录（`~/`）解压过旧版离线包，残留目录会：
1. 浪费磁盘空间（每个 bundle 约 500MB-660MB）
2. 导致 `tar xzf` 解压新包时，旧目录中多余的文件不会被删除，可能混入旧版 wheel 或脚本

**清理范围**：仅删除 bundle 解压目录（安装介质），**绝不触碰实际安装目录**（`~/.fw-context/`、`~/.local/bin/`、`~/.local/lib/ollama/`、`~/.ollama/`）。这些实际安装目录由安装脚本管理，清理残留不影响已安装的软件、配置和索引数据。

```bash
# ── 清理旧残留目录 ──
# bundle 目录名列表
BUNDLE_DIRS="fw-context-offline-bundle ollama-offline-bundle"

CLEANED_DIRS=""
for dir in $BUNDLE_DIRS; do
    # 当前工作目录（如 ~/offline-install/）下的旧残留
    if [ -d "$dir" ]; then
        echo "清理当前目录旧残留: $dir"
        rm -rf "$dir"
        CLEANED_DIRS="${CLEANED_DIRS} ${dir}"
    fi
    # 用户根目录（~/）下的旧残留（之前在 ~/ 解压的情况）
    # 排除当前目录就是 ~/ 的情况，避免重复
    if [ -d "$HOME/$dir" ] && [ "$(cd "$HOME" && pwd)" != "$(pwd)" ]; then
        echo "清理 ~/ 旧残留: ~/$dir"
        rm -rf "$HOME/$dir"
        CLEANED_DIRS="${CLEANED_DIRS} ~/$dir"
    fi
done

if [ -n "$CLEANED_DIRS" ]; then
    echo "已清理旧残留目录:${CLEANED_DIRS}"
else
    echo "无旧残留目录需清理"
fi
```

> **安全保证**：此步骤只删除 `fw-context-offline-bundle/` 和 `ollama-offline-bundle/` 这两个目录（无论在当前目录还是 `~/`）。不会删除：
> - `~/.fw-context/`（含 config.toml、venv、索引数据）
> - `~/.local/bin/`（含命令符号链接）
> - `~/.local/lib/ollama/`（含 llama-server + CPU 库）
> - `~/.ollama/`（含模型文件、日志）

### 3.2 解压新离线包

```bash
# 解压 fw-context-mcp 离线包
tar xzf fw-context-offline-bundle.tar.gz
test -d fw-context-offline-bundle || echo "ERROR: 解压后未找到 fw-context-offline-bundle 目录"

# 解压 Ollama 离线包
tar xzf ollama-offline-bundle.tar.gz
test -d ollama-offline-bundle || echo "ERROR: 解压后未找到 ollama-offline-bundle 目录"

# 确认子提示词存在
test -f fw-context-offline-bundle/AGENT-INSTALL.md || echo "ERROR: AGENT-INSTALL.md 缺失"
test -f ollama-offline-bundle/AGENT-INSTALL-OLLAMA.md || echo "ERROR: AGENT-INSTALL-OLLAMA.md 缺失"
```

- 如果输出任何 `ERROR:`，**停止**并报告

---

## 4. 安装/更新 fw-context-mcp

进入 `fw-context-offline-bundle/` 目录，**读取 `AGENT-INSTALL.md`**，并严格按其以下章节执行：

- §1 前置条件检查
- §2 校验离线包完整性
- §3 执行安装（使用 `./install.sh --user`）
- §4 验证安装成功
- §5 安装成功报告（将报告内容暂存，最终合并到本指令 §8）

**注意**：
- `install.sh --user` 是幂等的，全新安装和更新都使用相同命令
- 更新场景下，venv 会复用，依赖按 `--upgrade` 策略升级，项目 wheel 强制重装（`--force-reinstall`）
- 用户的 `~/.fw-context/config.toml` **不会被覆盖**，配置完全保留

**跳过逻辑（更新场景下自动生效）**：
- **依赖**：`install.sh` 会计算 `requirements.txt` 的 SHA256 hash，与上次安装时保存的 hash 对比。若一致，跳过整个依赖安装步骤（pip 逐包检查也省略）
- **项目 wheel**：`install.sh` 会计算 wheel 文件的 SHA256 hash，与上次保存的 hash 对比。若一致（代码未变化），跳过 `--force-reinstall`。若不一致（代码有更新），执行强制重装并更新 hash
- 首次安装时无 hash 文件，全部正常安装

安装完成后，记录新版本号：

```bash
FW_NEW_VERSION="$("$HOME/.local/bin/fw-context" --version 2>/dev/null | head -1)"
echo "fw-context 新版本: ${FW_NEW_VERSION}"
```

---

## 5. 安装/更新 Ollama

### 5.1 停止运行中的 serve（如运行中）

如果 §2 检测到 `OLLAMA_SERVE_RUNNING=1`，**必须**先停止 serve：

```bash
if [ -n "$OLLAMA_SERVE_PID" ]; then
    echo "停止 ollama serve (pid=${OLLAMA_SERVE_PID}) ..."
    kill "$OLLAMA_SERVE_PID" 2>/dev/null || true
    # 等待最多 10 秒确认已停止
    for i in $(seq 1 10); do
        if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "serve 已停止"
            break
        fi
        sleep 1
    done
    # 如果仍未停止，强制终止
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "serve 未响应 SIGTERM，强制终止 ..."
        pkill -9 -f "ollama serve" 2>/dev/null || true
        sleep 2
    fi
fi
```

- 如果 `OLLAMA_SERVE_RUNNING=0`（全新安装或 serve 未运行），跳过本步

### 5.2 跳过逻辑（更新场景下自动生效）

`install-ollama.sh` 内置了跳过逻辑，无需 agent 额外处理：

- **Ollama 二进制 + lib**：脚本计算 `ollama-cpu.tar.gz` 的 SHA256 hash，与上次安装时保存的 hash（`~/.ollama/.tarball_hash`）对比。若一致且二进制存在，跳过解压复制（节省约 5 秒）。若不一致，执行完整安装并更新 hash
- **模型文件**：脚本检查 `~/.ollama/models/manifests/.../mxbai-embed-large/latest` 是否存在。若存在，跳过 670MB 模型复制。若不存在，执行复制
- 首次安装时无 hash 文件且无 manifest，全部正常安装

### 5.3 执行安装

进入 `ollama-offline-bundle/` 目录，**读取 `AGENT-INSTALL-OLLAMA.md`**，并严格按其以下章节执行：

- §1 前置条件检查
- §2 校验离线包完整性
- §3 执行安装
- §4 验证安装成功（含 §4.1 版本检查、§4.2 启动 serve、§4.3 模型列表、§4.4 embedding 请求）

**重要**：
- **跳过** `AGENT-INSTALL-OLLAMA.md` 的 §5（配置 fw-context-mcp），配置由本指令 §6 统一处理
- §4.2 中 serve 启动验证是安装脚本的一部分，执行后会启动一个临时 serve。如果 §5.1 停止过 serve，这里会重新启动
- 安装完成后，serve 可能仍在运行（由子提示词 §4.2 启动）。记下当前 serve 状态

### 5.4 确保 serve 运行

安装完成后，确保 Ollama serve 正在运行（后续 embedding 验证和实际使用都需要）：

```bash
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "serve 未运行，启动 ..."
    nohup "$HOME/.local/bin/ollama" serve > "$HOME/.ollama/ollama.log" 2>&1 &
    SERVE_PID=$!
    # 等待最多 15 秒
    for i in $(seq 1 15); do
        if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "serve 启动成功 (pid=${SERVE_PID})"
            break
        fi
        sleep 1
    done
fi

# 最终确认
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "OLLAMA_SERVE_OK"
else
    echo "ERROR: serve 启动失败，检查日志: $HOME/.ollama/ollama.log"
    tail -20 "$HOME/.ollama/ollama.log"
fi
```

- 必须输出 `OLLAMA_SERVE_OK` 才能继续

记录新版本号：

```bash
OLLAMA_NEW_VERSION="$("$HOME/.local/bin/ollama" --version 2>/dev/null | head -1)"
echo "ollama 新版本: ${OLLAMA_NEW_VERSION}"
```

---

## 6. 统一配置

### 6.1 全局配置 `~/.fw-context/config.toml`

#### 全新安装场景（CONFIG_EXISTS=0）

创建 `~/.fw-context/config.toml`：

```bash
mkdir -p "$HOME/.fw-context"
cat > "$HOME/.fw-context/config.toml" <<'EOF'
[llm]
enabled = true
ollama_url = "http://localhost:11434"
embed_model = "mxbai-embed-large:latest"
EOF
```

#### 更新场景（CONFIG_EXISTS=1）

**仅补充缺失的必需字段，不覆盖已有设置**：

```bash
CONFIG_FILE="$HOME/.fw-context/config.toml"

# 检查并补充 [llm] 段
if ! grep -q '^\[llm\]' "$CONFIG_FILE" 2>/dev/null; then
    echo "" >> "$CONFIG_FILE"
    echo "[llm]" >> "$CONFIG_FILE"
fi

# 补充 enabled（如缺失）
if ! grep -qE '^\s*enabled\s*=' "$CONFIG_FILE"; then
    sed -i '/^\[llm\]/a enabled = true' "$CONFIG_FILE"
fi

# 补充 ollama_url（如缺失）
if ! grep -qE '^\s*ollama_url\s*=' "$CONFIG_FILE"; then
    sed -i '/^\[llm\]/a ollama_url = "http://localhost:11434"' "$CONFIG_FILE"
fi

# 补充 embed_model（如缺失）
if ! grep -qE '^\s*embed_model\s*=' "$CONFIG_FILE"; then
    sed -i '/^\[llm\]/a embed_model = "mxbai-embed-large:latest"' "$CONFIG_FILE"
fi

echo "配置检查完成（仅补充缺失字段，已有设置保留）"
```

**关键**：用户可能已自定义 `model`、`num_ctx`、`chat_api_base`、`stream` 等字段，这些**完全不动**。

### 6.2 Chat API 配置

`smart_search` 和 `explain_symbol` 功能需要 Chat API。先自动探测，探测失败再询问用户。

#### 6.2.1 自动探测 Chat API

```bash
CHAT_API_URL=""
CHAT_API_MODEL=""

# 探测常见端口
for port in 4000 8000 8080 11434; do
    resp=$(curl -sf "http://localhost:${port}/v1/models" 2>/dev/null) || continue
    # 解析第一个 model id（无 jq，用 grep+sed）
    model_id=$(echo "$resp" | grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')
    if [ -n "$model_id" ]; then
        CHAT_API_URL="http://localhost:${port}"
        CHAT_API_MODEL="$model_id"
        echo "探测到 Chat API: ${CHAT_API_URL} (model: ${CHAT_API_MODEL})"
        break
    fi
done
```

#### 6.2.2 探测成功 → 写入配置

如果 `CHAT_API_URL` 非空，写入 config.toml：

```bash
if [ -n "$CHAT_API_URL" ] && [ -n "$CHAT_API_MODEL" ]; then
    CONFIG_FILE="$HOME/.fw-context/config.toml"
    
    # 更新或添加 model
    if grep -qE '^\s*model\s*=' "$CONFIG_FILE"; then
        sed -i -E "s|^\s*model\s*=.*|model = \"${CHAT_API_MODEL}\"|" "$CONFIG_FILE"
    else
        sed -i "/^\[llm\]/a model = \"${CHAT_API_MODEL}\"" "$CONFIG_FILE"
    fi
    
    # 更新或添加 chat_api_base
    if grep -qE '^\s*chat_api_base\s*=' "$CONFIG_FILE"; then
        sed -i -E "s|^\s*chat_api_base\s*=.*|chat_api_base = \"${CHAT_API_URL}\"|" "$CONFIG_FILE"
    else
        sed -i "/^\[llm\]/a chat_api_base = \"${CHAT_API_URL}\"" "$CONFIG_FILE"
    fi
    
    echo "Chat API 配置已写入: model=${CHAT_API_MODEL}, chat_api_base=${CHAT_API_URL}"
    CHAT_API_STATUS="configured"
fi
```

> **注意**：更新场景下，如果 config.toml 已有 `model` 和 `chat_api_base`，上面的 sed 会覆盖它们。如果你希望保留用户已配置的值，请在执行前检查：
> ```bash
> # 如果用户已有 model 配置，不覆盖
> if ! grep -qE '^\s*model\s*=' "$CONFIG_FILE"; then
>     # 才写入探测到的 model
> fi
> ```
> **推荐策略**：全新安装时写入探测值；更新时仅当字段缺失才写入。

#### 6.2.3 探测失败 → 询问用户

如果 `CHAT_API_URL` 为空（自动探测未找到 Chat API），**询问用户**：

> 未检测到 Chat API。`smart_search` 和 `explain_symbol` 功能需要 Chat API（如 LiteLLM）。
> 
> 请提供以下信息，或回复 `skip` 跳过 chat 配置（`semantic_search` 仍可用）：
> - Chat API URL（如 `http://localhost:4000`）
> - 模型名称（如 `glm-5.2-fp8`）

根据用户响应：
- 用户提供 URL 和 model → 同 §6.2.2 写入 config.toml
- 用户回复 `skip` → 记录 `CHAT_API_STATUS="skipped"`，继续后续步骤

### 6.3 验证配置

```bash
echo "=== 当前 config.toml [llm] 段 ==="
grep -A20 '^\[llm\]' "$HOME/.fw-context/config.toml" 2>/dev/null | grep -vE '^\[' | head -15
```

确认以下字段存在且正确：
- `enabled = true`
- `ollama_url = "http://localhost:11434"`
- `embed_model = "mxbai-embed-large:latest"`
- `model` 和 `chat_api_base`（如已配置）

---

## 7. 端到端验证

执行以下全部验证步骤。所有步骤必须通过。

### 7.1 fw-context-mcp 版本

```bash
"$HOME/.local/bin/fw-context" --version 2>&1; RC=$?
echo "fw-context exit: ${RC}"
```

- 退出码必须为 0
- 输出应包含版本号

### 7.2 fw-context-mcp 符号链接完整性

```bash
for bin in fw-context fw-context-mcp fw-cache-server fw-cache-admin; do
    if [ -x "$HOME/.local/bin/$bin" ]; then
        echo "$bin: OK"
    else
        echo "$bin: BROKEN or MISSING"
    fi
done
```

- 4 个全部输出 OK

### 7.3 fw-context-mcp 核心依赖导入

```bash
"$HOME/.fw-context/.venv/bin/python" -c "import clang.cindex, pysqlite3, sqlite_vec, watchfiles, httpx, mcp; print('core deps OK')" 2>&1
```

- 必须输出 `core deps OK`

### 7.4 fw-context-mcp 服务器启动检查

```bash
timeout 30 "$HOME/.local/bin/fw-context-mcp" </dev/null >/tmp/mcp_check.out 2>&1
MCP_RC=$?
echo "mcp exit code: ${MCP_RC}"
head -10 /tmp/mcp_check.out
rm -f /tmp/mcp_check.out
```

- 退出码 124（timeout）或 0：正常
- 输出含 `ModuleNotFoundError`/`ImportError`/`Traceback`/`Segmentation fault`：失败

### 7.5 Ollama 版本

```bash
"$HOME/.local/bin/ollama" --version 2>&1; RC=$?
echo "ollama exit: ${RC}"
```

### 7.6 Ollama serve 运行

```bash
curl -sf http://localhost:11434/api/tags 2>&1 | head -c 300
echo
```

- 输出必须包含 `mxbai-embed-large`

### 7.7 Embedding 请求测试

```bash
EMBED_RESP=$(curl -sf http://localhost:11434/api/embed \
    -d '{"model":"mxbai-embed-large:latest","input":["hello world","test sentence"]}' 2>&1)
echo "$EMBED_RESP" | head -c 200
echo
echo "$EMBED_RESP" | grep -q '"embeddings"' \
    && echo "EMBED OK" \
    || echo "EMBED FAILED"
```

- 必须输出 `EMBED OK`

### 7.8 Chat API 测试（如已配置）

如果 §6.2 配置了 Chat API：

```bash
if [ -n "$CHAT_API_URL" ] && [ -n "$CHAT_API_MODEL" ]; then
    CHAT_RESP=$(curl -sf "${CHAT_API_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"${CHAT_API_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"say OK\"}],\"max_tokens\":10}" 2>&1)
    echo "$CHAT_RESP" | head -c 300
    echo
    echo "$CHAT_RESP" | grep -qE '"content"|"reasoning_content"' \
        && echo "CHAT OK" \
        || echo "CHAT FAILED"
fi
```

- 输出 `CHAT OK` 表示 Chat API 可用

### 7.9 fw-context-mcp LLM 配置检查

```bash
"$HOME/.fw-context/.venv/bin/python" -c "
from fw_context_mcp.config.settings import load_config
from fw_context_mcp.llm.ollama import check_setup
cfg = load_config()
setup = check_setup(cfg.llm)
print(f'enabled={cfg.llm.enabled}')
print(f'ollama_running={setup.get(\"ollama_running\")}')
print(f'embedding_installed={setup.get(\"embedding_installed\")}')
print(f'ollama_enabled={setup.get(\"ollama_enabled\")}')
print(f'model={cfg.llm.model}')
print(f'chat_api_base={cfg.llm.chat_api_base}')
" 2>&1
```

- `enabled` 应为 `True`
- `ollama_running` 应为 `True`
- `embedding_installed` 应为 `True`
- `ollama_enabled` 应为 `True`

---

## 8. 安装/更新成功报告

所有验证步骤通过后，根据场景输出报告。

### 8.1 全新安装报告

```
安装成功：fw-context-mcp <版本> + Ollama <版本>

安装路径:
  fw-context-mcp:
    Python:    ~/.fw-context/python/python3.11/
    venv:      ~/.fw-context/.venv/
    命令:      ~/.local/bin/{fw-context,fw-context-mcp,fw-cache-server,fw-cache-admin}
    配置:      ~/.fw-context/config.toml
  Ollama:
    二进制:    ~/.local/bin/ollama
    lib:       ~/.local/lib/ollama/ (llama-server + CPU 库)
    模型:      ~/.ollama/models/
    日志:      ~/.ollama/ollama.log

验证结果:
  [x] fw-context --version: <版本>
  [x] 符号链接: 4/4 可执行
  [x] 核心依赖导入: 正常
  [x] MCP 服务器: 正常启动
  [x] ollama --version: <版本>
  [x] serve 运行: 正常 (http://localhost:11434)
  [x] 模型列表: mxbai-embed-large:latest
  [x] embedding 请求: 成功 (1024 维)
  [x] LLM 配置: enabled=true, ollama_enabled=true, embedding_installed=true
  [x] Chat API: <已配置 / 已跳过> (<URL> / <model>)

下一步:
  1. 保持 ollama serve 运行
  2. cd /path/to/your/firmware/project
  3. fw-context init
  4. fw-context index --build
  5. fw-context index --embeddings
  6. 注册 MCP 服务器到 AI 工具: fw-context mcp register
```

### 8.2 更新报告

```
更新成功：fw-context-mcp + Ollama

版本变更:
  fw-context: <旧版本> → <新版本>
  Ollama:     <旧版本> → <新版本>

配置状态:
  ~/.fw-context/config.toml: 已保留（仅补充缺失字段）
  <Chat API: 已配置 / 已跳过 / 保留原有>

服务状态:
  ollama serve: 已重启 (pid=<新pid>)

验证结果:
  [x] fw-context --version: <新版本>
  [x] 符号链接: 4/4 可执行
  [x] 核心依赖导入: 正常
  [x] MCP 服务器: 正常启动
  [x] ollama --version: <新版本>
  [x] serve 运行: 正常 (http://localhost:11434)
  [x] 模型列表: mxbai-embed-large:latest
  [x] embedding 请求: 成功 (1024 维)
  [x] LLM 配置: enabled=true, ollama_enabled=true, embedding_installed=true
  [x] Chat API: <状态>

说明:
  - 配置文件已保留，用户自定义设置未受影响
  - 如版本未变化，说明已是最新版本
  - 已有索引数据 (~/.fw-context/ 下的 .db 文件) 保留，无需重建
```

### 8.3 部分失败报告

如果某些验证步骤失败，仍输出报告但标注失败项：

```
安装/更新完成（有警告）：fw-context-mcp + Ollama

<版本变更信息>

验证结果:
  [x] fw-context --version: <版本>
  [x] ...
  [!] <失败项>: <错误信息>
  [x] ...

需要处理:
  1. <失败项的修复建议>
```

---

## 9. 故障排除

### 9.1 Ollama serve 启动失败

```bash
# 检查日志
tail -50 "$HOME/.ollama/ollama.log"

# 常见原因:
# - 端口 11434 被占用: lsof -i :11434
# - lib/ollama/ 缺失: ls ~/.local/lib/ollama/llama-server
# - glibc 版本过低: ldd --version
```

### 9.2 fw-context-mcp 启动失败

```bash
# 检查 venv 完整性
"$HOME/.fw-context/.venv/bin/python" -c "import fw_context_mcp; print('OK')"

# 检查符号链接
ls -la "$HOME/.local/bin/fw-context"*

# 重新运行安装（幂等）
cd fw-context-offline-bundle/
./install.sh --user
```

### 9.3 Embedding 请求失败

```bash
# 确认 serve 运行
curl -sf http://localhost:11434/api/tags

# 确认模型已安装
ls ~/.ollama/models/manifests/registry.ollama.ai/library/mxbai-embed-large/latest

# 确认 llama-server 存在（CPU 包必需）
ls -la ~/.local/lib/ollama/llama-server
```

### 9.4 config.toml 配置问题

```bash
# 查看当前配置
cat "$HOME/.fw-context/config.toml"

# 查看默认配置（对比）
"$HOME/.fw-context/.venv/bin/python" -c "
from fw_context_mcp.config.settings import load_config
cfg = load_config()
print(f'enabled={cfg.llm.enabled}')
print(f'ollama_url={cfg.llm.ollama_url}')
print(f'embed_model={cfg.llm.embed_model}')
print(f'model={cfg.llm.model}')
print(f'chat_api_base={cfg.llm.chat_api_base}')
"
```

### 9.5 更新后功能异常

如果更新后 MCP 工具行为异常，可能是旧索引与新版本不兼容：

```bash
# 重建索引（在固件项目目录下）
cd /path/to/your/firmware/project
fw-context index --build
fw-context index --embeddings
```

---

## 10. 注意事项

- **用户级安装**：所有文件装在用户家目录下，不使用 sudo
- **幂等性**：两个安装脚本都是幂等的，重复运行不会造成问题。更新场景下内置跳过逻辑：fw-context 按 `requirements.txt` / wheel 的 SHA256 hash 跳过未变化的步骤；Ollama 按 tarball hash 跳过二进制安装、按 manifest 存在性跳过 670MB 模型复制
- **配置保护**：更新时 `config.toml` 仅补充缺失字段，用户自定义设置完全保留
- **Ollama serve 必须运行**才能使用 embedding 功能
- **模型文件较大**（~670MB）：首次安装需复制；更新时如 manifest 已存在则自动跳过复制
- **Chat API 可选**：如无 Chat API，`semantic_search` 仍可用（仅依赖 embedding），`smart_search` 和 `explain_symbol` 不可用
- **embedding 维度**：mxbai-embed-large 为 1024 维，与 fw-context-mcp 的 `semantic_search` 兼容
- **已有索引数据**：更新 fw-context-mcp 不会删除已有索引（`~/.fw-context/` 下的 .db 文件），但如版本跨度较大建议重建索引
- **MCP 服务器注册**：安装/更新完成后，如需将 MCP 服务器注册到 AI 工具，运行 `fw-context mcp register`
