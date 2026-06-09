# Session — Live introspection → diagnosis → blast-radius caps

**Timestamp:** 2026-06-09T085635Z
**Host:** worlock (192.168.1.85)
**Continues:** [2026-06-09T071447Z_running_paper_processor_explained.md](2026-06-09T071447Z_running_paper_processor_explained.md)

---

## Arc

Started by explaining what the running `paper_processor.py` instance (PID 104025) was
doing and hosting that explanation as a Neo4j `:PPExplain` graph on the LAN. That
introspection surfaced a performance problem, which led to a root-cause investigation
and a set of "blast-radius" fixes. Two repos touched:

- **lobster-graph** (`~/programs/python_programs/paper_processor`, `origin` =
  `github.com/danindiana/lobster-graph`) — the pipeline source + the change record.
- **paper-processor-neo4j-explain** (public showcase) — the live-introspection /
  Neo4j / LAN-viz tooling and diagrams.

---

## 1 · Live-process introspection + LAN dashboard

- Built `:PPExplain` (and live `:PPLive`) Neo4j graphs from `/proc`, `ss`, `nvidia-smi`,
  and `_processed/` mtimes; rendered as vis-network pages served on `0.0.0.0:8686`
  (UFW LAN-only). See `assets/paper_processor_runtime/` in the showcase repo.
- Subsystem diagrams (pipeline, OCR fallback, Ollama backend, dashboard, `--workers`),
  a squarified **corpus treemap** (370 categories, agents=433 leads), and a
  self-refreshing `live.html`.
- **systemd timer** `pp-live-state.timer` (15s) keeps the live JSON + Neo4j fresh.

## 2 · Diagnosis — why it was crawling, then crashing

Introspection showed the runner CPU-bound (~1167%) on a 333-page book, GPUs nearly idle
→ a single section taking 25+ min. The full run then ended **5208 ✅ / 11 ❌**, all
`HTTP 500 … model runner has unexpectedly stopped` (resource limitations).

**Root cause (from the Ollama server log): VRAM exhaustion → runner OOM.** The 30B model
split across the dual GPUs loads with ~zero headroom — GPU1 needs **8.26 GiB** vs **8.2
available**. Any larger KV cache (longer context) tips the CUDA allocation over and the
runner dies mid-request. Compounded by **two** 30B models (`nemotron` prose +
`qwen3-coder:30b` C++) thrashing under `OLLAMA_MAX_LOADED_MODELS=2`.

## 3 · The four blast-radius caps

Recorded in `changes/2026-06-09T082314Z_blast-radius-caps/` (patch + reference copies +
README + four Graphviz diagrams in PNG/SVG).

1. **`select_model`** — `>200pg` docs route to `deepseek-r1:14b` (was nemotron-30b);
   KV cache stays GPU-resident. *(commit aed7463)*
2. **`--ocr-max-pages`** budget (default 40) + `OcrStats.ocr_capped`; caps fresh
   rasterisations on huge scanned books. *(aed7463)*
3. **C++ `code_model`** — `qwen3-coder:30b` → `qwen2.5-coder:14b`; removes the second
   30B. *(commit c823b7d)*
4. **Prose context cap** — `90k → 45k` chars (~22k → ~11k tok) to fit the 16384 ctx
   window; diagram slice `60k → 30k`. *(c823b7d)*

> Residual: mid-size failures (20–189pg) are only partially de-risked; the operational
> backstop is `OLLAMA_MAX_LOADED_MODELS=1`.

## 4 · Validation

Restarted in a separate instance (PID 542604). Confirmed live: **reprocessing
`1512_08976v1`** — one of the 11 previously-failed papers — with
`code_model: qwen2.5-coder:14b` and only **one** 30B resident in VRAM (no thrash).

---

## Provenance / pushes

- lobster-graph `main`: `73ce63a` (viz docs) → `aed7463` (caps 1–2) → `c823b7d`
  (caps 3–4) → `15172f8` (diagrams). Rebased onto `origin/main` (webgl hardfork) cleanly.
- paper-processor-neo4j-explain `main`: runtime snapshot, diagrams, systemd timer,
  `1c31a77` (README cross-link to the blast-radius record).

## Running state (stop when done)
- `pp-live-state.timer` (active), LAN viz on `:8686`, Neo4j Docker `paper-processor-neo4j`.
- Long-lived uncommitted edits remain in `neo4j_viz/` (`server.py`, `docker-compose.yml`)
  — staged nothing of those; never `git add -A` in this tree.
