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

## Follow-up changes (same session)

After the initial swap, two further changes landed (committed to the same
sub-folder):

1. **CODE_MODEL swap** `qwen2.5-coder:14b → qwen3:14b` (`single_code` tier),
   patch `code_model_qwen3-14b.patch`, commit `c10b45c`.
2. **GPU-layer cap** — added `MODEL_GPU_LAYERS = {CODE_MODEL: 12}` and threaded
   `options.num_gpu` into `Backend._call_ollama()` so the co-loaded code model
   can't evict gemma4. Patch `num_gpu_cap.patch`, commit `171d9ec`.

### Why the cap

gemma4 (~20.3 GB fully GPU-resident) + qwen3:14b (9.3 GB) ≈ 29.6 GB exceeds
the ~26 GB total VRAM (RTX 5080 16 GB + 3080 10 GB). Without a cap, loading
qwen3 for a C++ section triggered `Ollama HTTP 500: model failed to load
(resource limitations)` and risked trimming gemma4 to CPU. Per user direction,
gemma4 is kept maximally in VRAM; qwen3 tolerates a CPU/GPU blend (12 GPU
layers, rest on CPU / 128 GB RAM).

## VRAM verification (live, 2026-06-11 15:45:08)

Captured the moment a real C++ section loaded qwen3:14b alongside gemma4:

```
qwen3:14b                | vram  4.1 GB / total 12.9 GB   ← capped (12 GPU layers)
gemma4:26b-a4b-it-q4_K_M | vram 20.3 GB / total 20.3 GB   ← fully resident, NOT evicted

nvidia-smi:
  GPU0 (RTX 5080): 15359 / 16303 MiB
  GPU1 (RTX 3080):  9483 / 10240 MiB
```

**Result:** gemma4 stayed 100% in VRAM (`size_vram == size`); qwen3 ran a
CPU/GPU blend with only 4.1 GB on GPU. No OOM, no HTTP 500. The `num_gpu=12`
cap behaved exactly as intended. If qwen3 ever evicts gemma4 under more
pressure, lower `MODEL_GPU_LAYERS[CODE_MODEL]` toward 0.

The processor was stopped after this verification (user satisfied with
results).

## Final simplification — single model for prose + code

To cut VRAM/CPU churn further, `CODE_MODEL` was repointed from `qwen3:14b` to
`MODEL_TIERS["xl_quality"]` (gemma4). Rationale: for the dominant case
(≥35-page papers, where gemma4 is already the prose model) the C++ section now
reuses the **single warm, fully-resident** gemma4 — no second model load, no
eviction, no CPU-offloaded layers, no num_gpu juggling. gemma4:26b handles the
extracted C++ examples adequately.

Consequently the `num_gpu` cap is now moot and `MODEL_GPU_LAYERS` was emptied
to `{}` (mechanism kept for future use; the gemma4 entry would have wrongly
throttled the prose model). Patch `code_model_to_gemma4.patch`.

**Residual nuance:** for small papers (≤18 pp) prose still uses a smaller
deepseek tier, so a small paper *with* a C++ section would load gemma4 (20 GB)
alongside the small prose model — a rare two-model case that still fits more
comfortably than the old layout. Not optimized; flagged only.
