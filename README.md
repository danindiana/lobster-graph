# paper-processor

A two-part pipeline for turning an AI/ML research-paper PDF corpus into
structured study dossiers using local LLMs:

- **`paper_processor.py`** — Python pipeline. For each PDF it produces a
  comprehensive summary, a formal-logic refactor, C++20/23 reference
  implementations of the key algorithms, six Graphviz diagrams (neon
  on black), and a critical-analysis doc. Metadata tracks progress so
  runs are resumable across interruptions.
- **`wizard/`** — [Ratatui](https://ratatui.rs/) TUI (`paper-wizard`)
  that explains the pipeline, scans your corpus, configures the run,
  launches the Python process, and streams its log with syntax colour.

Everything runs locally against [Ollama](https://ollama.com/); there is
no cloud dependency.

![neon-diagram-aesthetic](https://img.shields.io/badge/theme-neon--on--black-00FF41?style=flat-square)
![rust-ratatui](https://img.shields.io/badge/TUI-ratatui-FF00FF?style=flat-square)
![ollama](https://img.shields.io/badge/backend-ollama-00FFFF?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![rust](https://img.shields.io/badge/rust-stable-CE422B?style=flat-square&logo=rust&logoColor=white)
![platform](https://img.shields.io/badge/platform-linux-FCC624?style=flat-square&logo=linux&logoColor=black)
![gpu](https://img.shields.io/badge/GPU-CUDA%20dual--GPU-76b900?style=flat-square&logo=nvidia&logoColor=white)
![status](https://img.shields.io/badge/status-active-brightgreen?style=flat-square)
![no-cloud](https://img.shields.io/badge/cloud-none%20%7C%20local--only-lightgrey?style=flat-square)

## How it works

### System architecture

The wizard (or a direct CLI call) drives the Python pipeline; the pipeline
talks to Ollama over localhost; Ollama auto-schedules layers across both
GPUs; outputs land in a mirrored dossier tree.

![system architecture](docs/diagrams/01_architecture.png)

### Per-paper processing flow

Each PDF is extracted, chunked with a sliding window (only if it exceeds
12 pages), condensed via map-reduce, then run through five independent
prompt stages. Every stage writes its marker to `metadata.json`, so
partial runs resume exactly where they left off.

![per-paper flow](docs/diagrams/02_per_paper_flow.png)

### Model auto-selection

Page count picks the model tier. The C++ stage always runs on the
code-specialised model regardless of length. `num_gpu` is intentionally
left unset so Ollama can split the 30B models across the 3080+3060 pair
at 32k context without OOMing.

![model routing](docs/diagrams/03_model_routing.png)

### Wizard state machine

Five tabs cycle with `Tab` / `Shift-Tab`. `F5` re-probes the environment.
`Enter` on a Scan row queues that paper into Config; `L` launches the
Python subprocess from the Run tab; `X` kills it.

![wizard tabs](docs/diagrams/04_wizard_tabs.png)

> All four diagrams are in [`docs/diagrams/`](docs/diagrams/) as both `.dot`
> sources and rendered `.svg` / `.png` — regenerate any of them with
> `dot -Tpng -Gdpi=140 -o foo.png foo.dot`.


## Output per paper

```
_processed/<subfolder>/<slug>/
├── 01_summary.md              # motivation → results → limitations
├── 02_symbolic_logic.md       # formal notation, theorems, complexity
├── 03_cpp_examples.md         # C++20/23 implementations, compilable
├── 04_extras.md               # open questions, critique, connections
├── diagrams/
│   ├── 01_<title>.dot + .svg  # architecture
│   ├── 02_<title>.dot + .svg  # data flow
│   ├── 03_<title>.dot + .svg  # algorithm flowchart
│   ├── 04_<title>.dot + .svg  # taxonomy
│   ├── 05_<title>.dot + .svg  # training loop
│   └── 06_<title>.dot + .svg  # vs prior art
└── metadata.json              # model, hash, sections completed
```

## Hardware targets

Written for a dual-GPU workstation (validated on RTX 3080 10 GB + RTX 3060
12 GB). Models auto-route by page count:

| Pages | Model | VRAM |
|---|---|---|
| ≤ 8 | `deepseek-r1:8b` | ~5 GB |
| ≤ 18 | `deepseek-r1:14b` | ~9 GB |
| > 18 | `gemma4:31b-it-q4_K_M` | ~18 GB dual-GPU |
| C++ stage | `qwen3-coder:30b` | ~17 GB dual-GPU |

Single-GPU setups: force `--model deepseek-r1:14b` for all stages.

## Quick start

```bash
# System + Python deps
bash setup_paper_processor.sh

# Pull the models you want (see table above)
ollama pull deepseek-r1:8b
ollama pull deepseek-r1:14b
ollama pull gemma4:31b-it-q4_K_M
ollama pull qwen3-coder:30b

# Run headless on a directory of PDFs (recursive)
python paper_processor.py ~/my_papers

# Single paper, forced small model
python paper_processor.py ~/my_papers --paper "attention.pdf" \
                                       --model deepseek-r1:14b

# Re-run just the diagram stage
python paper_processor.py ~/my_papers --reprocess diagrams

# Status of the whole corpus, no LLM calls
python paper_processor.py ~/my_papers --list

# Evict any loaded model from VRAM before starting (restart service if stuck)
python paper_processor.py ~/my_papers --override
```

## TUI wizard

```bash
cd wizard
cargo build --release
sudo ln -sf "$PWD/target/release/paper-wizard" /usr/local/bin/paper-wizard
paper-wizard
```

Five tabs: **Overview · Scan · Config · Run · Help**. Streams the Python
subprocess output live with colour coding; `L` launches, `X` kills,
`Tab` cycles panels, `?` pops a key-ref overlay. Scan status is read
from each paper's `metadata.json` using the same slug logic as the
Python side, so partial runs are recognised and resumed correctly.

See [`wizard/README.md`](wizard/README.md) for full keybindings.

## CLI flags

```
python paper_processor.py [papers_dir]
    --backend {ollama,openclaw}   default ollama
    --model MODEL                 force one model for every stage
    --paper FILENAME              single paper (basename or rel path)
    --reprocess SECTION           summary|logic|cpp|diagrams|extras|all
    --workers N                   parallel papers (⚠ VRAM)
    --list                        show status table, exit
    --override                    evict loaded Ollama models; restart service if stuck
```

## Design notes

- **Resumable.** `metadata.json` records which sections finished. Re-running
  skips completed papers entirely and skips completed sections of partial
  papers. Safe to Ctrl-C anytime.
- **Recursive.** `papers_dir` is walked with `rglob`; the output tree
  mirrors the input subfolder structure so slugs can never collide.
- **Sliding-window chunking.** Papers longer than 12 pages are chunked
  with 2-page overlap, map-reduced per chunk, then condensed before the
  downstream prompts run on the distilled context.
- **Diagram aesthetic.** The LLM is explicitly instructed to emit neon
  accent colours on a black background; a post-processor injects
  `bgcolor=black` + default neon styles if the model forgets.
- **`--override` GPU provisioner.** Checks `/api/ps` before the first
  paper and evicts every loaded model via `keep_alive=0`. If any model
  is still resident after 20 s it restarts the `ollama` systemd service
  and waits up to 45 s for it to come back. Safe to combine with any
  other flag; no-op when the backend is not Ollama.
- **`num_gpu` intentionally unset.** Hard-forcing all layers on GPU causes
  OOM on 30B models at `num_ctx=32768` across a ≤22 GB VRAM pool; letting
  Ollama auto-schedule fixes it.

## Status

Built and validated on a single 4-page paper through all five sections +
six SVG diagrams. Runtime budget for a 5,000-paper corpus at average
~20-40 min/paper is measured in weeks, not hours — plan in batches.

## Operator notes

Workstation setup notes that aren't part of the pipeline itself —
toolchain installs, environment provisioning, incident write-ups —
live in [`docs/sessions/`](docs/sessions/). Each file is timestamped
(`YYYY-MM-DDTHHMMSSZ_<topic>.md`) and self-contained.

Recent additions on the `morlok` host include Python 3.13 venv setup,
a zsh install, a from-source lite-xl build, and a Midnight Commander
install with a user-local Dracula skin (`~/.local/share/mc/skins/`,
selected via `~/.config/mc/ini`). None of these are required to run
`paper_processor.py`; they document the operator environment so the
state of the workstation can be reconstructed from the repo alone.

## Forks

| Directory | Model change | Rationale |
|-----------|-------------|-----------|
| [`fork_2026-05-15T235801Z/`](fork_2026-05-15T235801Z/) | **Performance Optimization** | Optimized for RTX 5080 + 3080 mix. Added flags for `--ctx`, `--kv-cache`, and `--single-runner` to fix VRAM scheduling churn. |
| [`fork_gptOSS_textonly_2026-05-14T205304Z/`](fork_gptOSS_textonly_2026-05-14T205304Z/) | `xl_quality`: `gemma4:31b-it-q4_K_M` → `gpt-oss:20b` | Pipeline is text-only (`fitz.get_text`; no image fields in either backend). Gemma 4's multimodal capacity is never exercised; `gpt-oss:20b` reclaims ~6 GB VRAM on the dual-GPU pool. |

Each fork carries its own `docs/sessions/` log and a `README.md` describing the
change. Routing thresholds, prompts, and checkpointing are identical to the
parent unless otherwise noted.

## Licence

MIT
