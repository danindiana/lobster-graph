#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# start_gpu0_backend.sh
# Spawns a second isolated Ollama daemon restricted to GPU 0 (Quadro P4000 8GB),
# on its own port, so a second paper_proc_smrtevict.py worker can run alongside
# the existing GPU1 (GTX 1060) worker without either tensor-splitting across
# both cards (no NVLink between them — every cross-device layer boundary pays
# a PCIe transfer cost).
#
# Sibling of docs/sessions/1787167674_pin-model-gtx1060/start_gpu1_backend.sh —
# same recipe, GPU 0 instead of GPU 1. Both settings below are required:
# CUDA_VISIBLE_DEVICES alone is not enough because Ollama 0.32+ discovers GPUs
# via CUDA *and* Vulkan independently, and the Vulkan backend enumerates every
# physical GPU regardless of CUDA_VISIBLE_DEVICES. OLLAMA_VULKAN=0 removes the
# other card from discovery entirely, which is what actually confines this
# instance to CUDA0 (the P4000 once CUDA_VISIBLE_DEVICES=0 is set).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PORT="${PORT:-11436}"
LOG="${LOG:-/tmp/ollama_gpu0.log}"

export OLLAMA_MODELS="${OLLAMA_MODELS:-/home/ollama_models}"
export OLLAMA_HOST="127.0.0.1:${PORT}"
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_VULKAN=0

echo "Starting isolated Ollama backend on port ${PORT}, restricted to GPU 0 (Quadro P4000) ..."
nohup ollama serve > "${LOG}" 2>&1 &
echo $! > /tmp/ollama_gpu0.pid
echo "PID $(cat /tmp/ollama_gpu0.pid), log: ${LOG}"
echo "Point a second paper_proc_smrtevict.py worker at it with: OLLAMA_URL=http://127.0.0.1:${PORT}"
