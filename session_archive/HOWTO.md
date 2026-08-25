# HOWTO: session_archive cold-start setup

Setting up `session_archive` on a fresh, bare-metal box from nothing. See
[`diagrams/start_up_howto.png`](diagrams/start_up_howto.png) for the visual
flowchart version of this page.

## 1. Prerequisites

- **Python 3.10+** (developed against 3.13.7)
- **Docker + Docker Compose** — runs the shared `paper-processor-neo4j` graph database
- **Ollama** — installed and able to run locally (`ollama serve`)
- **GPU optional** — a CUDA GPU speeds up embedding and Ollama inference, but
  everything falls back to CPU automatically (`_pick_device()` in each
  script probes for free VRAM; bge-m3 and FAISS's `IndexFlatIP` are both
  CPU-capable)

## 2. Clone and set up the Python environment

```bash
git clone https://github.com/danindiana/lobster-graph.git paper_processor
cd paper_processor

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install sentence-transformers faiss-cpu
```

`session_archive/` has no separate venv — it shares `paper_processor/.venv`
so both talk to the same Neo4j driver without a cross-process IPC layer.

## 3. Start Neo4j

```bash
cd neo4j_viz
docker compose up -d
cd ..
```

This starts `paper-processor-neo4j` bound to `127.0.0.1:7474` (browser) and
`127.0.0.1:7687` (Bolt) — localhost-only, no WAN exposure. Default auth is
`neo4j`/`password123` (see `NEO4J_AUTH` in `neo4j_viz/docker-compose.yml`) —
override via the `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` env vars if you
change it.

## 4. Start Ollama and pull a model

```bash
ollama pull qwen3:14b   # or override with --model on ingest_sessions.py
ollama serve            # if not already running as a systemd service
```

`qwen3:14b` is `ingest_sessions.py`'s default for summary/concept extraction
— any locally-pulled instruction-following model works via `--model`.

## 5. Verify both services are reachable

```bash
curl -s http://localhost:11434/api/tags | head -c 200   # Ollama
curl -s http://localhost:7474 | head -c 200              # Neo4j browser
```

Both should return something (JSON / HTML) rather than a connection error.
If either fails, fix that before continuing — `ingest_sessions.py` exits
immediately on a Neo4j connection failure, and retries once on an Ollama
timeout before giving up.

## 6. First run: smoke test on a handful of folders

```bash
cd session_archive
../.venv/bin/python3 ingest_sessions.py --limit 5
```

The **first** run downloads `BAAI/bge-m3` from Hugging Face Hub (~2GB,
cached to `~/.cache/huggingface/` afterward) — this can look like a silent
hang the first time; it isn't.

By default this reads from `~/Documents/claude_creations` and writes the
FAISS index to `/mnt/nvme_staging/session_archive_index/` — point
`--root`/`--index-dir` elsewhere for a different corpus or a machine without
that mount. The index directory needs to be writable by your user; create it
with normal ownership if it doesn't already exist (on worlock,
`/mnt/nvme_staging` itself happens to be root-owned, so a one-time
`sudo mkdir -p <index-dir> && sudo chown $USER <index-dir>` was needed there).

## 7. Verify retrieval works

```bash
../.venv/bin/python3 query_sessions.py "<a question about one of those 5 folders>"
```

You should get back the folder you expect as the top hit, with a similarity
score and a text snippet.

## 8. Ingest the full corpus

```bash
../.venv/bin/python3 ingest_sessions.py
```

No `--limit` means "everything not already ingested." This is resumable and
checkpointed (every 5 folders by default, `--save-every`) — safe to `Ctrl-C`
and re-run later; already-ingested folders are skipped automatically.

## 9. Optional: teach it what's relevant

```bash
../.venv/bin/python3 label_sessions.py
```

Interactive, single-keypress (`y`/`n`/`s`/`t`/`q`) relevance labeling —
see `README.md` for the full design. Needs a real terminal (uses raw-mode
`termios`, won't work piped/non-interactively). Once you've labeled ~20+
chunks, `query_sessions.py ... --rerank` will use the trained probe.

## You're done

- `query_sessions.py "<question>"` — semantic search
- `query_sessions.py --like <slug>` — "more like this" session
- `query_sessions.py "<question>" --rerank` — relevance-boosted (once labeled)
