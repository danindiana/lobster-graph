# Model Tier Restriction (gemma4 + nemotron3) & 100% GPU Fit-Target Fix

**Date:** 2026-08-06
**Time:** 18:37:47
**Session ID:** 2026-08-06_183747

## Objective

Two related changes to the local-Ollama inference path:
1. Restrict `paper_processor_dir.py`'s model roster to exactly two models for now:
   `gemma4:26b-a4b-it-q4_K_M` (fast/lighter default) and `nemotron3:33b` (strong
   chain-of-thought, for long/complex papers).
2. Investigate why `gemma4:26b-a4b-it-q4_K_M` loaded at `4%/96% CPU/GPU` instead
   of `100% GPU` despite having two GPUs with combined free VRAM well above the
   model's footprint, and fix it if the CPU spillover was avoidable.

## Part 1 — Model roster restriction

### Change
`MODEL_TIERS` previously held 8 keys spanning several DeepSeek/Qwen/Devstral
models across VRAM tiers (dual-GPU, mid, single-GPU, fast-fallback). Collapsed
down to the two active models, reusing the existing `xl_quality` / `xl_reason`
key names so nothing else in the codebase (`CODE_MODEL`, `select_model()`,
the `-s`/`-c` interactive pickers, `main()`) needed to change beyond the two
lookup dicts and the page-count routing table:

```python
MODEL_TIERS = {
    "xl_quality":   "gemma4:26b-a4b-it-q4_K_M",     # MoE a4b (~17 GB) — default, fast
    "xl_reason":    "nemotron3:33b",                # ~27 GB — strong chain-of-thought
}

TIER_BY_PAGES: List[Tuple[int, str]] = [
    (35,  "xl_quality"),    # short-to-standard paper — fast, lighter model
    (200, "xl_reason"),     # long paper — chunked context benefits from stronger reasoning
    (999, "xl_quality"),    # very large book → lighter model, stays GPU-resident
]
```

`KNOWN_GOOD_MODELS` (`-s`) and `KNOWN_GOOD_CODE_MODELS` (`-c`) were trimmed to
the same two entries. `CODE_MODEL` is unchanged — it still reuses
`MODEL_TIERS["xl_quality"]` (gemma4) for the C++ section, per the existing
single-resident-model rationale.

### Rationale for the page-count split
- ≤35 pages: gemma4 — short/standard papers don't need the heavier reasoning
  model; keeps things fast.
- 36–200 pages: nemotron3 — long papers get map-reduce chunked context, where
  stronger reasoning pays off most.
- \>200 pages: back to gemma4 — same logic as the old `single` tier fallback:
  a 27 GB model with a near-max context risks CPU-offloaded layers and a
  25+ min/section grind on huge books; the lighter model stays GPU-resident.

### Note
`nemotron3:33b` (~27 GB) exceeds this machine's combined VRAM (RTX 5080 16 GB +
RTX 3080 10 GB ≈ 26.5 GB total, before other GPU consumers like the desktop
compositor), so it will always run with some CPU-offloaded layers regardless
of the fit-target change in Part 2 below. That's expected and unrelated to
the 100%-GPU fix, which only applies to models that actually fit.

## Part 2 — 100% GPU fit-target fix

### Symptom
```
ollama ps
NAME                        ID              SIZE     PROCESSOR         CONTEXT
gemma4:26b-a4b-it-q4_K_M    5571076f3d70    18 GB    4%/96% CPU/GPU    32768
```
~720 MB of the 18 GB model sat host-mapped (CPU) instead of in VRAM.

### Investigation
1. Confirmed hardware: RTX 5080 (16303 MiB) + RTX 3080 (10240 MiB), ~26.5 GB
   combined, only ~6 GB free at the time due to desktop/Chrome/mpv usage on
   top of the model itself.
2. Evicted the model and reloaded it with an explicit `options.num_gpu=30`
   (the model's full `block_count`) via a raw `/api/generate` call. Result:
   **identical** `4%/96%` split. This ruled out the classic "not enough
   layers requested" explanation — `num_gpu` had no effect.
3. Read `journalctl -u ollama` during the reload. This Ollama build (0.32.5)
   launches `llama-server` with **no** `-ngl`/`--n-cpu-moe` flags at all —
   layer/tensor placement is decided entirely by llama.cpp's own newer
   auto-fit engine (`LLAMA_ARG_FIT`, default `on`), which runs *inside*
   `llama-server` and ignores Ollama's legacy `num_gpu` option when active.
4. The fit engine's own log line was the key clue:
   ```
   common_params_fit_impl: projected to use 17176 MiB of device memory vs. 24402 MiB of free device memory
   ```
   7 GB of slack was available, yet it *still* chose to leave ~577 MB
   (an embedding/MoE-adjacent tensor) host-mapped. This meant the CPU
   spillover was a **deliberate safety-margin reservation**, not a real
   VRAM shortage — `num_gpu` tuning in `paper_processor_dir.py` could never
   have fixed it.
5. `llama-server --help` confirmed the relevant knobs live in llama.cpp
   itself (`--cpu-moe`, `--n-cpu-moe`), and `ollama serve --help` revealed
   two environment variables not previously set on this box:
   - `LLAMA_ARG_FIT` (default `on`) — enable/disable llama.cpp's auto-fit
   - `LLAMA_ARG_FIT_TARGET` — reserved free-VRAM margin per device (MiB),
     separate from Ollama's own (already-zeroed) `OLLAMA_GPU_OVERHEAD`.

### Fix
Added one line to the global Ollama systemd override and restarted the
service:

```ini
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
...
Environment="LLAMA_ARG_FIT_TARGET=0"
```

```bash
sudo cp override.conf override.conf.bak-<timestamp>   # backup taken first
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Verification
```
ollama ps
NAME                        ID              SIZE     PROCESSOR    CONTEXT
gemma4:26b-a4b-it-q4_K_M    5571076f3d70    17 GB    100% GPU     32768
```
- GPU1 (RTX 3080, previously the underused card) absorbed the extra ~1.3 GB,
  moving from ~4 GB to ~7.4 GB used — confirming the spare 3080 headroom is
  now actually used instead of falling back to host RAM.
- Sanity-checked generation quality after the change: a test prompt returned
  a clean `done_reason: stop` completion, no truncation or garbage output.

### Scope / risk
This is a **global, service-wide** change — it applies to every model Ollama
loads for every consumer on this machine (paper-processor, OpenWebUI, the
Ollama Delegation Toolkit, etc.), not just gemma4. Restarting the service
evicted whatever was loaded at the time. Zeroing the fit-target margin trades
a small safety cushion (headroom for KV-cache growth mid-generation) for full
VRAM utilization; if OOM crashes appear under real workloads on models
running close to capacity, revert via the backup file:
```bash
sudo cp /etc/systemd/system/ollama.service.d/override.conf.bak-<timestamp> \
        /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

## Files changed
- `paper_processor_dir.py` — `MODEL_TIERS`, `TIER_BY_PAGES`,
  `KNOWN_GOOD_MODELS`, `KNOWN_GOOD_CODE_MODELS`, CLI epilog/usage text.
- `/etc/systemd/system/ollama.service.d/override.conf` — added
  `LLAMA_ARG_FIT_TARGET=0` (backup left alongside as `.bak-<timestamp>`).
- `docs/ARCHITECTURE.md` — model auto-selection table updated to match.
- `docs/TROUBLESHOOTING.md` — GPU VRAM table updated; new entry added for
  the "stuck below 100% GPU despite free VRAM" symptom.

## Status
✅ Model roster restricted to gemma4 + nemotron3, page-count routing verified by reading code paths
✅ Root cause of the 4% CPU split identified (llama.cpp auto-fit safety margin, not scarcity)
✅ `LLAMA_ARG_FIT_TARGET=0` applied and verified — gemma4 now loads at 100% GPU
✅ Backup of the original override.conf preserved for rollback
⚠️ nemotron3:33b will still partially CPU-offload on this hardware (27 GB model > 26.5 GB combined VRAM) — expected, unrelated to this fix
