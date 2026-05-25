# Session: paper_processor Vision Model Fix + Model Selector UI
**Date:** 2026-05-25T150818  
**Working dir:** `/home/jeb/programs/python_programs/paper_processor/`  
**Repo:** https://github.com/danindiana/paper-processor

---

## Part 1 — Vision Model Audit

### Finding

`paper_processor.py` was configured with `gemma4:31b-it-q4_K_M` as the `xl_quality` model tier.  
`gemma4` is a **multimodal (vision) model** — but the pipeline is **text-only**:

- PDF pages extracted via `fitz.get_text("text")` — images discarded
- Ollama payload contains only `{"model": ..., "prompt": str, ...}` — no `images` field
- Vision weights (~6 GB overhead) loaded and wasted every run

A prior fork (`fork_gptOSS_textonly_2026-05-14T205304Z/`) had already noted this.

### Decision

Replace `gemma4:31b-it-q4_K_M` with `nemotron-3-nano-30b-small:latest` (24 GB, SSM/attention hybrid, text-only, already on-disk).

### Changes

| File | Change |
|------|--------|
| `paper_processor.py` line 85 | `xl_quality` → `nemotron-3-nano-30b-small:latest` |
| `paper_processor.py` line 991 | Help-text updated to match |

### Verification

Ran `entropy-16-03670.pdf` (19 pages, xl_quality tier) through the pipeline. Monitor confirmed:
```
Default   : nemotron-3-nano-30b-small:latest
chunk 1/2 ✓
chunk 2/2 ✓
```
Full output written to `/home/jeb/Documents/_processed/entropy-16-03670/`.

---

## Part 2 — Interactive Model Selector (-s flag)

### Feature

Added `KNOWN_GOOD_MODELS` list and `prompt_model_selection()` function to `paper_processor.py`.  
New `--select-model` / `-s` CLI flag triggers a numbered menu before processing:

```
  Known-good models — select for this run:
  #    Model                                           VRAM      Role
  ────────────────────────────────────────────────────────────────────────────────
     1 nemotron-3-nano-30b-small:latest                ~24 GB    xl_quality — current default  ◀
     2 deepseek-r1:32b                                 ~19 GB    xl_reason — chain-of-thought
     3 deepseek-r1:14b-qwen-distill-q8_0              ~15 GB    mid_reason — Q8 fidelity
     4 devstral:24b                                    ~14 GB    mid_code — code + text
     5 qwen3.6:35b                                     ~23 GB    xl — strong general reasoning
     6 deepseek-r1:14b                                 ~9 GB     single — reliable, 9 GB
     7 gpt-oss:20b                                     ~13 GB    text-only alternative
     8 deepseek-r1:8b                                  ~5 GB     fast — quick fallback
```

TTY guard: if stdin is not a terminal (batch/pipe), flag is silently ignored.  
Fork snapshot: `fork_model-select-ui_2026-05-25T150818Z/` created before changes.

**Commit:** `7916b11`

### Verification

Live test confirmed selection flows correctly:
```
Enter number [1]: 4  →  devstral:24b
Default   : devstral:24b
```

---

## Part 3 — Standalone Launcher (pp.py)

### Problem

The `-s` flag requires running from the main project dir; running from a fork dir fails with
`unrecognized arguments: -s` since forks are frozen snapshots.

### Solution

`pp.py` — a 16-line launcher that always shows the model picker then `os.execv`s into
`paper_processor.py` with `--model <chosen>` prepended. Works from any directory.

```bash
# From anywhere:
python3 /home/jeb/programs/python_programs/paper_processor/pp.py --paper foo.pdf /home/jeb/Documents

# From project dir:
./pp.py --paper foo.pdf --verbose /home/jeb/Documents
```

**Commit:** `220cef7` — pushed to `origin/main`

---

---

## Part 4 — Code Model Selector (-c flag)

### Feature

Extended the interactive selector pattern to the C++ section model (`CODE_MODEL`).  
Previously `--model` overrode both the main model AND the code model with no independent path.

Changes:
- `KNOWN_GOOD_CODE_MODELS` list added (6 code-specialist models)
- `prompt_model_selection()` generalised to accept any model list
- `--code-model MODEL` flag for direct non-interactive override
- `--select-code-model` / `-c` flag for interactive picker
- `Pipeline.__init__` gains `forced_code_model` param
- Priority chain: `forced_code_model → forced_model → CODE_MODEL`
- `pp.py` updated to show both pickers in sequence on launch

**Commit:** `cd7dae7`

### Verification (live run with pp.py)

`./pp.py --paper 2407.13885v1.pdf --verbose /home/jeb/Documents`

Selections made:
- Main model: `devstral:24b`
- Code model: `deepseek-coder-v2:16b`

Process command confirmed:
```
paper_processor.py --model devstral:24b --code-model deepseek-coder-v2:16b ...
```

Monitor trace:
```
devstral:24b loaded    → sections 1, 2 written (summary, logic)
devstral:24b unloaded  → deepseek-coder-v2:16b loaded for C++ section
deepseek-coder-v2:16b  → section 3 written (C++ examples)
devstral:24b reloaded  → section 4 (extras/diagrams) in progress
```

Model swap between main/code sections confirmed working correctly.

---

## Commits This Session

| Hash | Message |
|------|---------|
| `7916b11` | feat: interactive model selector (-s / --select-model) + fork snapshot |
| `220cef7` | feat: add pp.py standalone model-picker launcher |
| `cd7dae7` | feat: interactive code model selector (-c / --select-code-model) |
| `275b9c3` | docs: add session log |
| `5ff7025` | docs: add LESSONS_LEARNED.md |
