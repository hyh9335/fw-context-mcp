# Ollama 离线安装说明（仅 Embedding 模型）

本文档说明如何在离线 Linux 服务器上安装 Ollama + mxbai-embed-large embedding 模型，用于 fw-context-mcp 的 `semantic_search` 功能。

## 前提条件

- Linux x86_64，glibc >= 2.31（Ubuntu 20.04+）
- fw-context-mcp 已安装（参见 fw-context 离线安装包）
- 磁盘空间 >= 2 GB
- 无需 sudo/root 权限

## 快速安装

```bash
# 1. 解压离线包（如尚未解压）
tar -xzf ollama-offline-bundle.tar.gz
cd ollama-offline-bundle

# 2. 运行安装脚本
./install-ollama.sh

# 3. 启动 Ollama 服务
nohup ~/.local/bin/ollama serve > ~/.ollama/ollama.log 2>&1 &

# 4. 验证
curl http://localhost:11434/api/tags
```

## 安装内容

| 组件 | 安装位置 | 说明 |
|------|---------|------|
| ollama 二进制 | `~/.local/bin/ollama` | Ollama 运行时 |
| lib/ollama/ | `~/.local/lib/ollama/` | 推理引擎 + CPU 库（llama-server, libggml, libllama 等） |
| 模型文件 | `~/.ollama/models/` | mxbai-embed-large 权重 + 清单 |
| 日志 | `~/.ollama/ollama.log` | serve 运行日志 |

## 验证安装

```bash
# 版本
~/.local/bin/ollama --version

# 模型列表
~/.local/bin/ollama list

# Embedding 测试
curl http://localhost:11434/api/embed \
    -d '{"model":"mxbai-embed-large:latest","input":["hello"]}'
```

## 配置 fw-context-mcp

确保 `~/.fw-context/config.toml` 中 `[llm]` 段配置正确：

```toml
[llm]
enabled = true
ollama_url = "http://localhost:11434"
embed_model = "mxbai-embed-large:latest"
```

### Chat API 配置（可选）

本离线包仅安装 embedding 模型。如需 `smart_search` / `explain_symbol` 的 chat 功能，可配置外部 API。

在项目的 `.fw-context/local.toml` 中：

```toml
[llm]
# 使用 LiteLLM 代理
chat_api_base = "http://localhost:4000"
# chat 模型名称（需与 LiteLLM 配置一致）
model = "your-model-name"
# API key（如 LiteLLM 设置了 master key）
# chat_api_key = "sk-..."
```

配置后：
- Chat 请求 → LiteLLM（或外部 API）
- Embedding 请求 → 本地 Ollama

## 启动与停止

```bash
# 启动（后台）
nohup ~/.local/bin/ollama serve > ~/.ollama/ollama.log 2>&1 &

# 停止
pkill -f "ollama serve"

# 查看日志
tail -f ~/.ollama/ollama.log
```

## 卸载

```bash
./install-ollama.sh --uninstall

# 如需完全删除模型文件
rm -rf ~/.ollama
```

## 常见问题

### Q: Ollama serve 启动失败？

检查日志 `~/.ollama/ollama.log`。常见原因：
- 端口 11434 被占用：`OLLAMA_HOST=127.0.0.1:11435 ollama serve`
- glibc 版本过低：`ldd --version` 检查
- `llama-server binary not found`：检查 `~/.local/lib/ollama/llama-server` 是否存在且可执行

### Q: embedding 请求返回 500 错误？

如果日志中出现 `llama-server binary not found`，说明推理引擎未正确安装。重新运行安装脚本，或手动从离线包复制：
```bash
mkdir -p ~/.local/lib/ollama
tar -xzf ollama/ollama-cpu.tar.gz -C /tmp/ollama-cpu-extract
cp /tmp/ollama-cpu-extract/lib/ollama/llama-server ~/.local/lib/ollama/
chmod +x ~/.local/lib/ollama/llama-server
```

### Q: embedding 请求很慢？

mxbai-embed-large 在纯 CPU 上运行，首次请求需加载模型（~10 秒），后续请求较快。如需加速，可配置 GPU。

### Q: 如何更改模型存储位置？

设置环境变量 `OLLAMA_MODELS`：
```bash
OLLAMA_MODELS=/data/ollama/models ollama serve
```

### Q: smart_search 报错 "chat model not found"？

本包未安装 chat 模型。`smart_search` 会自动回退到纯 FTS5 搜索。如需完整功能，配置外部 chat API（见上文）。
