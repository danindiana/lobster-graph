# Thermal Shutdown: Stopping Both GPU Workers Pending Additional Cooling

**Date:** 2026-08-22
**Unix timestamp:** 1787454209
**Session folder:** `docs/sessions/1787454209_thermal-shutdown/`

## Why

GPU0 (Quadro P4000) held at 82°C under the new second-worker load (see
`1787453228_pin-model-quadro-p4000/` and `1787453721_gpu-temp-monitoring/`),
fan flat at 63% not still ramping. User decided the box needs additional
active cooling before running this load again, and is powering the machine
off entirely until a fan is installed. This session is the controlled
wind-down beforehand — not a crash, not a forced kill.

## What was stopped, in order, and how

1. **Both batch workers** (`paper_proc_smrtevict.py`, PID 160375 on GPU1 /
   1060, PID 678070 on GPU0 / P4000) — single `SIGTERM` each. The script
   installs real handlers for this (`_install_signal_handlers()`): first
   signal sets a shutdown flag, finishes the current section (or the current
   map-reduce chunk, then bails via `_ShutdownRequested`), checkpoints
   `metadata.json`, and exits; a **second** signal would force `os._exit(1)`
   immediately, skipping the checkpoint — so only one signal was sent to
   each, deliberately.
   - PID 160375 was mid-map-reduce on a large paper (chunk 44/117) when
     signaled — stopped cleanly with "0 section(s) saved" for that paper
     (it hadn't reached the first real section yet, so nothing partial was
     written; it'll restart that paper from scratch, not resume mid-way).
     Skipped its remaining 2407 queued papers and exited normally.
   - PID 678070 was fast-forwarding through already-completed/locked entries
     and exited normally at the same point in its own list.
   - Both printed final `✅ 3844/3844 papers processed successfully` tallies
     — this counts every visited entry (skip/lock-skip/real-work) with zero
     *errors*, not "3844 fully processed just now"; it's the loop's normal
     end-of-run summary regardless of whether the loop finished naturally or
     via shutdown.
   - Verified no stale `.processing.lock` files left behind anywhere in
     `_processed/` — the lock-release `finally` in `process()` held up under
     a real shutdown, not just under inspection.
2. **Two orphaned `neo4j_importer.py` children** (periodic sync subprocesses
   each worker had spawned, PIDs 680055/680116) — not killed directly; given
   a short grace window and both finished on their own before that window
   ran out. These are idempotent Cypher MERGEs, safe to interrupt if it ever
   comes to that, but didn't need to here.
3. **Both isolated Ollama backends** (`ollama serve` on 11435/GPU1 and
   11436/GPU0, PIDs 156820/677860 from `/tmp/ollama_gpu{0,1}.pid`) — plain
   `SIGTERM`, no in-flight requests to lose since the batch workers were
   already down. PID files cleaned up.
4. **`cosmos-dashboard.service`** then **`paper-processor-neo4j.service`**
   (systemd, in that order since the dashboard `Wants=` the DB) — via
   `systemctl stop`. The Neo4j stop runs `docker compose down` per the unit
   file, so the database got a clean container shutdown, not a yank.

Left running, deliberately out of scope: the shared system `ollama.service`
(port 11434, predates this work, used by other things on this host — was
already idle, 0% GPU, when checked) and the Open WebUI container (unrelated).
Neither contributes to the P4000/1060 heat and neither was touched.

## Verification

Post-shutdown sweep — no `paper_proc`/`neo4j_importer`/isolated-`ollama`
processes remaining; both systemd services `inactive`; Neo4j container gone
from `docker ps -a`. GPUs already cooling on their own:

| GPU | Before | Right after shutdown |
|---|---|---|
| GPU0 (P4000) | 82°C, 0% idle→72% util | 54°C, 0% util, 3 MiB, 7 W |
| GPU1 (1060)  | 65-68°C, 74-77% util   | 43°C, 0% util, 3 MiB, 13 W |

## Resume point (for whenever the fan is in and this restarts)

- GPU1 worker's in-flight paper at shutdown time had 0 sections checkpointed
  — it'll simply re-run from scratch on next launch, no special resume step
  needed.
- Backlog count as of the last full check (`session_1787450809/status.md`,
  ~21:40): 2277/3875 processed, since-boot rate ~2.97 papers/hour. This
  session's runs added some further progress before the shutdown but that
  wasn't re-tallied here — get a fresh count before estimating ETA next time
  rather than trusting either number blindly.
- To relaunch both workers later: `start_gpu1_backend.sh` +
  `start_gpu0_backend.sh` (both in their respective session folders) to
  bring the two isolated Ollama backends back up, then the two
  `nohup env OLLAMA_URL=... paper_proc_smrtevict.py ...` invocations from
  `1787453228_pin-model-quadro-p4000/`'s doc, once the new fan is confirmed
  actually cooling GPU0 under load (don't just re-launch and assume — check
  `gpu_temp_check.sh`'s log / a manual `nvidia-smi` pass after a few minutes
  under real load again).

## Status

✅ Both batch workers stopped via single graceful `SIGTERM` each, no forced
   kill, no data loss (checkpoint-clean, no stale locks)
✅ Both GPU-pinned Ollama backends stopped
✅ Neo4j (docker) and cosmos-dashboard stopped cleanly via systemd
✅ Confirmed nothing GPU-heavy left running; both GPUs already dropping in
   temperature within seconds of load removal
✅ Left the unrelated shared system `ollama.service` and Open WebUI alone —
   out of scope, not contributing to the heat problem
