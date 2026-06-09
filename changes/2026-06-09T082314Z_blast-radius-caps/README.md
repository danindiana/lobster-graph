# Blast-radius caps — 2026-06-09T082314Z

Four changes that cap the resource blast radius of large / scanned PDFs, motivated
by an investigation into 11 Ollama HTTP-500 failures in a 5219-paper run
(5208 ✅ / 11 ❌, 0.2%). Changes 1–2 shipped first; 3–4 followed in the same effort.

> The live, edited files are at the **repo root** (`paper_processor.py`,
> `ocr_fallback.py`) — this folder is a self-contained record: the unified
> `blast-radius-caps.patch` plus reference copies under `modified_files/`.

---

## Investigation — why the 11 papers failed

All 11 errors were Ollama `HTTP 500 … model runner has unexpectedly stopped, this
may be due to resource limitations` (two on `qwen3-coder:30b`, the rest on
`nemotron-3-nano-30b-small`).

**Root cause: VRAM exhaustion → runner OOM crash.** From the Ollama server log, the
30B model split across the dual GPUs leaves essentially zero headroom:

| GPU | weights | kv cache | compute graph | **total** | available |
|-----|---------|----------|---------------|-----------|-----------|
| GPU0 (RTX 5080) | 13.4 GiB | 0.82 | 0.54 | **14.76** | 15.0 GiB |
| GPU1 (RTX 3080) | 7.7 GiB | 0.37 | 0.19 | **8.26** | **8.2 GiB ← over** |

GPU1 is already oversubscribed at load. Any generation that grows the KV cache
beyond the baseline (a longer context) pushes the CUDA allocation past the limit and
the llama runner dies mid-request → HTTP 500. This is compounded by the pipeline
using **two distinct 30B models** (`nemotron` for prose + `qwen3-coder:30b` for the
C++ section) against `OLLAMA_MAX_LOADED_MODELS=2`, which thrashes VRAM on model
switch.

The failures span page counts (20 → 528), because what matters is the *context size*
that tips VRAM over the edge, not the page count alone:

```
2024.acl-long.356.pdf                20pg   qwen3-coder:30b  (runner terminated)
1512.08976v1.pdf                     26pg
on-the-universal-relation-ocr.pdf    24pg   17M, OCR-heavy   qwen3-coder:30b (load failed)
7039-1.pdf                           73pg
1989-801-05-Judy.pdf                113pg
machine-learning-cheat-sheet.pdf    135pg
9911…880.pdf                        150pg
menno_hellinga_thesis_final.pdf     189pg
Coecke-tutorial.pdf                 227pg   ← now routed to 14B
idealproblemsolver.pdf              269pg   ← now routed to 14B
complex.pdf                         528pg   26M ← now routed to 14B
```

---

## The changes

### 1. `select_model` — route >200-page docs to the lighter 14B tier
`paper_processor.py` · `TIER_BY_PAGES`

```python
(200, "xl_quality"),   # long paper — chunking handles context overflow
(999, "single"),       # >200pg book → deepseek-r1:14b, KV cache stays GPU-resident
```

The 30B with a near-max context overflows VRAM → CPU fallback (~12-core grind,
25+ min/section) or an OOM crash. The 9 GB 14B keeps its KV cache resident on-GPU.
Verified routing: `201pg → deepseek-r1:14b`, `333pg → deepseek-r1:14b`,
`≤200pg → nemotron-3-nano-30b` (unchanged).

### 2. OCR page-budget cap — `--ocr-max-pages` (default 40)
`ocr_fallback.py` + `paper_processor.py`

`extract_pages_with_ocr(..., max_ocr_pages=N)`: once **N** pages have been freshly
OCR'd, further low-text pages fall back to native text instead of rasterising. Caps
the blast radius on huge scanned books and shrinks the extracted-text volume (hence
context size) for image-only PDFs. Cached pages are free and don't count; `0` =
unlimited. New `OcrStats.ocr_capped` counter surfaces in the run log, e.g.
`333 pages → 10 native, 40 OCR'd, 5 cached, 278 budget-capped`.

---

### 3. C++ `code_model`: `qwen3-coder:30b` → `qwen2.5-coder:14b`
`paper_processor.py` · `CODE_MODEL = MODEL_TIERS["single_code"]`

The pipeline ran **two** 30B models — `nemotron` (prose) + `qwen3-coder:30b` (C++).
With `OLLAMA_MAX_LOADED_MODELS=2`, Ollama keeps both resident (~42 GB on 26.5 GB
VRAM → impossible), thrashing on every switch and OOM-crashing the runner (two of the
11 failures were on `qwen3-coder:30b`). Switching the C++ section to the 9 GB
`qwen2.5-coder:14b` lets the prose 30B + the coder 14B coexist on-GPU.

### 4. Prose context cap: 90k → 45k chars (diagram slice 60k → 30k)
`paper_processor.py` · `capped = context[:45_000]`

The model's context window is **16384 tokens**, but the prose sections were fed
`context[:90_000]` chars (~22k tokens) — overflowing the window and inflating the KV
cache (the exact allocation that tipped GPU1 over its 8.2 GiB). 45k chars (~11k
tokens) fits inside 16k with room for the prompt + output, so the KV cache stays small
and GPU-resident. The diagram section's slice drops from `capped[:60_000]` to
`capped[:30_000]` to match.

---

## Scope & residual risk

Changes **1–4** together address all four failure modes seen in the run: the >200-page
CPU-fallback/OOM (1), oversized OCR context (2), the dual-30B VRAM thrash (3), and the
context-window overflow that inflated the KV cache across *all* page sizes (4). The
remaining lever, if failures persist, is purely operational:

- Set `OLLAMA_MAX_LOADED_MODELS=1` (in the systemd override) so only one model is ever
  resident — belt-and-suspenders alongside change 3.

## Apply / revert

```bash
# already applied at repo root; to re-apply elsewhere:
git apply changes/2026-06-09T082314Z_blast-radius-caps/blast-radius-caps.patch
# revert:
git apply -R changes/2026-06-09T082314Z_blast-radius-caps/blast-radius-caps.patch
```
