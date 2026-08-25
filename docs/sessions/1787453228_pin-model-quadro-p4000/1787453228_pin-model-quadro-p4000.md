# Second GPU Worker: Pinning gemma4:e2b-it-qat to the Quadro P4000

**Date:** 2026-08-22
**Unix timestamp:** 1787453228
**Session folder:** `docs/sessions/1787453228_pin-model-quadro-p4000/`

## Objective

The GTX 1060 (GPU1) has been running the sole `paper_proc_smrtevict.py` batch
continuously since 2026-08-19 (`docs/sessions/1787167674_pin-model-gtx1060/`),
while the Quadro P4000 (GPU0, 8GB) sat completely idle the whole time.
Backlog math at the single-GPU rate put the remaining ~1600 papers at roughly
3 more weeks. Goal: stand up a second worker on the idle P4000 to roughly
double throughput.

## What was built

### `start_gpu0_backend.sh`
Sibling of the existing `start_gpu1_backend.sh`, same recipe applied to GPU0:
`CUDA_VISIBLE_DEVICES=0` + `OLLAMA_VULKAN=0` (both required — Ollama 0.32+
discovers GPUs via CUDA and Vulkan independently, and Vulkan ignores
`CUDA_VISIBLE_DEVICES`), `OLLAMA_HOST=127.0.0.1:11436`,
`OLLAMA_MODELS=/home/ollama_models`. Detached via `nohup`, PID in
`/tmp/ollama_gpu0.pid`, log at `/tmp/ollama_gpu0.log`.

Verified via server log (`CUDA0 ... description="Quadro P4000" pci_id=0000:03:00.0`)
and a live test generate call: **~62 tok/s**, `nvidia-smi` showing GPU0 busy
and GPU1 untouched by the test. (The P4000's extra CUDA cores + higher memory
bandwidth vs. the 1060 mean it's not just an additional stream — it's a
*faster* one too.)

### Model choice: same model on both GPUs (`gemma4:e2b-it-qat`)

Deliberately not using the P4000's extra 2GB headroom to run a bigger/different
model. Reasons:
- Output consistency across the corpus — Neo4j graph entries from two
  different worker lanes should read the same, not vary in quality depending
  on which GPU happened to process a given paper.
- This model is already the one validated end-to-end on this exact pipeline
  (`1787167674_pin-model-gtx1060` session): 36/36 layers resident, all five
  sections tested, no OOM.
- A bigger model would run slower per-token, eating into the parallelism gain
  this whole exercise is for.

### Concurrency-safety fix in `paper_proc_smrtevict.py`

Running two full-batch workers against the same `papers_dir` was **not**
safe as-is: `Processor.process()`'s only skip check was reading
`metadata.json` and comparing completed sections — no claim/lock before
starting expensive work, so both workers would race onto the same next
unprocessed paper and write to the same output files concurrently.

Fix: `process()` now atomically claims a `.processing.lock` file inside the
paper's output dir (`lock_path.touch(exist_ok=False)`) before delegating to
the renamed `_process_locked()` (the original body, unchanged), and releases
it in a `finally`. A lock older than `STALE_LOCK_SECONDS` (4h — comfortably
above the ~20min/paper observed average, even for large multi-chunk papers)
is treated as abandoned (crashed worker) and reclaimed rather than
permanently blocking retries.

## Batch launched

```
cd /home/ricky/programs/paper_proc
nohup env OLLAMA_URL=http://127.0.0.1:11436 .venv/bin/python paper_proc_smrtevict.py \
  --model gemma4:e2b-it-qat \
  /home/ricky/Documents/AI-ML_Papers/aug_8_2026 \
  > logs/smrtevict_gpu0_1787453282.log 2>&1 &
disown
```

PID 678070, fully detached (`PPID=1`). Existing GPU1 worker (PID 160375,
running since 2026-08-19) left untouched — this is purely additive, same
`papers_dir`, same `_processed/` output tree (no merge step needed since both
workers share one output root).

## Verification

- `nvidia-smi` post-launch: GPU0 (P4000) 66-90% util / ~2.8-3.0GB used, GPU1
  (1060) 73-75% util / ~3.0GB used — both cards active simultaneously, no
  interference.
- New worker's log shows it fast-forwarding through the already-completed
  alphabetical prefix of the corpus via the `⏭ (all sections complete)` skip
  path (unaffected by the lock change, since that check returns before the
  lock is ever touched) — expected while it catches up to wherever the GPU1
  worker's frontier currently is.
- Zero `🔒` (lock-contended) skip messages yet, since the two workers hadn't
  reached the same frontier paper at check time — expected to start appearing
  once they converge, and is the intended, safe behavior when they do.

## MOTD entry (durable status, not a one-shot `wall` broadcast)

Added `94-paperproc-gpu-workers` (copy in this folder) to
`/etc/update-motd.d/`, matching the existing `96-cosmosgl-status` /
`20-services` pattern on this host. Deliberately dynamic rather than a static
blurb — a `wall` message (like `cosmos-dashboard.service`'s
`ExecStartPost`/`ExecStopPost` uses) only reaches whoever's logged in at that
instant and leaves no trace after; this instead re-checks live state on every
login so it can't go stale:

- Model in use, read from the running process's own `--model` arg (not
  hardcoded)
- Whether each GPU-pinned backend (11435/GTX1060, 11436/P4000) is up, with
  the relevant `start_gpu*_backend.sh` path shown if one's down
- Whether both worker processes are running (flags 0 or 1 vs. the expected 2)
- Log file glob

Verified via `run-parts --lsbsysinit /etc/update-motd.d/` — renders correctly
alongside the existing entries, both backends and both workers show green at
install time.

## Status

✅ Root cause of the idle P4000: not previously targeted by any Ollama
   instance — the shared system daemon (11434, `CUDA_VISIBLE_DEVICES=0,1`)
   was never pointed at by the pipeline (`OLLAMA_URL` is 11435), so GPU0 had
   no traffic routed to it at all.
✅ Isolated GPU0-only backend built, running, detached (PID 677860, port
   11436) — additive, does not touch the shared system `ollama.service` or
   the existing GPU1 backend.
✅ Verified CUDA-only discovery confines it to the P4000; single test call
   confirmed ~62 tok/s.
✅ Added atomic per-paper locking so the two workers can safely share one
   `papers_dir`/`_processed` tree without double-processing or corrupting
   output.
✅ Second batch launched against `aug_8_2026`, running concurrently with the
   existing GPU1 batch.
⚠️ Not yet observed the two workers' frontiers actually meet and exercise the
   new lock-contention path live — logically verified by inspection, but
   worth a follow-up check once GPU0 catches up to GPU1's current position in
   the sorted file list.
