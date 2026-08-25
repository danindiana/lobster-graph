#!/usr/bin/env python3
"""
Semantic search over the claude_creations session archive FAISS index built by
ingest_sessions.py, with optional related-Concept lookup from the shared
paper-processor-neo4j graph.

Two extra retrieval modes (see docs/diagrams and the RAG consolidation plan's
"Phase 2" section for the militia-classifier-inspired design):
  --like SLUG   "more like this" — query by an existing session's own chunk
                vectors instead of new text.
  --rerank      if a relevance_probe.pt has been trained (via
                label_sessions.py), re-rank the candidate pool by learned
                relevance/quality score instead of raw cosine similarity.
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import faiss
from neo4j import GraphDatabase

from relevance_probe import MLPProbe

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")

DEFAULT_INDEX_DIR = Path("/mnt/nvme_staging/session_archive_index")
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
RERANK_POOL_MULTIPLIER = 4


def pick_device(min_free_mb: int = 2000) -> str:
    import torch
    for i in range(torch.cuda.device_count()):
        try:
            free, _ = torch.cuda.mem_get_info(i)
            if free // (1024 * 1024) >= min_free_mb:
                return f"cuda:{i}"
        except Exception:
            pass
    return "cpu"


def related_concepts(driver, session_path: str, limit: int = 5) -> list[str]:
    with driver.session() as session:
        result = session.run("""
            MATCH (s:Session {path: $path})-[:MENTIONS]->(c:Concept)
            RETURN c.name AS name LIMIT $limit
        """, {"path": session_path, "limit": limit})
        return [r["name"] for r in result]


def like_vector(index: faiss.Index, id_map: list[dict], slug: str) -> np.ndarray:
    """Mean-pool an existing session's own chunk vectors into a query vector."""
    own_rows = [i for i, entry in enumerate(id_map) if entry["session"] == slug]
    if not own_rows:
        raise SystemExit(f"No ingested chunks found for session {slug!r} — check the slug.")
    vecs = np.vstack([index.reconstruct(i) for i in own_rows])
    qvec = vecs.mean(axis=0, keepdims=True)
    qvec /= np.linalg.norm(qvec, axis=1, keepdims=True)
    return qvec.astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description="Semantic search over the ingested session archive")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("query", nargs="?", help="Natural-language query")
    group.add_argument("--like", metavar="SLUG", help="Find sessions similar to this session's own content")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--no-graph", action="store_true", help="Skip Neo4j related-concept lookup")
    ap.add_argument("--rerank", action="store_true", help="Re-rank results with the trained relevance probe, if any")
    args = ap.parse_args()

    index_dir = Path(args.index_dir)
    index_path = index_dir / "index.faiss"
    id_map_path = index_dir / "id_map.json"
    probe_path = index_dir / "relevance_probe.pt"
    if not index_path.exists() or not id_map_path.exists():
        raise SystemExit(f"No index found at {index_dir} — run ingest_sessions.py first.")

    index = faiss.read_index(str(index_path))
    id_map = json.loads(id_map_path.read_text())

    probe = None
    if args.rerank:
        if probe_path.exists():
            probe = MLPProbe.load(probe_path)
        else:
            print(f"(--rerank requested but no probe at {probe_path} yet — run label_sessions.py first; falling back to plain ranking)")

    if args.like:
        qvec = like_vector(index, id_map, args.like)
        label = f"sessions like {args.like!r}"
    else:
        from sentence_transformers import SentenceTransformer
        device = pick_device()
        encoder = SentenceTransformer(args.embed_model, device=device, trust_remote_code=True)
        qvec = encoder.encode([args.query], convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
        label = repr(args.query)

    fetch_k = args.top_k
    if probe is not None:
        fetch_k = max(fetch_k, args.top_k * RERANK_POOL_MULTIPLIER)
    if args.like:
        own_count = sum(1 for e in id_map if e["session"] == args.like)
        fetch_k = max(fetch_k, args.top_k + own_count + 20)
    fetch_k = min(fetch_k, index.ntotal)

    scores, indices = index.search(qvec, fetch_k)
    candidates = [(float(s), int(i)) for s, i in zip(scores[0], indices[0]) if 0 <= i < len(id_map)]

    if args.like:
        candidates = [(s, i) for s, i in candidates if id_map[i]["session"] != args.like]

    if probe is not None and candidates:
        rows = [i for _, i in candidates]
        vecs = np.vstack([index.reconstruct(i) for i in rows])
        probs = probe.predict_proba(vecs)
        candidates = sorted(zip(probs.tolist(), rows), key=lambda p: p[0], reverse=True)
        candidates = [(p, i) for p, i in candidates]  # probe score replaces cosine score for display

    candidates = candidates[: args.top_k]

    driver = None
    if not args.no_graph:
        try:
            driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            driver.verify_connectivity()
        except Exception as exc:
            print(f"(Neo4j unavailable, skipping graph context: {exc})")
            driver = None

    score_label = "relevance" if probe is not None else "score"
    print(f"\nTop {len(candidates)} matches for: {label}\n")
    seen_sessions = set()
    for rank, (score, i) in enumerate(candidates, 1):
        entry = id_map[i]
        print(f"{rank}. [{score_label} {score:.3f}] {entry['session']}  (chunk {entry['chunk_idx']})")
        print(f"   {entry['path']}")
        print(f"   …{entry['snippet']}…")
        if driver and entry["session"] not in seen_sessions:
            concepts = related_concepts(driver, entry["path"])
            if concepts:
                print(f"   concepts: {', '.join(concepts)}")
            seen_sessions.add(entry["session"])
        print()

    if driver:
        driver.close()


if __name__ == "__main__":
    main()
