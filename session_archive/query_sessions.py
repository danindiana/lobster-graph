#!/usr/bin/env python3
"""
Semantic search over the claude_creations session archive FAISS index built by
ingest_sessions.py, with optional related-Concept lookup from the shared
paper-processor-neo4j graph.
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import faiss
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")

DEFAULT_INDEX_DIR = Path("/mnt/nvme_staging/session_archive_index")
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"


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


def main():
    ap = argparse.ArgumentParser(description="Semantic search over the ingested session archive")
    ap.add_argument("query")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--no-graph", action="store_true", help="Skip Neo4j related-concept lookup")
    args = ap.parse_args()

    index_dir = Path(args.index_dir)
    index_path = index_dir / "index.faiss"
    id_map_path = index_dir / "id_map.json"
    if not index_path.exists() or not id_map_path.exists():
        raise SystemExit(f"No index found at {index_dir} — run ingest_sessions.py first.")

    index = faiss.read_index(str(index_path))
    id_map = json.loads(id_map_path.read_text())

    from sentence_transformers import SentenceTransformer
    device = pick_device()
    encoder = SentenceTransformer(args.embed_model, device=device, trust_remote_code=True)

    qvec = encoder.encode([args.query], convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
    scores, indices = index.search(qvec, args.top_k)

    driver = None
    if not args.no_graph:
        try:
            driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            driver.verify_connectivity()
        except Exception as exc:
            print(f"(Neo4j unavailable, skipping graph context: {exc})")
            driver = None

    print(f"\nTop {args.top_k} matches for: {args.query!r}\n")
    seen_sessions = set()
    for rank, (score, i) in enumerate(zip(scores[0], indices[0]), 1):
        if i < 0 or i >= len(id_map):
            continue
        entry = id_map[i]
        print(f"{rank}. [{score:.3f}] {entry['session']}  (chunk {entry['chunk_idx']})")
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
