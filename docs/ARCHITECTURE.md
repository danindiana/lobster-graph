# Architecture

![Data Flow Architecture](diagrams/explainer/architecture_dataflow.svg)

## Overview

paper-processor is a two-component system:

```
┌─────────────────────────┐        ┌──────────────────────────────┐
│   paper-wizard (Rust)   │        │  paper_processor.py (Python)  │
│   Ratatui TUI           │──────▶ │  Pipeline orchestrator         │
│   Tabs: Overview/Scan/  │  exec  │  Reads PDFs → calls Ollama     │
│   Config/Run/Help       │        │  Writes structured dossiers    │
└─────────────────────────┘        └──────────┬───────────────────┘
                                              │ HTTP REST
                                              ▼
                                   ┌──────────────────────┐
                                   │   Ollama (local)      │
                                   │   port 11434          │
                                   │   CUDA_VISIBLE_DEVICES│
                                   │   =0,1 (dual GPU)     │
                                   └──────────────────────┘
```

The wizard is optional — `paper_processor.py` can be called directly from the CLI.

---

## Python pipeline (`paper_processor.py`)

### Entry points

| Path | Purpose |
|------|---------|
| `main()` | Argument parsing, directory scan, worker dispatch |
| `PaperProcessor.process_paper()` | Per-paper orchestration |
| `Backend` | Abstraction over Ollama and OpenClaw HTTP calls |

### PDF extraction and chunking

`fitz` (pymupdf) extracts raw text page-by-page. Papers ≤ 12 pages are sent to
the model in a single call. Papers > 12 pages use a **sliding-window map-reduce**:

1. **Map** — the text is split into overlapping chunks (window = 8 pages,
   stride = 6 pages). Each chunk is summarised independently.
2. **Reduce** — the chunk summaries are concatenated and passed to the model a
   second time to produce a single coherent output.

The overlap (2 pages) prevents important content at chunk boundaries from being
lost between windows.

### Model auto-selection

Models are chosen per paper based on page count, defined in `TIER_BY_PAGES`.
Restricted to two active models for now (2026-08-06 — see
`docs/sessions/2026-08-06_183747_model-tier-restriction-and-100pct-gpu-fit.md`):

| Pages | Tier key | Default model |
|-------|----------|---------------|
| ≤ 35 | `xl_quality` | `gemma4:26b-a4b-it-q4_K_M` (~17 GB, fast/lighter) |
| 36–200 | `xl_reason` | `nemotron3:33b` (~27 GB, strong chain-of-thought) |
| > 200 | `xl_quality` | falls back to gemma4 — stays GPU-resident on huge books |

The C++ section always uses `CODE_MODEL`, which reuses `MODEL_TIERS["xl_quality"]`
(gemma4) rather than a separate code-specialised model — keeps a single model
resident, avoiding VRAM churn from loading a second model for the C++ stage.

`--model` overrides all automatic selection for every section of every paper.
`nemotron3:33b` (~27 GB) exceeds this workstation's combined VRAM (RTX 5080
16 GB + RTX 3080 10 GB ≈ 26.5 GB), so it always runs with some CPU-offloaded
layers — expected, and unrelated to the GPU fit-target tuning below.

### Processing stages

Five stages run per paper in order. Each stage is independent — failure in one
does not block others.

| Stage key | Output file | Prompt focus |
|-----------|-------------|-------------|
| `summary` | `01_summary.md` | Motivation, method, results, limitations |
| `logic` | `02_symbolic_logic.md` | Formal notation, theorems, complexity |
| `cpp` | `03_cpp_examples.md` | C++20/23 implementations (uses CODE_MODEL) |
| `extras` | `04_extras.md` | Open questions, critique, connections |
| `diagrams` | `diagrams/*.dot/.svg` | 6 neon Graphviz diagrams |

The diagram stage calls the model to generate DOT source, then shells out to
`graphviz dot` to render SVG files.

### Metadata checkpointing

Each paper has a `metadata.json` in its output directory:

```json
{
  "slug": "attention-is-all-you-need",
  "source_hash": "sha256:...",
  "model": "gemma4:31b-it-q4_K_M",
  "pages": 15,
  "sections": {
    "summary": {"completed": true, "elapsed_s": 142.3},
    "logic":   {"completed": true, "elapsed_s": 98.1},
    "cpp":     {"completed": false}
  }
}
```

On each run, the pipeline reads `metadata.json` and skips any stage where
`completed: true`. This makes runs **fully resumable** — an interrupted run
continues exactly where it left off.

`--reprocess <stage>` clears that stage's completion flag and forces a re-run
of just that section across all papers.

### Parallelism

`--workers N` controls the number of papers processed simultaneously via a
`ThreadPoolExecutor`. The default is 1 because Ollama's
`OLLAMA_NUM_PARALLEL=1` means concurrent requests queue server-side anyway.
Set `--workers 2` only if you have enough VRAM for two models simultaneously
(requires `OLLAMA_MAX_LOADED_MODELS=2`).

Sections within a single paper run **sequentially** in stage order.

### GPU provisioning (`--override`)

When `--override` is passed, the pipeline calls `_ollama_provision_gpu()` before
starting, which:

1. Lists all models currently resident in VRAM via `/api/ps`.
2. Sends `keep_alive=0` to each to force eviction.
3. Waits up to 20 s for VRAM to clear.
4. If models are still loaded, escalates to `systemctl restart ollama`.
5. Waits a further 15 s for the service to come back clean.

This ensures maximum free VRAM before loading the pipeline's own models.

### Ollama GPU fit-target tuning (host-level, not pipeline code)

Independent of the pipeline's own provisioning above, Ollama's underlying
`llama-server` (0.32.5+) runs its own auto-fit engine (`LLAMA_ARG_FIT`,
default `on`) that decides per-tensor GPU vs. CPU placement — this overrides
the classic `num_gpu` request option entirely when active. By default it
reserves a small safety margin per device (`LLAMA_ARG_FIT_TARGET`) even when
several GB of VRAM sit free, which can leave a model reported as e.g.
`96% GPU` instead of `100% GPU`. This host is configured with
`LLAMA_ARG_FIT_TARGET=0` in `/etc/systemd/system/ollama.service.d/override.conf`
to remove that margin, since the reference dual-GPU setup (RTX 5080 + RTX 3080)
has enough combined headroom for the two active models below their own
VRAM footprint. This is a global Ollama setting, not something
`paper_processor_dir.py` controls — see
`docs/sessions/2026-08-06_183747_model-tier-restriction-and-100pct-gpu-fit.md`
for the investigation and `docs/TROUBLESHOOTING.md` for the symptom/fix.

### Graceful shutdown

SIGINT (Ctrl+C) and SIGTERM set a `threading.Event` (`_shutdown`). The pipeline
checks this flag between sections and after each Ollama streaming chunk. When
set, the current section completes and writes its output, then the run stops
cleanly. A second Ctrl+C forces an immediate `os._exit(1)`.

---

## Rust TUI (`wizard/`)

The wizard is a [Ratatui](https://ratatui.rs/) terminal application compiled to
`paper-wizard`. It communicates with the pipeline by spawning `paper_processor.py`
as a subprocess and streaming its stdout.

### Tab state machine

```
Overview ──▶ Scan ──▶ Config ──▶ Run
                                  │
                              (logs stream here)
```

| Tab | Responsibility |
|-----|---------------|
| Overview | Explains the pipeline stages and output structure |
| Scan | Walks the PDF corpus directory, counts papers and their status |
| Config | Exposes `--backend`, `--model`, `--workers`, `--reprocess` as interactive fields |
| Run | Launches `paper_processor.py` with the configured flags, streams log output with syntax colouring |
| Help | Keybindings reference |

### Search path for `paper_processor.py`

The wizard looks for the Python script in this order:
1. Current working directory
2. Parent of current working directory
3. `~/programs/python_programs/paper_processor/`

### Build

```bash
cd wizard/
cargo build --release
# Binary: wizard/target/release/paper-wizard
sudo ln -sf "$(pwd)/target/release/paper-wizard" /usr/local/bin/paper-wizard
```

---

## Output layout

```
_processed/
└── <subfolder>/
    └── <slug>/              # deterministic from PDF filename
        ├── metadata.json
        ├── 01_summary.md
        ├── 02_symbolic_logic.md
        ├── 03_cpp_examples.md
        ├── 04_extras.md
        └── diagrams/
            ├── 01_<title>.dot
            ├── 01_<title>.svg
            └── …  (up to 6 pairs)
```

Slugs are derived from the PDF filename with spaces replaced by hyphens and
special characters stripped. The `source_hash` in `metadata.json` detects if
the source PDF has changed since the dossier was last generated.

---

## Neo4j Graph Service

### Purpose

The Neo4j database is the persistent backing store for the Lobster Graph knowledge graph. `neo4j_importer.py` continuously syncs processed dossier metadata into Neo4j as papers finish (every 300 seconds), building a typed graph of papers, concepts, theorems, algorithms, and cross-references. The optional web dashboard (`neo4j_viz/webgl.html`) queries this graph to visualize connectivity, enable interactive exploration, and export subgraph snapshots.

### systemd Configuration

Neo4j runs as a systemd service (`paper-processor-neo4j.service`) that auto-starts on system boot.

| Property | Value |
|----------|-------|
| **Service File** | `/etc/systemd/system/paper-processor-neo4j.service` |
| **Working Directory** | `/home/jeb/programs/python_programs/paper_processor/neo4j_viz/` |
| **Managed by** | Docker Compose |
| **Boot Target** | `multi-user.target` (auto-start on system boot) |
| **Restart Policy** | Always restart on failure (10-second delay) |
| **Memory Cap** | 4 GB |

### Access Points

| Service | Port | Purpose |
|---------|------|---------|
| HTTP Console | `localhost:7474` | Interactive web interface (password auth) |
| Bolt Protocol | `localhost:7687` | Driver connections (used by importer + dashboard) |

### Configuration

**Credentials (development default):**
- User: `neo4j`
- Password: `password123`

See `docs/sessions/2026-08-03_163916.md` ("Future Notes") for security notes on rotating credentials and moving to `.env` files.

**Docker Compose:**
- Location: `neo4j_viz/docker-compose.yml`
- Container name: `paper-processor-neo4j`
- Image: `neo4j:latest` (should be pinned to a specific version in production)
- Volumes: `./data`, `./logs`, `./import`, `./plugins` (persisted across restarts)

### Integration with Pipeline

The `paper_processor_dir.py` main loop runs a background thread (`_periodic_sync_worker`, line 1237) that:
1. Waits 15 seconds after pipeline startup (to let initial papers begin processing).
2. Every 300 seconds, checks if Neo4j Bolt port (7687) is reachable.
3. If reachable, calls `neo4j_importer.py` to sync the `_processed/` directory into the graph.

This means Neo4j does not need to be pre-running; the pipeline will detect when it comes online and begin syncing. However, systemd boot-order ensures Neo4j starts before the pipeline would typically run, so data is available immediately.

### Troubleshooting & History

Full troubleshooting guide: see `docs/TROUBLESHOOTING.md` ("Neo4j container won't start…" and "Permission denied…" sections).

Detailed setup history, lessons learned, and future directions: see `docs/sessions/2026-08-03_163916.md`.

### Architecture Diagram

![Neo4j boot service and integration](diagrams/09_neo4j_boot_service.svg)
