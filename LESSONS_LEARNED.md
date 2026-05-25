# Lessons Learned — paper_processor

A running log of non-obvious findings, debugging dead-ends, and design decisions worth
remembering. Most recent entries first.

---

## 2026-05-25 — Vision Model Was Never Being Used

**What happened:** `gemma4:31b-it-q4_K_M` (a multimodal vision model, ~19 GB) was set as
the `xl_quality` default. The pipeline looked like it was using vision capability but wasn't.

**Root cause:** PDF pages are extracted with `fitz.get_text("text")` — plain text only. All
embedded images, figures, and charts are silently discarded. The Ollama payload only ever
contains `{"model": ..., "prompt": str}` — no `images` field, no base64, nothing multimodal.
The vision weights load into VRAM and sit unused every run.

**Fix:** Replaced `gemma4` with `nemotron-3-nano-30b-small:latest` (text-only, SSM/attention
hybrid). Reclaimed ~6 GB of VRAM headroom.

**Broader rule:** If you're not calling `page.get_pixmap()` and base64-encoding the result
into the Ollama `images` field, you are not using vision — regardless of what model is loaded.
A vision-capable model used text-only is just a larger, slower text model.

**Future option:** To actually exercise vision, switch extraction to:
```python
pix = page.get_pixmap(dpi=150)
image_b64 = base64.b64encode(pix.tobytes("png")).decode()
payload["images"] = [image_b64]
```
Only worthwhile if the model is vision-capable AND the papers contain figures that matter.

---

## 2026-05-25 — Fork Directories Are Frozen Snapshots, Not Branches

**What happened:** Running `python paper_processor.py -s` from inside a `fork_*/` subdirectory
fails with `unrecognized arguments: -s` — the flag doesn't exist in the frozen copy.

**Root cause:** Forks are plain directory copies taken as pre-change snapshots. They have no
`.git`, don't receive updates, and diverge from `main` immediately after creation. They exist
for rollback reference only.

**Rule:** Always run from the project root (`/home/jeb/programs/python_programs/paper_processor/`),
not from a fork subdirectory. Use `./pp.py` or `python3 paper_processor.py` from the root.

---

## 2026-05-25 — Python stdout Buffering Hides Output When Not TTY-Attached

**What happened:** Backgrounding `paper_processor.py` via the Claude Code harness produced an
empty output file for several minutes. `tail -f` showed nothing. The process was running fine.

**Root cause:** Python buffers stdout when not attached to a TTY. Lines only flush when the
buffer fills (~8 KB) or the process exits — so a slow LLM generation produces silence for
minutes before a burst of output appears.

**Fix:** Use `python3 -u` (unbuffered) or run directly inside an `xterm` so it's TTY-attached.
The `xterm -e` approach is cleanest — output streams in real time and signal handling works
correctly.

**Rule:** Any time you want to watch paper_processor live, run it inside a terminal, not
backgrounded:
```bash
xterm -e "python3 -u paper_processor.py --paper foo.pdf --verbose /path/to/docs"
```

---

## 2026-05-25 — `--select-model` / `-s` vs `pp.py` Launcher

**Context:** Added an interactive model picker (`-s` flag) so operators can choose from a
curated list of known-good models at runtime instead of relying on silent page-count
auto-selection.

**Lesson:** A flag-gated feature (`-s`) is easy to forget and awkward to discover. A thin
launcher script (`pp.py`) that always shows the picker is more ergonomic — it makes the
interactive path the default entry point while leaving `paper_processor.py` fully scriptable
for automation.

**Pattern:** Keep the main script non-interactive and flag-driven (good for cron, pipes,
batch). Put interactive UX in a separate launcher that `os.execv`s into the main script.
`os.execv` is preferable to `subprocess` here — it replaces the process cleanly so TTY
attachment, signal handling, and exit codes all work correctly.

---

## 2026-05-14 — text-only Fork (gpt-oss:20b)

**Context:** `fork_gptOSS_textonly_2026-05-14T205304Z/` was created to explicitly swap the
multimodal `gemma4` for `gpt-oss:20b` and document that the pipeline is text-only. This was
the first acknowledgement of the vision waste problem.

**Lesson:** The fork's README clearly stated the pipeline was text-only. That finding was not
propagated back to `main` until 2026-05-25. Write findings in LESSONS_LEARNED (here), not
just fork READMEs, so they survive across the project history.

---

## General — Model Selection Heuristics

| Paper size | Auto-selected tier | Model | VRAM |
|---|---|---|---|
| ≤ 8 pages | `fast` | `deepseek-r1:8b` | ~5 GB |
| ≤ 18 pages | `single` | `deepseek-r1:14b` | ~9 GB |
| > 18 pages | `xl_quality` | `nemotron-3-nano-30b-small` | ~24 GB |
| C++ sections | `xl_code` | `qwen3-coder:30b` | ~17 GB |

Both GPUs (RTX 5080 16 GB + RTX 3080 10 GB, ~26 GB combined) are required for xl-tier models.
Keep `OLLAMA_MAX_LOADED_MODELS=1` when running xl-tier to avoid VRAM contention.
