# session_archive

Vector search + knowledge-graph extension over the `claude_creations` engineering
session archive — a companion to the main pipeline's Paper/Concept graph, targeting
this project's own history instead of PDF papers.

Extends the same `paper-processor-neo4j` graph with `Session` nodes (one per dated
`claude_creations` folder) linked to shared `Concept` nodes, and adds a FAISS
vector index so a natural-language question can retrieve the right past session.
See:
![ingestion pipeline](../docs/diagrams/13_session_archive_ingestion.png)
![query path](../docs/diagrams/14_session_archive_query.png)

## Why it exists

`paper_processor` has no vector/embedding component — retrieval is graph
traversal only. `session_archive` reuses `neo4j_importer.py`'s idempotent
`MERGE`-writer pattern and doc-classifier-gpu's proven chunk/embed approach
(`BAAI/bge-m3`, char-based chunking) to add the missing semantic-search layer,
scoped to this repo's own session logs rather than a new corpus.

## `ingest_sessions.py`

Scans `claude_creations/*/` (one level), reads each folder's primary markdown
(`SESSION.md` → `README.md` → first `*.md`), calls a local Ollama model to
extract a short summary + concept list, chunks the full document text, embeds
each chunk with `bge-m3`, and writes:
- `Session`/`Concept` nodes + `MENTIONS` edges into Neo4j (`bolt://localhost:7687`)
- chunk vectors + an id-map into a FAISS `IndexFlatIP` index

```bash
paper_processor/.venv/bin/python3 session_archive/ingest_sessions.py [options]
```

| Flag | Default | Notes |
|---|---|---|
| `--root` | `~/Documents/claude_creations` | one level of dated folders |
| `--index-dir` | `/mnt/nvme_staging/session_archive_index` | FAISS index + id-map + resume state |
| `--limit N` | none | process at most N *new* folders this run |
| `--folders a,b,c` | none | explicit folder names instead of scanning `--root` |
| `--model` | `qwen3:14b` | Ollama model for summary/concept extraction |
| `--embed-model` | `BAAI/bge-m3` | sentence-transformers model |
| `--chunk-chars` | `4000` | char-based chunk size (200-char overlap) |
| `--reprocess` | off | re-ingest folders already marked done |
| `--save-every N` | `5` | checkpoint interval (folders) |

Resumable: already-ingested folders (tracked in
`<index-dir>/ingested_sessions.json`) are skipped on the next run unless
`--reprocess` is passed. A full run over the archive takes roughly 10–15s per
folder (dominated by the Ollama call).

## `query_sessions.py`

Semantic search over the index built above, with optional related-`Concept`
lookup from Neo4j for extra context. Takes either a natural-language query or
`--like SLUG` (mutually exclusive — exactly one is required).

```bash
paper_processor/.venv/bin/python3 session_archive/query_sessions.py "<question>" [--top-k N] [--no-graph] [--rerank]
paper_processor/.venv/bin/python3 session_archive/query_sessions.py --like <session-slug> [--top-k N] [--no-graph] [--rerank]
```

Prints ranked matches (score, source session + file, a text snippet, and —
unless `--no-graph` is passed — related `Concept` names pulled from the graph
for each matched session).

- **`--like SLUG`** — "more like this": instead of embedding new query text,
  mean-pools the given session's own chunk vectors (pulled back out of the
  index via `IndexFlatIP.reconstruct()`, re-normalized) and searches with
  that, excluding the source session's own chunks from results. Good for
  surfacing related sessions beyond what shared `Concept` nodes catch —
  e.g. `--like <rtx3080-gpu-bar-loss slug>` surfaces the earlier ReBAR
  investigation and GPU boot-display-priority sessions even though they don't
  share an explicit `Concept` edge.
- **`--rerank`** — if a relevance probe has been trained (see
  `label_sessions.py` below), widens the candidate pool
  (`top_k × 4`) and re-sorts it by learned relevance score instead of raw
  cosine similarity. Silently falls back to plain ranking with a note printed
  if no probe exists yet.

## `label_sessions.py`

Interactive relevance-labeling loop — ported from a sibling project,
`militia-classifier`'s `militia-rlhf/labeler.py` and `probe.py`, collapsed to
a single modality (`bge-m3`, 1024-d) since session_archive has no text/image
split.

```bash
paper_processor/.venv/bin/python3 session_archive/label_sessions.py
```

Shows one unlabeled chunk at a time (session, path, snippet); single
keypress, no Enter needed:

| Key | Action |
|---|---|
| `y` | label relevant |
| `n` | label not relevant |
| `s` | skip |
| `t` | retrain now |
| `q` | quit (auto-saves) |

Auto-retrains every 20 new labels. Before any probe is trained, candidates
are shown in unlabeled-first order (session_archive has no fixed target
class the way militia-classifier does, so there's no centroid-distance
signal to pre-sort by). Once a probe exists, candidates are re-sorted by
`|probe_prob − 0.5|` — most uncertain first, same as the original.

**Design note:** unlike militia-classifier's probe (trained against one fixed
target — "is this militia-like"), this probe has no single fixed target:
every query against `query_sessions.py` is different. What it actually learns
is a general, corpus-wide relevance/quality signal accumulated across
whatever you've labeled so far — not true per-query relevance. That's an
accepted simplification: `--rerank` boosts chunks that look like the kind of
thing you've previously marked useful, rather than what's specifically
relevant to today's query.

## Storage

Index artifacts live under `/mnt/nvme_staging/session_archive_index/` — not
`/mnt/raid0`, which is 95% full with zero redundancy (RAID0). This directory
only holds generated data; the code lives here in `session_archive/`.

| File | Written by |
|---|---|
| `index.faiss`, `id_map.json`, `ingested_sessions.json` | `ingest_sessions.py` |
| `relevance_labels.json`, `relevance_probe.pt` | `label_sessions.py` |

## Neo4j schema addition

```
(Session {title, path, slug, date, summary})-[:MENTIONS]->(Concept {name, definition})
```

`Concept` is the same node label `neo4j_importer.py` writes for papers, so a
concept mentioned in both a session and a paper resolves to one shared node —
sessions and papers become cross-linked automatically wherever their concepts
overlap. Both dashboards (`neo4j_viz/cosmos_server.py` on :8686,
`neo4j_viz/server.py` on :8585) query generically by label, so `Session` nodes
render on :8686 without any dashboard changes; :8585 is more Paper-coupled and
won't show them usefully.

## Environment

Runs in `paper_processor/.venv` (extended with `sentence-transformers` and
`faiss-cpu`) — no separate venv, since both scripts also need the `neo4j`
driver already installed there.

## Setup from scratch

See [`HOWTO.md`](HOWTO.md) for cold-start bare-metal setup (prereqs, Neo4j,
Ollama, first run, full ingestion).

## Diagrams

`diagrams/` holds the module-specific set (flat, descriptive names — see
`docs/diagrams/explainer/` at the repo root for the same convention):

| Diagram | Covers |
|---|---|
| [`overview`](diagrams/overview.png) | 30,000-ft mental model of the whole module |
| [`system_architecture`](diagrams/system_architecture.png) | The 4 scripts and what each reads/writes |
| [`start_up_howto`](diagrams/start_up_howto.png) | Flowchart companion to `HOWTO.md` |
| [`portability`](diagrams/portability.png) | What's worlock-specific vs. portable, and how to change it |
| [`catch22s`](diagrams/catch22s.png) | Real ordering/environment/repo gotchas hit while building this |
| [`future_directions`](diagrams/future_directions.png) | Shipped vs. proposed roadmap |
| [`network_topology`](diagrams/network_topology.png) | What talks to what — all localhost/outbound, zero WAN exposure |
| [`lib_depends`](diagrams/lib_depends.png) | Library dependency tree |

`docs/diagrams/13_session_archive_ingestion.png` and `14_session_archive_query.png`
(repo root, numbered sequence) cover the detailed data-flow — this module's
`diagrams/` folder is the higher-level/reference set.
