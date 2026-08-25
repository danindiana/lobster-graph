# Ingest Sessions: The Byzantine Hyper-Architecture

Welcome to the comprehensive, unfathomably detailed guide to the `ingest_sessions.py` macro-system. This document delineates the labyrinthine execution pathways, non-linear state transitions, and multidimensional data transformations involved in translating raw, unformatted Markdown strings into a highly structured, dual-persisted semantic topology spanning both Neo4j Knowledge Graphs and FAISS High-Dimensional Vector Indices.

## I. Epistemological Foundation & Teleology

The primary objective of this module is to achieve *knowledge extraction and semantic crystallization*. The raw textual exhaust of engineering sessions is highly unstructured. To interact with it efficiently at scale requires a metamorphosis of unstructured ASCII/UTF-8 bytes into:
1. **Topological Relationships**: Nodes and edges in a property graph (Neo4j).
2. **Topological Coordinates**: Dense floating-point vectors in a high-dimensional continuous space (FAISS).

The system architecture achieves this via a bifurcated pipeline:
- A **Macroscopic Semantic Extractor** utilizing a quantized Large Language Model (`qwen3:14b` running via the Ollama inferencing engine).
- A **Microscopic Semantic Embedder** utilizing a local embedding transformer (`BAAI/bge-m3` running on hardware-accelerated PyTorch tensors).

## II. The Execution Labyrinth (Process Flow)

### Phase 1: Initialization & Environment Bootstrapping
Upon invocation, the script parses `argparse` flags to determine spatial bounds (the `root` directory, `index-dir`) and temporal limits (`--limit`, `--folders`). It establishes an asynchronous-style synchronous loop against the Neo4j Bolt protocol to verify connectivity. 

Simultaneously, the script probes the underlying silicon infrastructure via `torch.cuda.mem_get_info()`, searching the PCI-E bus for a Compute Unified Device Architecture (CUDA) enabled GPU boasting a minimum of 2000 Megabytes of unallocated VRAM. If found, the PyTorch tensors are aggressively pinned to device memory; otherwise, the script gracefully (but tragically) falls back to the CPU execution provider.

### Phase 2: Directory Traversal and Payload Identification
The script embarks on a sequential traversal of the target `claude_creations` ecosystem. It identifies potential ingestion candidates by applying `SLUG_DATE_RE` and `SLUG_TITLE_RE` Regular Expressions to the directory nomenclature. 

A specialized heuristic `find_primary_doc` is deployed to hunt for `SESSION.md` or `README.md`. If these elusive targets are not found, a desperate glob (`*.md`) is executed to seize any available markdown artifact.

### Phase 3: The Bifurcated Data Transformation Engine

#### Path A: Macro-Summarization (The LLM Oracle)
The first 16,000 characters of the payload are excised and injected into a highly opinionated zero-shot prompt (`SUMMARY_PROMPT`). This prompt orchestrates the Ollama endpoint to return a strictly structured Markdown response. The output is then parsed using custom logic `clean_markdown_headers` and `parse_logic_definitions` to isolate the `Summary` scalar string and a heterogeneous array of `Concept` dictionaries.

#### Path B: Micro-Embedding (The FAISS Vectorizer)
In parallel (conceptually), the *entirety* of the document is subjected to the `chunk_text` function, slicing the string into contiguous 4000-character segments with a 200-character overlap (a technique deployed to mitigate contextual shearing at chunk boundaries). These chunks are processed by `bge-m3` to produce L2-normalized dense embeddings ($R^{1024}$).

### Phase 4: Dual-Persistence Synchronization
The data is finally entombed in two completely disparate database paradigms:
1. **The Graph**: Cypher `MERGE` statements execute an Upsert (Update or Insert) semantic pattern. A `Session` node is materialized, followed by $N$ `Concept` nodes, inextricably linked by `:MENTIONS` directional edges.
2. **The Vector Store**: The dense embeddings are appended to the FAISS `IndexFlatIP` (Inner Product, maximizing cosine similarity). A parallel JSON sidecar `id_map.json` meticulously records the provenance of every single $R^{1024}$ vector, linking chunk indices back to the originating filesystem coordinate and session slug.

## III. Error Mitigation and Resilience
The script exhibits extraordinary resilience, aggressively catching arbitrary `Exceptions` during the Ollama inference phase, preventing isolated inference timeouts or CUDA Out-Of-Memory exceptions from cascading into a global catastrophic failure of the ingestion loop. Checkpoints to the FAISS index are serialized to NVMe storage strictly modulo the `--save-every` parameter.

## IV. Conclusion
This architecture stands as a monolith of data ingestion complexity, weaving together filesystem heuristics, LLM prompt engineering, tensor mathematics, and graph topology into a singular, cohesive extraction engine.
