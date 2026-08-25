# Architecture and Analysis of `ingest_sessions.py`

## Overview
`ingest_sessions.py` is an ingestion pipeline script designed to process engineering session logs (Markdown files) from a local directory (default `~/Documents/claude_creations`) and index them for semantic search and knowledge graph querying. 

It accomplishes two main tasks:
1. **Knowledge Graph Extraction**: It uses a local Large Language Model (via Ollama) to summarize sessions and extract concrete technical concepts, which are then persisted as nodes and relationships in a Neo4j database.
2. **Semantic Search Indexing**: It chunks the entire session text, computes dense embeddings for each chunk using a local embedding model, and saves them into a FAISS vector index.

## Architecture & Workflow

### 1. File Discovery
The script scans a root directory for session folders. For each folder, it attempts to find the primary markdown document by looking for `SESSION.md`, `README.md`, or picking the first `.md` file it finds.

### 2. LLM Summarization and Concept Extraction (Ollama)
It reads the document and passes the first 16,000 characters to a local LLM (by default, `qwen3:14b`) using the `call_ollama` function. 
The LLM is prompted to return a specific Markdown shape containing:
- A brief plain-English **Summary** of the session.
- A list of **Concepts** (tools, topics, hosts) discussed in the session, along with brief definitions.

### 3. Text Chunking and Embedding (Sentence Transformers)
While the LLM only sees the first 16K characters, the *entire* document is chunked for semantic search.
- **Chunking**: The document is split into overlapping chunks (default 4,000 characters, 200 character overlap).
- **Embedding**: It uses the `BAAI/bge-m3` model via the `sentence_transformers` library to convert each text chunk into a high-dimensional vector. The script dynamically selects a CUDA GPU with at least 2GB of free VRAM, falling back to CPU if none is available.

### 4. Storage and Persistence
The parsed data is stored across two different database systems:

#### Neo4j (Knowledge Graph)
The script connects to Neo4j and uses the `MERGE` pattern to idempotently insert:
- A **`Session` node** containing the file path, session title, date, and LLM-generated summary.
- Multiple **`Concept` nodes** for the topics extracted by the LLM.
- **`MENTIONS` relationships** linking the `Session` to the extracted `Concepts`.

#### FAISS (Vector Store)
The generated chunk embeddings are inserted into a FAISS index (`IndexFlatIP`). The `SessionIndex` class manages the persistence of this index alongside metadata:
- `index.faiss`: The raw vector index.
- `id_map.json`: Maps each vector in FAISS back to its original chunk, file path, and session slug.
- `ingested_sessions.json`: A manifest tracking which sessions have already been processed to prevent redundant ingestion on subsequent runs.

## CLI & Execution
The script uses `argparse` allowing the user to configure:
- The target directories and index paths.
- Limits on how many folders to process in a run, or explicitly specifying folders.
- Model overrides for both Ollama and the embedding model.
- An optional `--reprocess` flag to forcefully re-ingest previously processed sessions. 

The state of the FAISS index is checkpointed to disk periodically (default every 5 folders) to avoid data loss during a long ingestion run.
