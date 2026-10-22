#!/usr/bin/env bash
# Test embedding request manually (CPU-only package verification)
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"
export PATH="$HOME/.local/bin:$PATH"

echo "[*] Checking installation..."
OLLAMA_BIN="$HOME/.local/bin/ollama"
LLAMA_SERVER="$HOME/.local/lib/ollama/llama-server"

if [[ ! -x "$OLLAMA_BIN" ]]; then
    echo "[!] ollama binary not found: $OLLAMA_BIN"
    exit 1
fi
echo "[+] ollama: $OLLAMA_BIN ($($OLLAMA_BIN --version 2>&1 | head -1))"

if [[ ! -x "$LLAMA_SERVER" ]]; then
    echo "[!] llama-server not found: $LLAMA_SERVER"
    echo "[!] embedding inference will fail — install cpu package (not slim)"
    exit 1
fi
echo "[+] llama-server: $LLAMA_SERVER"

echo "[*] Starting ollama serve..."
nohup ollama serve > /tmp/ollama-manual.log 2>&1 &
PID=$!

for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        echo "[+] Ollama serve started (pid=$PID)"
        break
    fi
    sleep 1
done

echo "[*] Model list:"
curl -s http://127.0.0.1:11434/api/tags
echo

echo "[*] Embedding request (first load may take 10-30s)..."
RESULT=$(curl -s --max-time 120 http://127.0.0.1:11434/api/embed \
    -H 'Content-Type: application/json' \
    -d '{"model":"mxbai-embed-large:latest","input":["hello world"]}')
echo "[*] Response (first 500 chars):"
echo "$RESULT" | head -c 500
echo
echo

if echo "$RESULT" | grep -q '"embeddings"'; then
    DIMS=$(echo "$RESULT" | grep -oE '"embeddings":\[\[[0-9., -]+\]' | head -1 | tr -cd ',' | wc -c)
    echo "[+] Embedding SUCCESS! Dimensions: $((DIMS + 1))"
else
    echo "[!] Embedding FAILED"
    echo "[*] Ollama log (last 20 lines):"
    tail -20 /tmp/ollama-manual.log
fi

echo "[*] Stopping serve (pid=$PID)..."
kill "$PID" 2>/dev/null || true
sleep 2
kill -9 "$PID" 2>/dev/null || true
echo "[+] Done"
