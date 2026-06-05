#!/usr/bin/env bash
# installers/install_linux.sh
# ─────────────────────────────────────────────────────────────────────────────
# Baremetal Linux Installer for Lobster Graph (Paper Processor)
# Optimized for NVIDIA stack environments (Ubuntu/Debian)
# ─────────────────────────────────────────────────────────────────────────────
set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

log()  { echo -e "${CYAN}▶  $*${RESET}"; }
ok()   { echo -e "${GREEN}✓  $*${RESET}"; }
warn() { echo -e "${YELLOW}⚠  $*${RESET}"; }
fail() { echo -e "${RED}✗  $*${RESET}"; exit 1; }

echo -e "\n${BOLD}🦞  Lobster Graph — Linux Setup${RESET}\n"

# 1. Check for NVIDIA Driver/Toolkit
log "Checking NVIDIA Hardware Stack..."
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    ok "NVIDIA GPU detected and ready."
else
    warn "nvidia-smi not found. This application relies heavily on local VRAM for inference."
    warn "Please ensure your NVIDIA drivers and CUDA toolkit are installed."
fi

# 2. System Packages
log "Installing System Dependencies..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-pip graphviz curl
    ok "System dependencies installed."
else
    warn "apt-get not found. Please install python3, python3-venv, pip, and graphviz manually."
fi

# 3. Virtual Environment & Python Packages
log "Configuring Python Environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    ok "Virtual environment created."
fi
source .venv/bin/activate
pip install --upgrade pip
if [ -f "../requirements.txt" ]; then
    pip install -r ../requirements.txt
elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    pip install pymupdf requests neo4j
fi
ok "Python dependencies installed."

# 4. Ollama Installation Check
log "Checking Ollama..."
if command -v ollama &> /dev/null; then
    ok "Ollama is installed."
else
    warn "Ollama not found. Downloading install script..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed."
fi

# 5. Neo4j Instructions
log "Checking Neo4j..."
echo -e "Lobster Graph visualizer requires Neo4j graph database."
echo -e "Recommended deployment via Docker:"
echo -e "  ${BOLD}docker run -d -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:latest${RESET}"

echo -e "\n${GREEN}${BOLD}🎉 Setup Complete!${RESET}"
echo -e "To start the application:"
echo -e "  source .venv/bin/activate"
echo -e "  python paper_processor.py /path/to/pdfs"
