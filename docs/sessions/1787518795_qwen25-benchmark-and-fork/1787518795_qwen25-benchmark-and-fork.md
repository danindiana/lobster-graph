# qwen2.5 Family Benchmark, and a Pivot to Forking

**Date:** 2026-08-23
**Unix timestamp:** 1787518795
**Session folder:** `docs/sessions/1787518795_qwen25-benchmark-and-fork/`

## Objective

The GTX1060 batch (48 tok/s, `gemma4:e2b-it-qat`) was running as the safe
default, but the operator wanted something faster still and specifically
asked about smaller models on the P4000. Continued the small-model search
started in earlier sessions (`1787167674_pin-model-gtx1060`,
`1787453228_pin-model-quadro-p4000`) and in
`~/Documents/claude_creations/session_1787507023_paper-proc-investigation/`.

## What was tested

Pulled and raw-benchmarked (`/api/generate`, P4000, 100% VRAM-resident,
native ctx 32768) three `qwen2.5` instruct models:

| Model | tok/s | Diagrams |
|---|---:|---|
| `qwen2.5:0.5b` | 145.8 | inconclusive in `paper_proc_smrtevict.py` (240s timeout, no log output); confirmed **failing** in the fork's own harness across 3 full runs |
| `qwen2.5:1.5b` | 82.7 | ❌ placeholder-echo — `digraph G { // ... full valid DOT source ... }` |
| `qwen2.5:3b` | 54.6 | not tested |

`qwen2.5:1.5b`'s failure is a fourth distinct failure mode found across
this whole investigation (instruction leakage, degenerate repetition,
non-terminating generation, now placeholder-echo), across six models from
four families. `gemma4:e2b-it-qat` is still the only one that has never
failed this check.

## Decision

Stopped searching for another candidate model. The pattern itself — every
fast small model is unreliable in a different way — is the finding, and it
points at an architecture change, not another model swap: a model doesn't
need to be individually reliable if something downstream always verifies
its work before it becomes final output.

## → Forked: `~/programs/speculative-paper-proc`

A new, separate repo (own git history, MIT-licensed, not a subtree of this
one): a two-GPU speculative pipeline. P4000 drafts every paper's sections
and diagrams fast and unverified; a bounded look-ahead queue lets it run
ahead of the verify stage; GTX1060 (`gemma4:e2b-it-qat`) checks every
section and diagram against the same failure-mode taxonomy documented here
before anything reaches disk, regenerating only what fails rather than
redoing everything.

Built, and smoke-tested end-to-end on this exact hardware (3 full runs
against a real paper, using the P4000 backend for both roles since the
GTX1060 was occupied by this repo's own production batch at the time — not
the intended topology, but sufficient to validate the code paths). That
testing caught two real bugs before the fork's initial commit:
- `verify_worker` wasn't re-checking regenerated output before writing it —
  only the original draft was verified.
- `num_predict=2000` was truncating multi-diagram responses.

Both fixed; full write-up in that repo's own `docs/FINDINGS.md` and commit
history (`4a1dc69`). Across all 3 test runs, prose sections were accepted
from the draft 4/4 times every run, and zero broken diagram files ever
reached disk — every diagram failure correctly fell back to a valid,
honestly-labeled placeholder. Diagram generation quality itself still needs
prompt tuning (tracked as an open item there, not blocking).

## Status of this repo (paper_proc) after this session

- `paper_proc_smrtevict.py`: unchanged by this session (the Neo4j sync
  timeout fix from the prior session, commit `3b68280`, stands).
- GTX1060 production batch (PID 29032 at last check, port 11435,
  `gemma4:e2b-it-qat`, ~48 tok/s): untouched throughout — all of this
  session's testing used the P4000 backend (port 11436) exclusively to
  avoid any interference.
- See `~/Documents/claude_creations/session_1787507023_paper-proc-investigation/`
  (`paper_proc_smrtevict.md`, `ascii_tree.md`) for the fuller narrative and
  the full model comparison table, both updated this same session.

## Status
✅ Four distinct small-model failure modes now documented across six models
✅ Decision made and acted on: fork rather than keep searching this repo's
   model space
✅ New repo built, smoke-tested (2 real bugs found and fixed), committed
✅ Production GTX1060 batch in this repo confirmed unaffected throughout
