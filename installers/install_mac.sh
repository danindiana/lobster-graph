#!/usr/bin/env bash
# installers/install_mac.sh
# ─────────────────────────────────────────────────────────────────────────────
# macOS Installer for Lobster Graph (Paper Processor)
# Optimized for Apple Silicon (M1/M2/M3) unified memory stack
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

echo -e "\n${BOLD}🦞  Lobster Graph — macOS Setup${RESET}\n"

# 1. Check Homebrew
log "Checking Homebrew..."
if ! command -v brew &> /dev/null; then
    warn "Homebrew not found. Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
ok "Homebrew is ready."

# 2. System Packages
log "Installing System Dependencies..."
brew install python graphviz
ok "Python and Graphviz installed."

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
    warn "Ollama not found. You can install it via Homebrew:"
    brew install --cask ollama
    ok "Ollama installed."
fi

# 5. Neo4j Instructions
log "Checking Neo4j..."
echo -e "Lobster Graph visualizer requires Neo4j graph database."
echo -e "Recommended deployment via Docker (make sure Docker Desktop is installed):"
echo -e "  ${BOLD}docker run -d -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:latest${RESET}"

echo -e "\n${GREEN}${BOLD}🎉 Setup Complete!${RESET}"
echo -e "To start the application:"
echo -e "  source .venv/bin/activate"
echo -e "  python paper_processor.py /path/to/pdfs"
