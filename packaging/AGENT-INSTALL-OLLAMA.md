# Agent 安装指令：Ollama 离线安装（仅 Embedding）

> **本文件供 AI agent 自动读取执行。** 你（agent）的目标是在当前 Linux 服务器上完成 Ollama + embedding 模型的离线安装，并在安装成功后报告结果。请严格按照以下步骤执行，不要跳过任何检查。
>
> **核心原则**：
> - **用户级安装，不使用 sudo**：所有文件装在用户家目录下（`~/.local/bin/` 和 `~/.ollama/`），无需 root 权限
> - 所有命令验证使用**绝对路径**（`~/.local/bin/ollama`），不依赖 PATH 是否正确配置
> - 遇到不确定的情况时，优先检查退出码而非输出文本
> - 不要无限等待任何命令；对可能阻塞的命令使用 `timeout` 包裹
> - 本包仅安装 **embedding 模型**（mxbai-embed-large:latest），用于 fw-context-mcp 的 `semantic_search` 功能。`smart_search` 和 `explain_symbol` 的 chat 功能需另行配置外部 API（如 LiteLLM）

---

## 0. 概述

你即将安装 **Ollama** —— 一个本地 LLM 运行时，以及 **mxbai-embed-large** embedding 模型。

离线包已包含全部所需文件（Ollama 二进制、模型权重、安装脚本），**无需联网**，**无需 root 权限**。

安装方式：通过 `install-ollama.sh` 脚本完成用户级安装，脚本会自动处理二进制部署、模型文件复制和验证。

安装位置：
- Ollama 二进制：`~/.local/bin/ollama`
- lib/ollama/（推理引擎 + CPU 库）：`~/.local/lib/ollama/`（含 llama-server + libggml + libllama 等）
- 模型文件：`~/.ollama/models/`（manifests + blobs）
- 完整版 runtime（仅 full 模式）：`~/.ollama/runtime/`

**前提条件**：fw-context-mcp 已安装（如未安装，请先运行 fw-context 离线包的 AGENT-INSTALL.md）。

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
for cmd in bash tar sha256sum curl; do
    command -v "$cmd" >/dev/null 2>&1 || echo "MISSING: $cmd"
done
```

- 如果输出任何 `MISSING:`，**停止**并报告缺少的命令
- `curl` 用于后续验证 Ollama API，不可缺失

### 1.3 glibc 版本

```bash
ldd --version | head -1 | awk '{print $NF}'
```

- glibc 版本必须 >= 2.31（Ollama 二进制要求）
- 如果低于 2.31，**停止**并报告："glibc 版本 <版本> 低于 2.31，Ollama 无法运行"
- 如果版本解析失败（如 musl/Alpine），**停止**并报告："无法检测 glibc 版本，可能是 musl/Alpine 系统，不支持"

### 1.4 磁盘空间与家目录可写性

```bash
df -h ~
test -w ~ || echo "STOP: 家目录不可写"
```

- 家目录至少需要 2 GB（Ollama 二进制 + 模型文件）
- 如果空间不足，**停止**并报告
- 如果输出 "STOP: 家目录不可写"，**停止**并报告

### 1.5 离线包完整性预检

确认当前目录是离线包根目录（应包含 `install-ollama.sh`、`ollama/`、`models/`）：

```bash
for f in install-ollama.sh ollama models manifest.sha256; do
    test -e "$f" || echo "MISSING: $f"
done

test -s manifest.sha256 || echo "MISSING: manifest.sha256 为空或不存在"

# 检查 Ollama 二进制包存在（cpu、slim 或 full 模式）
if [ -f ollama/ollama-cpu.tar.gz ]; then
    echo "打包模式: cpu (bin/ollama + lib/ollama/llama-server)"
elif [ -f ollama/ollama-bin.tar.gz ]; then
    echo "打包模式: slim (仅 bin/ollama，推理不可用)"
elif [ -f ollama/ollama-full.tar.gz ]; then
    echo "打包模式: full (含 lib/，完整 tarball)"
else
    echo "MISSING: ollama/ 下未找到 ollama-cpu.tar.gz、ollama-bin.tar.gz 或 ollama-full.tar.gz"
fi

# 检查模型文件存在
test -d models/manifests/registry.ollama.ai/library/mxbai-embed-large \
    || echo "MISSING: models/manifests/.../mxbai-embed-large 目录"
test -d models/blobs && [ "$(find models/blobs -type f | wc -l)" -gt 0 ] \
    || echo "MISSING: models/blobs 为空或不存在"
```

- 如果输出任何 `MISSING:`，**停止**并报告缺失的文件

### 1.6 修复文件权限与行尾

```bash
chmod +x install-ollama.sh

if grep -q $'\r' install-ollama.sh 2>/dev/null; then
    sed -i 's/\r$//' install-ollama.sh
fi
```

---

## 2. 校验离线包完整性

```bash
./install-ollama.sh --verify 2>&1; VERIFY_RC=$?
echo "verify exit code: ${VERIFY_RC}"
```

- 退出码为 0：校验通过，继续
- 退出码非 0：**停止**并报告完整错误输出，告知用户离线包在传输过程中损坏

---

## 3. 执行安装

### 3.1 执行用户级安装

```bash
timeout 120 ./install-ollama.sh 2>&1; INSTALL_RC=$?
echo "install exit code: ${INSTALL_RC}"
```

- 退出码为 0：安装成功，继续验证
- 退出码为 124：超时（2 分钟），**停止**并报告
- 退出码非 0：安装失败，按 §3.3 诊断

> **注意**：不使用 sudo。所有写入均在 `$HOME` 下。

### 3.2 安装过程说明

安装脚本的核心步骤依次为：
1. 前置检查（OS、架构、glibc、磁盘、命令）
2. 校验离线包完整性（manifest.sha256）
3. 解压 Ollama 二进制到 `~/.local/bin/ollama` + `~/.local/lib/ollama/`（cpu 模式，含 llama-server 和所有 CPU 库）；slim 模式仅复制 bin/ollama（推理不可用）；full 模式解压到 `~/.ollama/runtime/` 并创建包装脚本设置 `LD_LIBRARY_PATH`（**跳过**：tarball SHA256 hash 与上次一致且二进制存在则跳过）
4. 复制模型文件到 `~/.ollama/models/`（manifests + blobs）（**跳过**：manifest 文件已存在则跳过 670MB 复制）
5. 启动临时 `ollama serve` 验证模型列表和 embedding 请求
6. 停止临时 serve

### 3.3 安装失败诊断

| 错误关键字 | 可能原因 | 恢复操作 |
|-----------|---------|---------|
| `完整性校验失败` | 离线包损坏 | 重新拷贝离线包（**不可重试**） |
| `未找到 ollama-cpu.tar.gz` | 二进制包缺失 | 重新拷贝 `ollama/` 目录（**不可重试**） |
| `tarball 中未找到 bin/ollama` | 二进制包损坏 | 重新拷贝 `ollama/` 目录（**不可重试**） |
| `tarball 中未找到 lib/ollama/llama-server` | CPU 包不完整 | 重新拷贝 `ollama/` 目录（**不可重试**） |
| `未找到 models/manifests 目录` | 模型文件缺失 | 重新拷贝 `models/` 目录（**不可重试**） |
| `glibc 版本.*低于 2.31` | glibc 过低 | 更换到 Ubuntu 20.04+ 服务器（**不可重试**） |
| `磁盘空间不足` | 磁盘空间不够 | 清理磁盘后重试 |
| `家目录.*不可写` | 权限问题 | 检查家目录权限 |
| `ollama --version 执行失败` | 二进制不兼容 | 检查 glibc 版本和架构 |
| `bad interpreter` | CRLF 行尾 | `sed -i 's/\r$//' install-ollama.sh` 后重试 |
| `No space left` | 磁盘空间不足 | 清理磁盘后重试 |
| `GLIBC_2.xx not found` | glibc 版本过低 | 更换服务器（**不可重试**） |

**重试策略**：
- **不可重试错误**（完整性校验、文件缺失、glibc 过低）：停止并报告
- **可重试错误**（权限、磁盘空间）：修复后重试
- 最多重试 2 次

---

## 4. 验证安装成功

安装完成后，**必须**执行以下全部验证步骤。

> **重要**：以下验证命令全部使用**绝对路径**。

### 4.1 版本检查

```bash
~/.local/bin/ollama --version 2>&1; VERSION_RC=$?
echo "version exit code: ${VERSION_RC}"
```

- 退出码必须为 0
- 输出应包含版本号（如 `0.32.1`）

### 4.2 启动 Ollama 服务

安装脚本通常会停止临时 serve。需要手动启动服务进行验证：

```bash
# 检查是否已有 serve 在运行
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama serve 已在运行"
else
    nohup ~/.local/bin/ollama serve > ~/.ollama/ollama.log 2>&1 &
    SERVE_PID=$!
    echo "启动 serve, pid=${SERVE_PID}"
    # 等待最多 15 秒
    for i in $(seq 1 15); do
        if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "serve 启动成功"
            break
        fi
        sleep 1
    done
fi
```

- 如果 15 秒后 serve 仍未启动，检查日志：
```bash
tail -20 ~/.ollama/ollama.log
```

### 4.3 模型列表检查

```bash
curl -sf http://localhost:11434/api/tags 2>&1 | head -c 500
echo
```

- 输出必须包含 `mxbai-embed-large`
- 如果不包含，说明模型文件未正确安装

### 4.4 Embedding 请求测试

```bash
EMBED_RESP=$(curl -sf http://localhost:11434/api/embed \
    -d '{"model":"mxbai-embed-large:latest","input":["hello world","test sentence"]}' 2>&1)
echo "${EMBED_RESP}" | head -c 200
echo
echo "${EMBED_RESP}" | grep -q '"embeddings"' \
    && echo "EMBED OK" \
    || echo "EMBED FAILED"
```

- 必须输出 `EMBED OK`
- 响应应包含 `"embeddings"` 字段，每个向量 1024 维

### 4.5 停止临时服务（可选）

安装验证完成后，可以选择停止 serve（如果后续不需要立即使用）：

```bash
# 如果是本步骤启动的 serve，停止它
if [ -n "${SERVE_PID:-}" ]; then
    kill "${SERVE_PID}" 2>/dev/null || true
    echo "已停止验证用 serve"
fi
```

> **注意**：如果 fw-context-mcp 需要使用 embedding 功能，Ollama serve 必须保持运行。建议将其配置为后台服务或加入 shell 启动脚本。

---

## 5. 配置 fw-context-mcp

安装成功后，需要确保 fw-context-mcp 的配置正确指向 Ollama。

### 5.1 检查全局配置

```bash
cat ~/.fw-context/config.toml 2>/dev/null | grep -A5 '\[llm\]'
```

确保 `[llm]` 段包含以下设置（这些是默认值，如已正确则无需修改）：

```toml
[llm]
enabled = true
ollama_url = "http://localhost:11434"
embed_model = "mxbai-embed-large:latest"
```

### 5.2 Chat API 配置（可选）

如果需要 `smart_search` / `explain_symbol` 的 chat 功能，且使用外部 API（如 LiteLLM），在项目的 `.fw-context/local.toml` 中配置：

```toml
[llm]
# chat 模型名称（需与 LiteLLM 配置中的模型名一致）
model = "your-model-name"
# LiteLLM 端口
chat_api_base = "http://localhost:4000"
# API key（LiteLLM 的 master key，如已设置）
# chat_api_key = "sk-..."
```

> **注意**：`chat_api_base` 设置后，chat 请求发往 LiteLLM，但 embedding 仍使用本地 Ollama。

---

## 6. 安装成功报告

所有验证步骤通过后，向用户输出以下格式的报告：

```
安装成功：Ollama <版本号> + mxbai-embed-large:latest

安装路径:
  二进制:       ~/.local/bin/ollama
  lib/ollama/:  ~/.local/lib/ollama/ (llama-server + CPU 库)
  模型:         ~/.ollama/models/
  日志:         ~/.ollama/ollama.log

验证结果:
  [x] 版本: <版本号>
  [x] serve 启动: 正常 (http://localhost:11434)
  [x] 模型列表: mxbai-embed-large:latest 已安装
  [x] embedding 请求: 成功 (1024 维)

下一步:
  1. 保持 ollama serve 运行
  2. 确保 fw-context-mcp 配置正确 (~/.fw-context/config.toml)
  3. 在固件项目中运行: fw-context index --build (生成 embedding)
  4. 使用 semantic_search 工具进行语义搜索
```

---

## 7. 卸载（如需要）

```bash
./install-ollama.sh --uninstall
```

这会移除：
- `~/.local/bin/ollama`
- `~/.local/lib/ollama/llama-server`
- `~/.ollama/runtime/`（full 模式）
- 停止运行中的 `ollama serve` 进程

> **注意**：模型文件（`~/.ollama/models/`）默认保留（可能较大）。如需完全删除，手动执行 `rm -rf ~/.ollama`。

卸载后验证：
```bash
test -x ~/.local/bin/ollama && echo "卸载失败: 命令仍存在" || echo "卸载成功"
```

---

## 8. 注意事项

- **用户级安装**：所有文件装在用户家目录下，不使用 sudo
- 安装是**幂等**的：重复运行不会造成问题。**跳过逻辑**：Ollama 二进制按 `ollama-cpu.tar.gz` 的 SHA256 hash 判断（hash 与上次一致且二进制存在则跳过），模型按 manifest 文件存在性判断（`~/.ollama/models/manifests/.../mxbai-embed-large/latest` 存在则跳过 670MB 复制）。首次安装无 hash 文件且无 manifest，全部正常安装
- **Ollama serve 必须运行**才能使用 embedding 功能。启动命令：`nohup ~/.local/bin/ollama serve > ~/.ollama/ollama.log 2>&1 &`
- 本包仅安装 embedding 模型，chat 功能需配置外部 API（如 LiteLLM、DeepSeek 等）
- mxbai-embed-large 模型约 670 MB，纯 CPU 运行，无需 GPU
- embedding 维度为 1024，与 fw-context-mcp 的 `semantic_search` 兼容
- 如果 Ollama serve 启动失败，检查日志 `~/.ollama/ollama.log`
- CPU 模式包约 50 MB（bin/ollama + lib/ollama/ CPU 库，不含 GPU 库）
- slim 模式（旧版）仅含 bin/ollama，**无法做 embedding 推理**，不建议使用
- full 模式含 GPU 库约 2 GB，仅在需要 GPU 加速时使用
- 如果需要让 Ollama serve 开机自启，可将启动命令加入 `~/.bashrc` 或 `~/.profile`
