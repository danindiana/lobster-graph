# Operator Quick-Start: motd Entry + Repeating Wall Broadcast

**Date:** 2026-08-23
**Unix timestamp:** 1787515405
**Session folder:** `docs/sessions/1787515405_operator-howto-motd/`

## Why

Following the model throughput benchmark (see
`~/Documents/claude_creations/session_1787507023_paper-proc-investigation/`),
a human operator needs a concrete, always-visible answer to "how do I
actually run `paper_proc_smrtevict.py`?" — not just the full reference doc.
Two delivery mechanisms, matching this host's existing patterns:
- **motd** (`/etc/update-motd.d/`) — shown once per login, like the existing
  `94-paperproc-gpu-workers` (live status) and `96-cosmosgl-status`.
- **`wall`, cron'd** — for an operator already logged in, since motd only
  fires at login. No existing precedent in this repo for a periodic
  *reminder* wall broadcast (the existing `gpu_temp_check.sh` wall is
  event-triggered on a >85°C crossing, not a recurring cadence).

## What was built

### `/etc/update-motd.d/94-paperproc-howto` (copy in this folder)
Static content (unlike `94-paperproc-gpu-workers`'s live-checked state) —
this is instructions, not status, so it doesn't need to re-derive anything
at render time. Sorts right after `94-paperproc-gpu-workers` (status, then
how-to). Covers: starting the GPU-pinned backend, the recommended launch
command (GTX1060, the safe/proven option per the benchmark), monitoring,
and the checkpoint-safe stop command — with an explicit callout that the
faster P4000 backend (61 tok/s) hits 82°C in ~7 minutes under this exact
load and should not be left unattended until the fan is installed.

Installed via `sudo cp` + `chmod 755` (the first `cp` preserved a
scratchpad-restrictive mode that `run-parts` couldn't read — fixed to
`755`, matching every other file in `/etc/update-motd.d/`).

### `scripts/paperproc_howto_wall.sh` (copy in this folder)
Condensed one-line version of the same instructions, piped to `wall`.
Best-effort delivery (silently does nothing to ttys with `mesg n` set,
same as the existing `cosmos-dashboard.service` wall notifications).

### Cron entry (user crontab, `ricky`)
```
0 */2 * * * /home/ricky/programs/paper_proc/scripts/paperproc_howto_wall.sh >/dev/null 2>&1
```
Every 2 hours, on the hour. Chosen as a reminder cadence, not a monitoring
cadence — deliberately much less frequent than `gpu_temp_check.sh`'s 5-minute
poll, since a wall broadcast interrupts every open terminal and this is a
static reminder, not new information. Adjust the `*/2` if a different
frequency is wanted.

**Gotcha hit during install:** `crontab <path>` failed with a truncated
error (`...scratc: No such file or directory`) when given the long
`claude_creations` scratchpad path — the `crontab` binary appears to choke
on very long temp-file paths. Worked fine once copied to a short `/tmp/`
path first.

## Verification
- `run-parts --lsbsysinit /etc/update-motd.d/` — the how-to block renders
  correctly, right after the existing GPU-worker status block.
- `paperproc_howto_wall.sh` run manually — exits 0 cleanly.
- `crontab -l` — confirms the new line alongside the two pre-existing
  entries (`duck.sh`, `gpu_temp_check.sh`), nothing removed or altered.

## Update 2026-08-23 (same day): `journalctl -u ollama -f` trap

While the GTX1060 batch (launched this same session, see the throughput
investigation doc) was running, an operator asked what `journalctl -u
ollama -f` would show in a second terminal. Answer: **the wrong daemon.**
`journalctl -u ollama` only covers the shared *systemd-managed*
`ollama.service` (port 11434) — a different process from the isolated
GPU-pinned backends this pipeline actually talks to (`start_gpu{0,1}_backend.sh`,
plain `nohup ollama serve &`, not systemd units). Watching it during a batch
run shows nothing relevant. Added the correct equivalent
(`tail -f /tmp/ollama_gpu1.log` / `_gpu0.log`) plus this caveat to both the
motd entry (step 3, "Monitor") and the wall broadcast, and to
`ascii_tree.md` §4.4.

## Status
✅ motd entry installed and rendering correctly
✅ Wall broadcast script installed, tested, cron'd every 2 hours
✅ `journalctl -u ollama` wrong-daemon trap documented in both, plus the
   correct `/tmp/ollama_gpu*.log` tail commands
✅ Both preserve the throughput-benchmark finding: GTX1060 is the
   recommended default (safe, 48 tok/s); P4000 (61 tok/s) is flagged as
   supervise-only pending the fan install
