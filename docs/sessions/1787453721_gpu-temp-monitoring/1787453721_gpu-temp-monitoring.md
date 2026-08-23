# Periodic GPU Temperature Check

**Date:** 2026-08-22
**Unix timestamp:** 1787453721
**Session folder:** `docs/sessions/1787453721_gpu-temp-monitoring/`

## Why

Follow-up to `docs/sessions/1787453228_pin-model-quadro-p4000/` (second batch
worker added on the Quadro P4000, GPU0). A manual check shortly after that
worker started sustained real load showed GPU0 running noticeably hotter
than GPU1 under comparable utilization:

| GPU | Temp | Fan | Util |
|---|---|---|---|
| GPU0 (P4000) | 82°C | 63% (flat across 20s of sampling — not still ramping) | 69-76% |
| GPU1 (1060)  | 68°C | 51% | 77% |

82°C is inside the P4000's rated range (~94°C max) and wasn't still
climbing, so nothing acted on immediately — but GPU0 had been idle at
46-47°C for the prior 3+ days before this, so this was the first time it saw
sustained multi-day load, and running the hotter of the two cards at 82°C
continuously for the ~2-3 weeks this backlog is expected to take is worth
tracking rather than assuming away. Cause not confirmed (blower-style Quadro
cooling, possibly restricted intake/exhaust from the adjacent 1060 in the
same case) — this session adds observability, not a fix.

## What was built

### `scripts/gpu_temp_check.sh`

Queries `nvidia-smi` for both GPUs (index, name, temp, fan%, power draw,
util%) and appends one CSV row per GPU per run to
`~/programs/paper_proc/logs/gpu_temp.csv`. On a GPU crossing `WARN_C=85`,
broadcasts a `wall` message once (state tracked via a marker file in
`/tmp/gpu_temp_check_state/`, so a sustained hot streak doesn't re-broadcast
every run) and re-arms once that GPU drops back below the threshold.

### Cron schedule

Added to `ricky`'s crontab (existing duckdns entry untouched):

```
*/5 * * * * /home/ricky/programs/paper_proc/scripts/gpu_temp_check.sh >/dev/null 2>&1
```

5-minute interval matches the existing duckdns cron cadence on this host —
thermal state doesn't change fast enough to need finer granularity, and this
is cheap enough (~5s `nvidia-smi` call) not to worry about tightening it.

## Verification

Ran manually once before scheduling — produced:

```
timestamp,gpu_index,name,temp_c,fan_pct,power_w,util_pct
2026-08-22 21:55:33,0,Quadro P4000,82,64,66.15,74
2026-08-22 21:55:33,1,NVIDIA GeForce GTX 1060 6GB,68,51,81.78,77
```

## Status

✅ `scripts/gpu_temp_check.sh` built and manually verified
✅ Scheduled every 5 min via cron, existing crontab entries untouched
✅ Log at `~/programs/paper_proc/logs/gpu_temp.csv`, growing ~1 row/GPU/5min
   (negligible size — no rotation needed at this rate for the backlog's
   expected multi-week duration)
⚠️ `WARN_C=85` is a starting guess (P4000 was observed steady at 82°C, rated
   to ~94°C) — not vendor-verified as *the* right threshold for this specific
   card/case airflow, just set above the one hot reading seen so far with
   some margin. Revisit if it fires spuriously or never fires despite GPU0
   staying pinned near 82-85°C for days.
⚠️ Root cause of GPU0 running hotter than GPU1 (fan curve ceiling vs. case
   airflow vs. just this card's normal blower behavior under load) not
   diagnosed — this only adds visibility, doesn't address a cause.
