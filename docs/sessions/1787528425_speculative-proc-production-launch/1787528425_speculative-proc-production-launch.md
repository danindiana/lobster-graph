# speculative-paper-proc Goes Live: Production Batch Stopped, Neo4j Sync Fixed, Sibling Project Launched

**Date:** 2026-08-23
**Unix timestamp:** 1787528425
**Session folder:** `docs/sessions/1787528425_speculative-proc-production-launch/`

## What changed in this repo's own state

- **`paper_proc_smrtevict.py`'s GTX1060 batch (PID 29032) was stopped**
  (single `SIGTERM`, clean checkpoint, no stale lock) at the user's request
  to free the GPU for fast testing of the sibling
  `~/programs/speculative-paper-proc` project. **It has not been
  restarted.** Backlog was at 2279/3875 when stopped.
- **`neo4j_viz/neo4j_importer.py` got a real perf fix** (commit `383802c`):
  it was re-parsing and re-syncing *every* paper in `_processed/` on
  *every* invocation, with no change-detection. At this repo's real corpus
  size (~2280 papers), a full pass took over an hour — meaning this
  repo's own 5-minute periodic sync (`_sync_to_neo4j`) had been silently
  timing out and doing nothing for an unknown but likely substantial
  period. Fixed with a one-query pre-check that skips papers whose
  `metadata.json` `paper_hash` already matches what's in the graph. A
  real backlog of ~46+ completed-but-never-synced papers got caught up
  the same session this was found. Full account, with real timing
  numbers, in `speculative-paper-proc/docs/FINDINGS.md`.

## The sibling project

`~/programs/speculative-paper-proc` (public:
https://github.com/danindiana/speculative-paper-proc) — forked after this
repo's own model-search investigation
(`docs/sessions/1787518795_qwen25-benchmark-and-fork/`) established that
no single local model is both fast and diagram-reliable on this hardware.
It splits the problem across both GPUs instead: P4000 drafts every paper
fast and unverified, GTX1060 (`gemma4:e2b-it-qat`, this repo's own
verify-tier model) checks every section/diagram before anything is
written, using the same `.processing.lock` and slug convention as this
repo so the two tools can safely share one `_processed/` tree.

**Now running for real**: launched via `nohup`, both GPU-pinned backends
(the same isolated `start_gpu{0,1}_backend.sh` instances this repo uses),
against the full production corpus, syncing into the *same* Neo4j graph
this repo populates. Verified end-to-end before launch: a real paper
(`andrew_glew_1.pdf`) produced a real `Paper` node with genuine
motivation/methodology/contributions text and real linked
Diagram/CodeSnippet/Algorithm nodes — not just "the importer exited 0".

**New thermal finding worth knowing about**: the P4000 running
`qwen2.5:0.5b` (that project's draft model) plateaued at 71°C under active
7-minute monitoring — a materially cooler profile than this repo's own
`gemma4` solo/dual-worker runs, which climbed continuously to 82°C (see
`docs/sessions/1787454209_thermal-shutdown/`). This is model-specific, not
a general "P4000 runs cool now" — don't assume it applies to a future
`gemma4`-on-P4000 relaunch of this repo's own batch.

## Resuming this repo's own GTX1060 batch later

Not done in this session, deliberately. When ready:
1. Check whether `speculative-paper-proc`'s own run is still using the
   GTX1060 verify backend (port 11435) — the two can share it (verified
   working, just slower for both), or stop one first if full throughput on
   one is preferred.
2. Relaunch per `ascii_tree.md`'s documented command (or the
   `94-paperproc-howto` motd entry).
3. Both tools' skip-checks and `.processing.lock` mean it's safe to run
   either, both, or alternate between them — no manual coordination step
   needed beyond deciding which you want using the GPU right now.

## Status
✅ Real, unmodified perf fix landed in the shared `neo4j_importer.py`,
   pushed
✅ Sibling project confirmed working end-to-end against real production
   infrastructure (same Neo4j graph, same corpus, same locking convention)
✅ New P4000 thermal data point recorded (model-specific, not a general
   revision of the existing `gemma4` finding)
⚠️ This repo's own GTX1060 batch remains stopped at 2279/3875 — resuming
   it is a separate, not-yet-made decision
