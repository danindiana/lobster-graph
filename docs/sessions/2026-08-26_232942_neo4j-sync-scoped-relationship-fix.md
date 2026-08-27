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
