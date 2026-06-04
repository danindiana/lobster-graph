#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# start_isolated_backends.sh
# Spawns twin isolated Ollama services to prevent VRAM swapping on dual-GPUs.
# Bound to separate graphics cards (GPU 0 & GPU 1) and ports (11434 & 11435).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Clean exit on Ctrl+C
trap 'echo -e "\n🛑 Stopping Ollama daemons..."; kill $GPU0_PID $GPU1_PID 2>/dev/null; exit 0' INT TERM

echo -e "Stopping standard system Ollama service to free VRAM..."
sudo systemctl stop ollama || true

# Export system-wide models directory to make downloaded models accessible to user-space instances
export OLLAMA_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"

echo -e "\n🚀 Starting Ollama Backend 0 (Primary) on Port 11434 (RESTRICTED to GPU 0: RTX 5080)..."
OLLAMA_HOST=127.0.0.1:11434 CUDA_VISIBLE_DEVICES=0 ollama serve > /tmp/ollama_gpu0.log 2>&1 &
GPU0_PID=$!

echo -e "🚀 Starting Ollama Backend 1 (Code) on Port 11435 (RESTRICTED to GPU 1: RTX 3080)..."
OLLAMA_HOST=127.0.0.1:11435 CUDA_VISIBLE_DEVICES=1 ollama serve > /tmp/ollama_gpu1.log 2>&1 &
GPU1_PID=$!

echo -e "\n✅ Services launched successfully!"
echo -e "  • Primary Ollama (Port 11434, GPU 0) -> PID: $GPU0_PID"
echo -e "  • Code Ollama (Port 11435, GPU 1)    -> PID: $GPU1_PID"
echo -e "  • Logs: /tmp/ollama_gpu0.log and /tmp/ollama_gpu1.log"
echo -e "\nPress Ctrl+C to terminate both servers and restore defaults."

# Keep script running to maintain processes
wait
