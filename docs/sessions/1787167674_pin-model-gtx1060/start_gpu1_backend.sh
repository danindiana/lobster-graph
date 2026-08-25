#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# start_gpu1_backend.sh
# Spawns a single isolated Ollama daemon restricted to GPU 1 (GTX 1060 6GB),
# on its own port, so it never tensor-splits onto GPU 0 (Quadro P4000).
# Leaves the shared systemd ollama.service (port 11434, CUDA_VISIBLE_DEVICES=0,1)
# untouched — this is purely additive.
#
# Modeled on docs/sessions/2026-06-04T14-03-40_gpu_telemetry/start_isolated_backends.sh,
# but only starts the GPU1 leg (single-GPU box here, not the RTX 5080 + RTX 3080
# dual-GPU host those comments were written for), and points OLLAMA_MODELS at
# this box's real models dir instead of the stale default.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PORT="${PORT:-11435}"
LOG="${LOG:-/tmp/ollama_gpu1.log}"

export OLLAMA_MODELS="${OLLAMA_MODELS:-/home/ollama_models}"
export OLLAMA_HOST="127.0.0.1:${PORT}"
export CUDA_VISIBLE_DEVICES=1
# Ollama 0.32+ discovers GPUs via CUDA *and* Vulkan independently.
# CUDA_VISIBLE_DEVICES only filters the CUDA backend — the Vulkan backend
# still sees every card (P4000 included) and the scheduler is free to pick
# it instead. Disabling Vulkan here is what actually confines this instance
# to the CUDA-visible GTX 1060.
export OLLAMA_VULKAN=0

echo "Starting isolated Ollama backend on port ${PORT}, restricted to GPU 1 (GTX 1060) ..."
nohup ollama serve > "${LOG}" 2>&1 &
echo $! > /tmp/ollama_gpu1.pid
echo "PID $(cat /tmp/ollama_gpu1.pid), log: ${LOG}"
echo "Point paper_proc_smrtevict.py at it with: OLLAMA_URL=http://127.0.0.1:${PORT}"
