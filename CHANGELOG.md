# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

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
