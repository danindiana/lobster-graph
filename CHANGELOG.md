# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.6.0] — 2026-06-04

### Added
- **Graph State Portability (Save/Load)**: Added "Save State" and "Load State" capabilities to the web dashboard. Captures `(x, y)` physics layout positions into `.json` so offline users can load the graph exactly as it was laid out without needing the Neo4j database.
- **Interactive Physics Controls**: Added dedicated "Physics: ON" and "Stabilize: OFF" toggles to the web dashboard. Users can disable gravity on the fly or toggle the stabilization phase to watch the gravitational engine layout papers in real-time.
- **Native Database Snapshots**: Built `snapshot_db.sh` to safely pause Neo4j, dump the raw binary data (`neo4j-admin database dump`), and instantly restart it. Added support for this to the `vram_wizard.py` control center.
- **Cross-Platform Deployments**: Engineered completely automated `installers/` stack for Linux (NVIDIA/apt), macOS (Homebrew), and Windows (Winget), seamlessly handling system deps, Python venvs, and Ollama installation.
- **Dark/Light Mode Toggle**: Added a seamless theme toggle to the graph dashboard, dynamically re-rendering node/edge colors and UI panels without requiring a page reload.
- **Automated Graph Synchronization**: Integrated a 5-minute periodic background worker in `vram_resident_processor.py` to seamlessly sync newly processed papers into Neo4j.
- **Repository Migration**: Promoted the graph processing visualization into a dedicated standalone repository (`danindiana/lobster-graph`) with comprehensive documentation and badges.

### Fixed
- **Light Mode Visual Contrast**: Fixed an issue where the canvas neon border colors became unreadable against white backgrounds. Implemented an automatic ~20% color darkening algorithm and adaptive drop shadows for Light Mode nodes.
- **Canvas CSS Variable Resolution**: Replaced CSS variables (`var(--color-concept)`) with hardcoded Hex values in `vis-network` configurations. This fixed a silent failure where the HTML5 Canvas could not parse CSS variables, resulting in broken/default node colors.
- **Background Sync Database Drop**: Replaced a full database wipe (`MATCH (n) DETACH DELETE n`) with idempotent `MERGE` queries in `neo4j_importer.py`. This fixed a critical issue where the dashboard would randomly go blank while the background sync was reconstructing the graph.
- **Graphviz SVG Visibility**: Added a CSS `invert(1) hue-rotate(180deg)` filter to embedded `.dot` SVG diagrams to make black text readable on dark mode backgrounds, while correctly disabling the filter in light mode.
- **Auto-Connect Modal**: Modified the connection overlay to automatically initialize the dashboard connection by default, preventing users from landing on a static connection screen.
- **UI Button Overflow**: Restructured the control panel grid into three horizontal rows to prevent overflowing buttons from being cut off.

---

## [0.5.1] — 2026-05-25

### Fixed
- wizard: `draw_model_picker` showed border/title but no entries. Root cause: `Paragraph`
  silently discards any line wider than the widget's inner width when `.wrap()` is not set —
  it does not truncate, it drops the entire line. The format string produced ~91-char lines
  in an 88-char inner area. Fix: replaced with `List` + `render_stateful_widget` +
  `ListState`, the same pattern used by the working Scan tab. Selection highlight and
  keyboard navigation are unchanged.

---

## [0.5.0] — 2026-05-25

### Added
- `pp.py` standalone launcher: shows model pickers for both main and C++ models in
  sequence, then `os.execv`s into `paper_processor.py`. Works from any directory.
- Interactive main model selector (`-s` / `--select-model`): numbered menu of
  `KNOWN_GOOD_MODELS` at startup; TTY-guarded so batch runs are unaffected.
- Interactive code model selector (`-c` / `--select-code-model`): independent picker
  for the C++ section model (`KNOWN_GOOD_CODE_MODELS`); `--code-model` for direct override.
- `LESSONS_LEARNED.md`: running log of non-obvious findings and design decisions.
- Session docs under `docs/sessions/`.

### Changed
- `xl_quality` default model: `gemma4:31b-it-q4_K_M` (multimodal, vision unused) →
  `nemotron-3-nano-30b-small:latest` (text-only SSM/attention hybrid, reclaims ~6 GB VRAM).
- `prompt_model_selection()` generalised to accept any model list; reused for both pickers.
- `Pipeline.__init__` gains `forced_code_model` param; priority chain:
  `forced_code_model → forced_model → CODE_MODEL`.

### Fixed
- `.gitignore`: added `backups/`, `fork_*/`, `.claude/` patterns.

---

## [0.4.0] — 2026-05-23

### Added
- Seven shields.io badges to README (license, Python, Rust, Linux, CUDA, status, local-only).
- `requirements.txt` pinning `pymupdf` and `requests`.
- `docs/TROUBLESHOOTING.md` covering common failure modes.

---

## [0.3.0] — 2026-05-15

### Fixed
- Stop passing `flash_attention` and `kv_cache_type` as per-request Ollama API options;
  they are model-load-time settings and newer Ollama versions reject them with a 400 error.
  (`49b1c20`)
- Survive transient `ConnectionError` during Ollama service restart; the pipeline now retries
  automatically rather than aborting the run. (`6927bc9`)
- Purge stale diagram slugs before writing fresh ones on reprocess, preventing orphaned
  files from previous runs. (`b3687cf`)

### Added
- RTX 5080 + RTX 3080 performance optimization fork (`fork_gptOSS_textonly`) with:
  - `gpt-oss:20b` replacing `gemma4` as the primary model.
  - Dual-GPU model residency and pinning for zero-swap inference.
  - Token budget caps scaled linearly with page count (10 K analysis / 20 K code).
  - Parallel section processing with a keep-alive heartbeat.
  - Per-paper section timing written to `metadata.json`.
  - GPU routing: code sections → RTX 5080, reasoning → RTX 3080.
- `FUTURE_DIRECTIONS.md` with a ranked improvement backlog.
- Session docs covering GPU thermal management, power capping, and dual-GPU residency.

---

## [0.2.0] — 2026-05-01

### Added
- Graceful shutdown on SIGINT/SIGTERM with per-section checkpointing.
- `--override` flag for aggressive Ollama GPU provisioning.
- Neon-on-black Graphviz architecture diagrams in README.
- Operator notes section in README.

### Changed
- Default workers raised to 2.
- Ollama calls streamed instead of blocking.
- Local-model gate added — pipeline refuses to start if required models are absent.

### Fixed
- DOT diagram render failures; diagram stage now retries with the reasoning model on empty parse.
- Retry logic with exponential back-off on Ollama timeouts.

---

## [0.1.0] — 2026-04-15

### Added
- Initial release: `paper_processor.py` pipeline + Ratatui `paper-wizard` TUI.
- Python pipeline producing per-paper: summary, formal-logic refactor, C++20/23
  implementations, six neon Graphviz diagrams, and a critical-analysis doc.
- Resumable runs via `metadata.json` stage markers.
- `setup_paper_processor.sh` for system dependency and venv setup.
- MIT License.
