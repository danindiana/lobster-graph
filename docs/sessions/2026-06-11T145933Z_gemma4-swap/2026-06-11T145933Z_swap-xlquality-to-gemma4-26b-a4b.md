# Session — Swap `xl_quality` from nemotron-3-nano-30b → gemma4:26b-a4b-it-q4_K_M

**Timestamp:** 2026-06-11T145933Z
**Machine:** worlock
**File touched:** `paper_processor.py`

## Context

A live `paper_processor.py` instance (PID 88498, started 13:21) was running
the `xl_quality` default model:

```
nemotron-3-nano-30b-small:latest   99f84e11c973   26 GB   11%/89% CPU/GPU
ctx 32768   ~59 minutes ETA
```

User asked to swap the `xl_quality` default to **`gemma4:26b-a4b-it-q4_K_M`**
(MoE a4b, ~17 GB in `ollama list`), which fits the dual-GPU tier with more
headroom than the 24–26 GB nemotron footprint.

## Changes (`paper_processor.py`)

| Line | Before | After |
|------|--------|-------|
| `MODEL_TIERS["xl_quality"]` | `nemotron-3-nano-30b-small:latest` | `gemma4:26b-a4b-it-q4_K_M` |
| `KNOWN_GOOD_MODELS` default | nemotron (default=True) | gemma4 (default=True); nemotron retained as text-only alt (default=False) |
| `--help` page-count map | `> 18 pages → nemotron-3-nano-30b-small (~24 GB)` | `> 18 pages → gemma4:26b-a4b-it-q4_K_M (~17 GB)` |

`CODE_MODEL` (qwen2.5-coder:14b) and the >200-page → `single` routing are
unchanged. `ast.parse` syntax check passed.

## Important: the running instance

PID 88498 already loaded nemotron into VRAM and will **finish its current job
on nemotron** (~59 min ETA). Python re-reads `MODEL_TIERS` only at process
start, so the swap takes effect on the **next** launch of `paper_processor.py`,
not the in-flight run. No restart performed — left to drain naturally.

## VRAM note

gemma4:26b-a4b-it-q4_K_M (~17 GB) leaves more room alongside the
qwen2.5-coder:14b `CODE_MODEL` (~9 GB) under `OLLAMA_MAX_LOADED_MODELS=2`,
reducing the VRAM-thrash / CPU-fallback risk that drove the >200-page
down-routing. gemma4 is multimodal but vision is unused by the pipeline
(text-only sectioning), same as the prior gemma4 default before nemotron.

## Verify next run

```bash
python paper_processor.py -s   # selector should show gemma4:26b-a4b-it-q4_K_M as default
```
