#!/usr/bin/env bash
# setup_paper_processor.sh
# ─────────────────────────────────────────────────────────────────────────────
# Installs all dependencies for paper_processor.py on Ubuntu 22.04
# RTX 3080 + RTX 3060 / Ollama / OpenClaw environment
# Run once:  bash setup_paper_processor.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

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

echo -e "\n${BOLD}🦞  Paper Processor — Setup${RESET}\n"

# ── 1. System packages ─────────────────────────────────────────────────────
log "Installing system packages …"
sudo apt-get update -qq
sudo apt-get install -y \
    graphviz          \
    graphviz-dev      \
    python3-pip       \
    python3-venv      \
    || fail "apt-get install failed"
ok "System packages installed"

# ── 2. Python packages ─────────────────────────────────────────────────────
log "Installing Python packages …"
pip install --break-system-packages --upgrade \
    pymupdf   \
    requests  \
    || fail "pip install failed"
ok "Python packages installed"

# ── 3. Verify graphviz `dot` ───────────────────────────────────────────────
if command -v dot &>/dev/null; then
    DOT_VER=$(dot -V 2>&1 | head -1)
    ok "graphviz: ${DOT_VER}"
else
    warn "dot not found in PATH — SVG rendering will be skipped"
fi

# ── 4. Verify Ollama ───────────────────────────────────────────────────────
log "Checking Ollama …"
if curl -s --max-time 5 http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  models available: {len(d[\"models\"])}')" 2>/dev/null; then
    ok "Ollama reachable at http://localhost:11434"
else
    warn "Ollama not reachable — start it with:  ollama serve"
    warn "Then pull any missing models (examples below)"
fi

# ── 5. GPU check ───────────────────────────────────────────────────────────
log "NVIDIA GPU check …"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits \
        | awk -F',' '{printf "  GPU: %-35s  Total: %6s MiB  Free: %6s MiB\n", $1, $2, $3}'
else
    warn "nvidia-smi not found"
fi

# ── 6. Recommended model pulls ─────────────────────────────────────────────
echo ""
echo -e "${BOLD}Recommended model pulls for your hardware:${RESET}"
echo -e "  (RTX 3080 10GB + RTX 3060 12GB = ~22 GB total VRAM)"
echo ""
echo "  # Primary reasoning model  (~18 GB, dual-GPU)"
echo "  ollama pull gemma4:31b-it-q4_K_M"
echo ""
echo "  # C++ code generation  (~17 GB, dual-GPU)"
echo "  ollama pull qwen3-coder:30b"
echo ""
echo "  # Single-GPU fallback  (~9 GB, fits on 3060 alone)"
echo "  ollama pull deepseek-r1:14b"
echo ""
echo "  # Fast small-paper model  (~5 GB)"
echo "  ollama pull deepseek-r1:8b"
echo ""

# ── 7. OpenClaw health (non-fatal) ─────────────────────────────────────────
log "Checking OpenClaw gateway …"
if command -v openclaw &>/dev/null; then
    if openclaw health &>/dev/null; then
        ok "OpenClaw gateway healthy"
    else
        warn "openclaw found but gateway not running"
        warn "Start it with:  openclaw gateway --force"
        warn "Or use --backend ollama (default) to skip OpenClaw entirely"
    fi
else
    warn "openclaw not found in PATH — use --backend ollama (default)"
fi

# ── 8. Rust + wizard build ─────────────────────────────────────────────────
log "Checking Rust toolchain …"
SCRIPT_DIR_RUST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIZARD_DIR="${SCRIPT_DIR_RUST}/wizard"

if ! command -v cargo &>/dev/null; then
    warn "cargo not found — Rust is required to build the paper-wizard TUI"
    warn "Install Rust:  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    warn "Then re-run this script to build the wizard automatically."
else
    CARGO_VER=$(cargo --version)
    ok "Rust toolchain: ${CARGO_VER}"

    if [[ -d "$WIZARD_DIR" ]]; then
        log "Building paper-wizard (this may take a minute on first run) …"
        if cargo build --release --manifest-path "${WIZARD_DIR}/Cargo.toml" 2>&1; then
            ok "paper-wizard built: ${WIZARD_DIR}/target/release/paper-wizard"

            # Install symlink to /usr/local/bin if writable, else ~/bin
            if [[ -w /usr/local/bin ]]; then
                INSTALL_DIR="/usr/local/bin"
            else
                INSTALL_DIR="${HOME}/.local/bin"
                mkdir -p "$INSTALL_DIR"
            fi
            ln -sf "${WIZARD_DIR}/target/release/paper-wizard" "${INSTALL_DIR}/paper-wizard"
            ok "Symlink created: ${INSTALL_DIR}/paper-wizard"
        else
            warn "cargo build failed — wizard will need to be built manually"
            warn "  cd ${WIZARD_DIR} && cargo build --release"
        fi
    else
        warn "wizard/ directory not found at ${WIZARD_DIR} — skipping build"
    fi
fi

# ── 9. Smoke test ──────────────────────────────────────────────────────────
log "Smoke-testing paper_processor.py …"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROCESSOR="${SCRIPT_DIR}/paper_processor.py"

if [[ ! -f "$PROCESSOR" ]]; then
    warn "paper_processor.py not found at ${PROCESSOR}"
    warn "Place both files in the same directory and re-run."
else
    python3 "$PROCESSOR" --help > /dev/null && ok "Script loads correctly"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Setup complete!${RESET}"
echo ""
echo -e "${BOLD}Quick start:${RESET}"
echo "  python3 paper_processor.py --list"
echo "  python3 paper_processor.py"
echo "  python3 paper_processor.py --paper 'attention_is_all_you_need.pdf'"
echo "  python3 paper_processor.py --backend openclaw"
echo "  python3 paper_processor.py --model deepseek-r1:14b  # force a smaller model"
echo "  python3 paper_processor.py --reprocess diagrams      # redo diagrams only"
echo ""
echo -e "${BOLD}OpenClaw model configuration tip:${RESET}"
echo "  openclaw models --help               # see model management options"
echo "  openclaw config set defaultModel gemma4:31b-it-q4_K_M"
echo "  openclaw agent --help                # check if --model flag is supported"
echo ""
