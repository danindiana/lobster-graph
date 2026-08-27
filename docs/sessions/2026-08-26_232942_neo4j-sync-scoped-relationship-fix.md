# Neo4j Periodic Sync: Diagnosing and Fixing the 120s Timeout

**Date:** 2026-08-26
**Time:** 23:10 (troubleshoot) → 23:29 (scoped fix)
**Session ID:** 2026-08-26_231029 / 2026-08-26_232942

## Objective

`paper_proc_smrtevict.py`'s periodic Neo4j sync (every 300s, via
`_sync_to_neo4j()` → `neo4j_viz/neo4j_importer.py`) was intermittently
printing:

```
⚠️  Neo4j sync timed out after 120s (stale connection?) — skipped, will retry next cycle
```

Diagnose the actual cause and fix it, including the case where papers
genuinely change (not just the trivial "nothing changed" case).

## Part 1 — Diagnosis: not a stale connection

Reproduced directly: running `neo4j_viz/neo4j_importer.py` by hand against
the live processed dir took **2:53–3:07 wall-clock**, but only **~2.7s of
actual CPU time** — almost the entire run was the client sitting idle,
waiting on the Neo4j server. Ruled out disk I/O, swap, RAID health, and CPU
contention (checked `iostat`, `free -h`, `/proc/mdstat`, `dmesg`, `docker
stats` — all clean once a leftover unrelated background `find /` from this
same session was killed and the timing was reproduced clean).

The real cost: `neo4j_importer.py`'s `main()` ran an **unconditional
full-graph cartesian-product query** every single invocation to rebuild
`MENTIONS`/`REFERS_TO`/`IMPLEMENTS` relationships between Papers, Concepts,
Algorithms, and CodeSnippets — regardless of whether any paper had actually
changed:

```cypher
MATCH (p:Paper), (c:Concept)
WHERE NOT (p)-[:DEFINES]->(c)
  AND (toLower(p.motivation) CONTAINS toLower(c.name)
       OR toLower(p.methodology) CONTAINS toLower(c.name))
MERGE (p)-[:MENTIONS]->(c)
-- + 3 more of the same shape (Concept×Concept, Algorithm×Concept, CodeSnippet×Concept)
```

At the graph's size at the time (1384 Papers, 6287 Concepts, 1803 Algorithms,
3881 CodeSnippets), that's **~84M unindexed string-containment comparisons**
per sync — no property indexes existed anywhere in the database (confirmed
via `SHOW INDEXES` — only default label-scan indexes). Cost was ~170-190s
server-side every cycle, independent of whether anything changed, and only
getting worse as the corpus grows (~O(N²) in Concept count).

### Fix 1 (this session, first pass): skip when nothing changed

Added a `synced` counter, incremented only for papers that actually get
resynced (as opposed to skipped via the existing `paper_hash` match). Gated
the whole relationship-inference block on `synced > 0` — when nothing
changed this cycle (the common case), sync now returns immediately after the
cheap hash-fetch/skip pass. Verified: no-change sync dropped from ~170-190s
to well under a second.

This didn't address the case where papers *do* change — see Part 2.

## Part 2 — Fixing the "papers changed" case

Even one changed paper still triggered the full O(all×all) cartesian scan.
Investigated (via an Explore agent) whether anything depends on the
full-graph behavior:
- **No test coverage** for `main()` or the relationship block (only pure
  parser helpers are tested, in `tests/test_importer_parsers.py`).
- **No "full rebuild" flag/script** anywhere — all 6 call sites
  (`vram_wizard.py`, `vram_resident_processor.py`, `paper_processor_dir.py`,
  `paper_proc_smrtevict.py`, `paper_processor.py`, `neo4j_viz/server.py`)
  invoke the same `main()` path identically, none parse importer stdout.
- **Zero Neo4j indexes/constraints** existed anywhere in the codebase.

**Correctness constraint**: the full scan today catches genuine cross-paper
links — e.g. paper A defines a new Concept X this cycle, and an unrelated,
already-synced paper B (untouched this cycle) already mentions "X" in text
from months ago. A naive "only this cycle's touched papers × only this
cycle's touched concepts" scoping would silently miss that edge, with
nothing in place to catch the regression.

### Fix 2: bidirectional touched-node scoping

For each of the 4 relationship types, replaced the single unconditional
cartesian query with **two** explicit scoped queries — one fixing each side
to the set of nodes touched this cycle, scanning the *other* label in full:

```
(touched Papers × ALL Concepts)  ∪  (ALL Papers × touched Concepts)      → MENTIONS
(touched Concepts × ALL Concepts) ∪ (ALL Concepts × touched Concepts)    → REFERS_TO
(touched Algorithms × ALL Concepts) ∪ (ALL Algorithms × touched Concepts) → IMPLEMENTS
(touched CodeSnippets × ALL Concepts) ∪ (ALL CodeSnippets × touched Concepts) → IMPLEMENTS
```

This is `O(touched × all + all × touched)` instead of `O(all × all)` —
typically tiny when 1-3 papers change per cycle — and is provably equivalent
in coverage to the original full scan: an edge can only newly become true
this cycle if a property on at least one endpoint changed, and
untouched↔untouched pairs were already correctly resolved by a prior cycle.

Also added, since none existed: idempotent property indexes
(`CREATE INDEX ... IF NOT EXISTS FOR (n:Label) ON (n.prop)`) on
`Paper.name`, `Concept.name`, `Theorem.name`, `Algorithm.name`,
`CodeSnippet.title`, created once near the top of `main()`. These let the
new scoped queries' `WHERE x.name IN $touched` clauses use index seeks
instead of label scans, and speed up the ~5-6 existing per-paper
`MATCH (p:Paper {name: $paper_name})` lookups elsewhere in the file for
free.

A new pure helper, `extract_touched_names(defs, algs, cpp_examples)`,
collects the Concept/Algorithm/CodeSnippet identity keys touched by each
synced paper (safe over-approximation: includes re-merged existing nodes,
not just newly-created ones — harmless, just occasionally re-checks a node
that didn't actually change). Covered by new unit tests in
`tests/test_importer_parsers.py::TestExtractTouchedNames`.

### Verification performed

1. `py_compile` + full `pytest tests/test_importer_parsers.py` — 20 passed,
   6 pre-existing xfails, 3 new tests all pass.
2. Indexes confirmed `ONLINE` via `SHOW INDEXES`.
3. **No-op timing**: nothing changed → still hits the `synced == 0` early
   return, ~0.7s.
4. **Single-change timing**: built a synthetic one-paper processed folder in
   the scratchpad, ran the importer against just that folder → **1.26s**
   wall-clock (vs. ~170-190s before), with one real paper synced.
5. **Live reversible bidirectional-correctness test**: temporarily appended
   a unique marker string to one real, already-synced Paper's `motivation`
   property (`03_MAUCHERAT_NEW.pdf`), then synced the synthetic paper (which
   defines a Concept with that same marker name) as the *only* touched node
   this cycle. Confirmed the untouched real paper picked up a new `MENTIONS`
   edge to the brand-new concept — proving the "ALL Papers × touched
   Concepts" half of the scoping fired correctly for a paper not itself
   touched. Cleaned up afterward: deleted the synthetic Concept/Paper/edges,
   restored the real paper's `motivation` and `paper_hash` to their exact
   original values (recovered from the source metadata.json at
   `/mnt/raid0/monolithic_pdf_folderv3/July-30-2026/saved_go_crawlerv2/_processed/03_maucherat_new/metadata.json`),
   removed the scratch directory. Final node counts confirmed back to
   exactly 1384 Papers / 6287 Concepts — no residue.

## Files changed

- `neo4j_viz/neo4j_importer.py` — both fixes (early-exit gate +
  touched-node-scoped relationship queries + index creation +
  `extract_touched_names` helper).
- `tests/test_importer_parsers.py` — `TestExtractTouchedNames`.

## Not fixed / left as-is

- A pre-existing, unrelated minor inefficiency noticed in passing: the
  per-paper `DEFINES` edge (`Paper -[:DEFINES]-> Concept`) is created with
  `CREATE` rather than `MERGE`, so re-syncing an unchanged-but-reprocessed
  paper that still defines the same concept would create duplicate `DEFINES`
  edges. Out of scope for this fix (doesn't affect correctness of the
  scoping above — a duplicate edge still makes `NOT (p)-[:DEFINES]->(c)`
  false either way), left untouched.

## Part 3 (2026-08-27) — the deeper bug: a structural mismatch, not just timing

After the scoped-query fix landed, checked whether papers already-completed
on disk were actually reaching Neo4j. Two separate problems found:

**3a. `aug_8_2026` (flat layout) — 1,458 of 1,820 completed papers were
never synced.** Their on-disk completion timestamps (2026-07-29 →
2026-08-19 00:59) span right up to 35 minutes before `config.json`'s
`papers_dir` switched to `aug_12_2026` (01:34:01). Consistent with the
Part 1/2 bug: early on, syncs succeeded (accounting for the 362 that did
make it in); once the graph grew large enough, the pre-fix unconditional
cartesian scan started reliably exceeding the caller's 120s timeout on
every cycle, silently halting all further syncs from that directory while
processing continued for weeks. **Fixed by backfilling**: ran the (now
scoped, fast) importer directly against `aug_8_2026/_processed` — 1,458
papers synced in 5:04 total wall-clock, confirming the scoped relationship
queries hold up at real production scale (1,458 touched papers, thousands
of touched concepts), not just the earlier 1-paper synthetic test.

**3b. `aug_12_2026` (nested layout) — ALL 2,113+ completed papers were
invisible to the importer, unconditionally, regardless of the timing fix.**
`paper_proc_smrtevict.py` deliberately mirrors the source PDF tree under
`_processed/` (code comment: *"Mirror the input tree under `_processed/` so
subfolder structure is preserved"*) when the source corpus itself is
organized into subfolders — output lands at
`_processed/<source-subfolder>/<paper>/metadata.json`, one level deeper
than a flat corpus. `neo4j_importer.py`'s per-paper walk
(`PROCESSED_DIR.iterdir()`) only ever checked **immediate children** for
`metadata.json` — it never descended into subfolders. Every paper under a
nested corpus was therefore structurally unreachable, forever, independent
of any timeout.

**Fix**: changed the walk from `PROCESSED_DIR.iterdir()` to
`sorted(PROCESSED_DIR.rglob("metadata.json"))`, deriving each paper's folder
as `meta_file.parent` — finds papers at any depth, and is a no-op behavior
change for flat corpora (verified: re-ran against the now-fully-synced
`aug_8_2026` afterward, all 1,820 correctly recognized as unchanged and
skipped, no duplicates, no regression). Also fixed a related bug this
exposed: the Diagram `svg_path` property was built from `path.name` alone
(just the paper's own leaf folder name), which for a nested corpus produces
a truncated path that doesn't match the real on-disk location for static
serving — changed to `path.relative_to(PROCESSED_DIR).as_posix()`, correct
at any depth.

Backfilled `aug_12_2026/_processed` the same way: 2,190 papers synced in
10:22 total. Neo4j: 1,384 → 2,842 (Part 3a) → 5,032 (Part 3b) papers.

**Scope check — how many other corpora were silently orphaned the same
way?** A bounded `find /mnt/raid0 -iname _processed` plus known locations
under `/home/jeb` turned up 14 more `_processed` directories never synced
into this Neo4j instance, ~14,482 additional completed papers total:
`July-30-2026/saved_go_crawlerv2` (801), 5× IETF proceedings/slides tranches
(946+950+1069+1013+1189), `illoinois_edu` (555), `july_HF_Papers` (1368),
`Aug_7_2026/papers_20260717_114100` (189), the repo-local
`fork_2026-05-09T184929Z` (1034), and `~/Documents/AI-ML_Papers` plus its
`transformers`/`EBF`/`nanobots` subfolders (5261+25+6+76). Backfilling all
of these in one sequential pass (avoiding parallel writers against the same
Neo4j instance).

**Known limitation, not fixed**: `Paper` nodes are `MERGE`d globally by
`paper_name` (usually the source filename). Checked for cross-corpus name
collisions before backfilling: 1,131 of the 14,482 paper_names appear in
more than one corpus. The large majority are genuine duplicates — the same
arXiv ID or paper reprocessed across different crawl snapshots (e.g.
`fork_2026-05-09T184929Z` looks like an earlier snapshot of
`AI-ML_Papers` itself) — correctly deduplicating into one node is the
*right* outcome there. A minority use generic numeric filenames
(`01.pdf`, `17.pdf`, ...) inherited from different source sites (e.g. IETF
slide decks vs. `illoinois_edu` papers) that are almost certainly
*different* physical documents colliding on name — for those, whichever
corpus syncs last wins the Paper node's properties. Pre-existing design
limitation (paper identity was never more than the filename), not
introduced by this session's changes, and not fixed here — flagging for
awareness. A real fix would need a more unique Paper identity (e.g. content
hash instead of filename).

## Part 4 (2026-08-27) — crash-hardening, retry, and a final completeness sweep

The 14-corpus backfill in Part 3 silently lost data in 6 of the 14 runs:
each `main()` invocation is a single process with no per-paper error
isolation, so one malformed paper crashed the whole corpus and every
remaining paper (in `rglob` order) was never attempted, with no error
surfaced to `backfill_all.sh` (no `set -e`, so the loop just moved on to
the next directory).

Two real crash classes found in the log (`grep -n "Traceback" `):

1. `neo4j.exceptions.ClientError: ... 'MERGE' cannot be used with a graph
   element property value that is null` — a `Theorem`'s `"name"` was an
   explicit JSON `null` rather than an absent key. `dict.get(key, "")`
   only substitutes the default when the key is *missing*, not when its
   value is `None` — so this reached the Cypher layer as a literal null
   and MERGE (which requires a non-null match-key) rejected it.
2. `AttributeError: 'str' object has no attribute 'get'` — a
   `concepts`/`algorithms`/`examples` JSON array occasionally contained a
   bare string instead of the expected `{name, ...}` object, and `.get()`
   was called on it directly.

**Fix** (`neo4j_viz/neo4j_importer.py`): added `_safe_str(d, key)`
(coerces missing/null/non-string values to `""`) and `_sanitize_items(items)`
(drops non-dict list entries), applied at every per-item Cypher parameter
site. Also wrapped the entire per-paper processing body in `try/except`,
so any *other* unanticipated malformed-data shape skips just that one paper
with a warning instead of aborting the rest of the corpus. Added unit tests
(`TestSanitizeItems`, `TestSafeStr`) reproducing both crash inputs.
Regression-checked against the already-fully-synced `aug_8_2026` (flat
layout) — no behavior change, still fast, no crash.

**Retry**: re-ran the 6 affected corpora (`ietf-106`, `ietf-105`, `ietf-121`,
`ietf-120`, `july_HF_Papers`, `AI-ML_Papers` root) with the fix. All 6
completed with **zero tracebacks** this time (confirmed via `grep -c
Traceback` on the full retry log). The `AI-ML_Papers` run (5,261 papers,
by far the largest single corpus in this backfill) took noticeably longer
on its final relationship-scoping query — one `CodeSnippet × touched-Concepts`
`IMPLEMENTS` query ran ~14s→55s+ before completing. Verified via `SHOW
TRANSACTIONS` that this was a single legitimately long-running query
(status `Running`, elapsed climbing steadily), not a deadlock or hang: when
an entire large corpus is synced in one batch, its touched-node set
approaches the corpus size itself, so the scoped-query optimization's
`O(touched × all)` cost degrades back toward the original `O(all × all)`
for that one batch. This is expected and acceptable for a one-time bulk
backfill — the optimization's actual target is the recurring 300s
incremental cycle, where touched sets are small (1-3 papers).

**Final completeness sweep**: a fresh bounded `find` under `/mnt/raid0` and
all of `/home/jeb` (not just the previously-checked subset) turned up 3
more real corpora that had been missed: `~/Documents/_processed` (3
papers), `~/Documents/computer science/_processed` (1,020 papers),
`~/Documents/computers/_processed` (1,330 papers) — backfilled the same way.
Also found `~/tolaria-aiml-vault/` (a separate git repo with the same
`transformers`/`EBF`/`nanobots` subfolder layout as `AI-ML_Papers`, distinct
inode, not a symlink) but all four of its `_processed` dirs are empty (0
`metadata.json`) — nothing to sync there.

The 3 newly-found corpora backfilled clean (zero tracebacks): `~/Documents/_processed`
(3 papers), `~/Documents/computer science/_processed` (1,020 papers),
`~/Documents/computers/_processed` (1,330 papers).

**Corpora swept this session: 19 total.** `aug_8_2026`, `aug_12_2026`,
`July-30-2026/saved_go_crawlerv2`, 5× IETF proceedings/slides tranches
(106/105/104, 121/120), `illoinois_edu`, `july_HF_Papers`, `Aug_7_2026`,
`fork_2026-05-09T184929Z`, `AI-ML_Papers` + its `transformers`/`EBF`/
`nanobots` subfolders, and the 3 found in the final sweep above. (One more,
`mono_folderv5/Aug_21_2026/_processed`, and all four `tolaria-aiml-vault`
`_processed` dirs were checked and confirmed empty — 0 completed papers,
nothing to sync.)

**True final graph totals** (after Parts 1-4, including the final sweep):
**19,627 Papers**, 48,523 Concepts, 19,573 Algorithms, 46,779 CodeSnippets,
25,050 Theorems, 41,209 MENTIONS edges, 1,886,221 REFERS_TO edges, 1,486,053
IMPLEMENTS edges — up from 1,384 Papers at the start of this session (a
~14.2x increase in synced papers). The large REFERS_TO/IMPLEMENTS edge
counts are expected/pre-existing: the CONTAINS-based name-matching
heuristic is intentionally loose and produces many matches, especially for
short concept names — a data-quality characteristic of the heuristic
itself, unrelated to this session's changes.

## Summary of bugs fixed this session

1. **Unconditional full-graph relationship rescan** (Part 1-2): every sync
   cycle rebuilt all cross-paper relationships from scratch regardless of
   what changed, costing ~170-190s and intermittently exceeding the
   caller's 120s timeout. Fixed with an early-exit when nothing changed,
   plus touched-node-scoped queries (bidirectional per relationship type)
   when something did.
2. **Nested-corpus directory blindness** (Part 3): the importer's walk only
   checked immediate children of `PROCESSED_DIR` for `metadata.json`, so
   any corpus whose source PDFs were organized into subfolders (mirrored
   into `_processed/<subfolder>/<paper>/`) was 100% invisible to sync,
   unconditionally — not a timing issue, a structural one. Fixed by
   switching to a recursive `rglob("metadata.json")` walk.
3. **Malformed-JSON crashes losing whole corpora** (Part 4): a single
   paper with an explicit JSON `null` name or a bare string where an
   object was expected crashed the entire importer process, silently
   abandoning every remaining paper in that corpus. Fixed with defensive
   value coercion (`_safe_str`, `_sanitize_items`) and a per-paper
   try/except safety net.

All three are independent root causes of the same symptom ("papers aren't
showing up in the graph") — fixing only one would have left real gaps.
