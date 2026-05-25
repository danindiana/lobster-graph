# paper-wizard

Neon-on-black [Ratatui](https://ratatui.rs/) TUI for the `paper_processor.py`
pipeline. Explains the workflow, scans your PDF corpus, configures the run,
and launches/monitors the Python subprocess with live log streaming.

## Install

```bash
cd ~/programs/python_programs/paper_processor/wizard
cargo build --release
sudo ln -sf "$PWD/target/release/paper-wizard" /usr/local/bin/paper-wizard
```

Already installed on worlock at `/usr/local/bin/paper-wizard`.

## Run

```bash
paper-wizard
```

No arguments — everything is configured inside the TUI. The wizard looks
for `paper_processor.py` in the current directory, its parent, or the
canonical `~/programs/python_programs/paper_processor/` path.

## Tabs

| Tab | What it shows |
|---|---|
| **Overview** | Pipeline explanation + live environment health (Ollama reachability + model count, Graphviz, Python deps, GPU memory) |
| **Scan** | Recursive scan of the target directory; each PDF colour-coded by status (✅ complete / ⚠ partial / ⬜ not started). Enter queues a selected paper |
| **Config** | `papers_dir`, `--backend`, `--model`, `--code-model`, `--paper`, `--reprocess`, `--workers` — live form; press `i` on `--model` or `--code-model` for interactive picker |
| **Run** | Status strip (state · elapsed · paper · section · done count) + streaming, colour-highlighted log. `L` to launch, `X` to kill |
| **Help** | Full keybinding reference |

## Keybindings

**Global:** `Tab` / `Shift-Tab` switch panels · `q` or `Ctrl-C` quit · `?`
help overlay · `F5` re-probe environment.

**Scan:** `s` scan · `↑`/`↓`/`PgUp`/`PgDn` navigate · `Enter` queue paper.

**Config:** `↑`/`↓` field · `←`/`→` cycle enums · type to edit text · `i` open
interactive model picker (on `--model` or `--code-model` field) · `Enter` jump to Run.

**Run:** `L` launch · `X` kill · `c` clear log · `↑`/`↓`/`PgUp`/`PgDn` scroll
· `End` re-enable autoscroll.

## Design notes

- The wizard is a thin controller — it only shells out to `paper_processor.py`.
  All real work (LLM calls, PDF extraction, diagram rendering) happens in
  Python. Kill the wizard anytime; the Python process is killed with it.
- Status in the Scan tab is computed by reading each paper's
  `_processed/<rel_path>/<slug>/metadata.json` and inspecting
  `sections_completed`. The slug logic mirrors the Python verbatim, so
  in-progress work is correctly recognised as resumable.
- Health probe (`F5`): hits `GET /api/tags` on Ollama, runs `dot -V`,
  imports `fitz`/`requests` in the venv, parses `nvidia-smi`.
- Log highlighting is heuristic on emoji prefixes from the Python output:
  📄 new paper → cyan, ❌/Error → red, ✅/✓ → green, ⚠ → yellow.
