# Resuming smrtevict, Model Tuning for a Small-VRAM Box, Overnight Batch Launch

**Date:** 2026-08-19
**Time:** 01:39:17
**Session ID:** 2026-08-19_013917

## Objective

Resume work after the prior Claude Code session was dropped mid-flight (uncommitted
fork cleanup, an untracked WIP file). Then get `paper_proc_smrtevict.py` — the
most up-to-date fork, layering proactive model eviction, batch tier-sorting, a
persisted default papers dir, and a dashboard-status print on top of
`paper_processor.py` — actually running end-to-end on this dev box, confirm it
builds the Neo4j graph, and launch an overnight batch over the full papers
directory.

## Part 1 — Resuming the dropped session

`git status` showed two unrelated groups of uncommitted changes:
1. Deletions of four merged `fork_*` directories and the
   `changes/2026-06-09T082314Z_blast-radius-caps/` snapshot, plus a stale
   `neo4j_viz/cosmos_build/` dir — all superseded/landed work.
2. Small fixes: `import fitz` → `import pymupdf as fitz` (the bare alias is
   deprecated as of pymupdf 1.28) in `ocr_fallback.py`, `paper_processor.py`,
   `paper_processor_dir.py`; a flaky OCR test fixed to render sample text with
   a real scalable font instead of PIL's ~10px default bitmap font; `pytest`/
   `Pillow` added to `requirements.txt`.
3. `paper_proc_smrtevict.py` — untracked, 1863 lines, syntactically valid, never
   git-tracked.

Split into two commits (`cff5a77` cleanup, `706ccb8` fitz/test fix), both
verified via `pytest tests/ -q` (52 passed, 6 xfailed) before committing.

Git identity wasn't configured in this repo clone (`git commit` failed with
"Author identity unknown"); set locally (not `--global`) to match the existing
commit history (`danindiana <benjamin@alphasort.com>`).

### Tracking the fork + a real bug found by testing it

Wrote `tests/test_smrtevict_helpers.py` for the new pure-logic helpers
(`_quick_page_count`, `_estimate_sort_cost`, config persistence,
`resolve_papers_dir`, `_ensure_model_exclusive`). This caught a real bug:
`_quick_page_count()` called `fitz.open()`, but the module only imported
`pymupdf` — no `fitz` name existed, so every call raised `NameError`, silently
swallowed by a broad `except Exception: return 0`. `--sort-batch` was
therefore always bucketing every paper as page-count 0. Fixed the same way as
the other three files. Committed `paper_proc_smrtevict.py` to git for the
first time (`6b41491`) so this class of work can no longer be dropped by a
session interruption.

## Part 2 — Hardware reality check

`paper_proc_smrtevict.py`'s `MODEL_TIERS` target a different machine
(RTX 5080 + RTX 3080, ~26 GB combined VRAM — see its `MODEL_GPU_LAYERS`
comments dated 2026-08-15). This session's machine (`dellt3600`) has a
**Quadro P4000 (8 GB) + GTX 1060 (6 GB), ~13.7 GB combined**, and neither
`gemma4:26b-a4b-it-q4_K_M` nor `qwen3.6:35b` is even pulled here. Real
end-to-end inference had to target a locally-pulled model instead.

Target directory resolved via `resolve_papers_dir()`: `~/Documents/AI-ML_Papers`,
604 PDFs, none processed yet. Neo4j confirmed up (`paper-processor-neo4j`
container, `neo4j/password123`), baseline 13 nodes (pre-existing, unrelated
data — used as the before/after diff for confirming graph sync). Tesseract
5.3.4 present for the OCR fallback path.

## Part 3 — Model selection: four candidates tested on real hardware

### `gpt-oss:20b` (user's first choice, ~13GB)
Ollama's auto-fit at this pipeline's real `num_ctx=32768` placed only
**68.6%** of weights on GPU (`size_vram` 9.15GB / `size` 13.34GB) — MoE
CPU-offloaded expert routing is especially slow. Mid-run, an **orphaned
background agent left over from the previous (dropped) session** — still
running independently, unbeknownst to this session — issued
`sudo systemctl restart ollama` while stress-testing `num_gpu` forcing on the
same model, and its restart landed the exact second this session's own C++-section
request was completing, producing `Response ended prematurely`. That agent's
own (independently arrived-at) findings matched this session's numbers almost
exactly and confirmed forcing full GPU residency
(`num_gpu=24`) **hard-crashes with a `cudaMalloc` OOM** at this pipeline's
real context — a genuine hardware ceiling on this box, not a config bug.
Even in its "working" degraded mode, throughput implied *days*, not one
night, for 604 papers. Ruled out.

### `deepseek-r1:7b` (~4.7GB weights, qwen2/GQA family)
100% VRAM-resident at full `num_ctx=32768` (11.63GB used of ~13.7GB pool).
Summary/logic/cpp/extras all completed correctly and the Neo4j sync worked.
The diagrams section failed: the model produced free-form prose, completely
ignoring the strict `===DIAGRAM_START...===`-delimited DOT-block format —
a classic reasoning-model instruction-following gap, not a parsing bug (the
raw output contained no DOT syntax at all).

### `phi4-mini:latest` (~2.5GB weight file, phi3 family, no GQA)
Smaller weights did **not** mean a better VRAM fit: at `num_ctx=32768` its
*loaded* footprint was **~20.67GB** (11.63GB VRAM / 56.3% resident) — worse
than `gpt-oss:20b`. Its architecture burns far more KV-cache per context
token than a GQA model. Even after adding `--ctx-tokens 16384` (100%
resident at that context, 11.38GB), the logic section entered a **non-terminating
generation loop** — 20,200+ tokens and climbing (already past its own context
window, context-shifting and losing its own prompt) for a section that should
be a few hundred to low-thousand tokens. Ruled out on quality/reliability
grounds, independent of VRAM.

### `gemma4:e2b-it-qat` (~1.7-3.6GB depending on context, MoE, instruction-tuned)
The winner. ~2B active params (of 4.6B total) keeps per-token memory traffic
low — **48.4 tok/s** raw throughput on the P4000 alone, vs. `deepseek-r1:7b`'s
~28.6 tok/s single-GPU / ~17.6 tok/s dual-GPU-split. 100% VRAM-resident even
at the pipeline's *full* default `num_ctx=32768` (3.57GB total — huge headroom
on this box). Being instruction-tuned rather than a reasoning-distill, it
followed the diagrams section's strict delimiter format on the first full-context
attempt (all 6 titles parsed correctly) — but all 6 DOT files failed to
*render* (see Part 5).

## Part 4 — `--ctx-tokens` and the single-GPU-placement discovery

Added a `--ctx-tokens` CLI flag (`Backend.default_ctx_tokens`) so `num_ctx`
is configurable per run instead of hardcoded to `32768` in every call
(`paper_proc_smrtevict.py`, `Backend.__init__`/`Backend.call`; the diagrams
call's redundant explicit `ctx_tokens=32768` was removed so it inherits the
same configurable default).

While chasing the user's throughput target (35-40 tok/s), found the real
lever: `CUDA_VISIBLE_DEVICES=0,1` is set at the systemd level
(`/etc/systemd/system/ollama.service.d/override.conf`), and any model whose
footprint exceeds *either single card's* capacity (P4000 7.8GB, GTX1060
5.9GB) gets tensor-split across both GPUs over PCIe — with no NVLink between
a Quadro P4000 and a GTX 1060, every cross-device layer boundary pays a PCIe
transfer. Confirmed via `nvidia-smi` polling during generation: reducing
`deepseek-r1:7b` to `--ctx-tokens 8192` (6.4GB total) let `OLLAMA_SCHED_SPREAD:false`
place it on the P4000 *alone* (GTX1060 stayed at 3MiB, idle) — throughput
jumped **17.6 → 28.6 tok/s (+62%)**, with the P4000 confirmed running at
near-max boost clock (~1500-1570 of 1708 MHz max) and 96-98% utilization
during generation (not throttled/misconfigured — genuinely near its ceiling
for a 7B Q4_K_M model). `gemma4:e2b-it-qat`'s MoE efficiency beat this outright
(48.4 tok/s) without even needing the context reduction.

## Part 5 — `DIAGRAM_PROMPT` DOT syntax bug

Root cause of `gemma4:e2b-it-qat`'s 6/6 "dot saved, SVG render failed"
diagrams: `DIAGRAM_PROMPT`'s "MANDATORY VISUAL STYLE" section described the
required attributes as informal shorthand —

```
graph-level:  bgcolor="black"
node default: style=filled, fillcolor="#0a0a0a", ...
```

— which the model copied **verbatim** into the DOT output. `graph-level:`
and `node default:` are not valid Graphviz syntax, so `dot` failed to parse
every single diagram. Rewrote the prompt to give literal, correct DOT lines
(`bgcolor="black";` / `node [style=filled, ...];`) and an explicit instruction
against pseudo-syntax. Verified via `--reprocess diagrams`: **6/6 parsed and
rendered** on the retry.

## Verification

Full single-paper run (`1301.3781.pdf`, the Word2Vec paper) with
`gemma4:e2b-it-qat` at the pipeline's default `--ctx-tokens` (32768):
- All 5 sections completed (summary, logic, cpp, diagrams ×6, extras).
- 100% VRAM-resident on the P4000 alone throughout (3.57GB / 3.57GB).
- Neo4j node count: 13 (baseline) → 26 after this paper synced.
- `_processed/1301_3781/` contains all expected `.md`/`.dot`/`.svg`/
  `metadata.json` artifacts.

## Files changed
- `changes/2026-06-09T082314Z_blast-radius-caps/`, `fork_2026-05-09T184929Z/`,
  `fork_2026-05-15T235801Z/`, `fork_gptOSS_textonly_2026-05-14T205304Z/`,
  `fork_model-select-ui_2026-05-25T150818Z/`, `neo4j_viz/cosmos_build/` —
  deleted (merged/stale). Commit `cff5a77`.
- `ocr_fallback.py`, `paper_processor.py`, `paper_processor_dir.py`,
  `requirements.txt`, `tests/test_ocr_fallback.py` — fitz→pymupdf alias fix,
  OCR test font fix, test deps. Commit `706ccb8`.
- `paper_proc_smrtevict.py` — tracked in git for the first time; fixed
  `_quick_page_count`'s missing `fitz` import. `tests/test_smrtevict_helpers.py`
  added. Commit `6b41491`.
- `paper_proc_smrtevict.py` — added `--ctx-tokens` CLI flag /
  `Backend.default_ctx_tokens`; fixed `DIAGRAM_PROMPT`'s invalid pseudo-syntax
  style guide. Commit `867feb7`.
- `/etc/update-motd.d/97-todo-git-push-auth` — added (system-level, via sudo,
  not part of the git repo): reminder that `origin` (github.com/danindiana/
  lobster-graph) has no stored push credentials in this environment (no `gh`
  CLI, no credential helper, no SSH remote) — pushing required a manually
  pasted token this session. Delete once auth is set up.
- All four repo commits pushed to `origin/main` after a GitHub token was
  provided interactively (no credential helper was configured beforehand).

## Status
✅ Dropped-session cleanup committed and pushed
✅ `paper_proc_smrtevict.py` tracked in git; real `_quick_page_count` bug fixed
✅ Model selection resolved for this specific box: `gemma4:e2b-it-qat`
   (ruled out: `gpt-oss:20b` — hardware VRAM ceiling; `deepseek-r1:7b` —
   diagrams format non-compliance; `phi4-mini` — non-terminating generation)
✅ `--ctx-tokens` CLI override added; single-GPU-placement PCIe-split
   throughput cost identified and documented in `MODEL_GPU_LAYERS`-adjacent
   code comments
✅ `DIAGRAM_PROMPT` DOT syntax bug fixed and verified (6/6 diagrams render)
✅ Full single-paper pipeline run verified end-to-end, Neo4j sync confirmed
✅ Overnight batch launched: `nohup .venv/bin/python paper_proc_smrtevict.py
   --model gemma4:e2b-it-qat`, PID 81524, `logs/smrtevict_overnight_20260819T012215.log`,
   all 604 papers in `~/Documents/AI-ML_Papers` queued (1 already-processed
   paper auto-skipped), workers=1
⚠️ Batch was still running as of session end — not yet confirmed complete
⚠️ `paper_proc_smrtevict.py` is still a fork, not yet merged into
   `paper_processor.py`/`paper_processor_dir.py`
⚠️ TODO left on system MOTD: git push auth for this repo clone is unresolved
   beyond tonight's one-time pasted token
