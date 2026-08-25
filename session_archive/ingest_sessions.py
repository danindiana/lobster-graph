#!/usr/bin/env python3
"""
Ingest ~/Documents/claude_creations session folders into:
  - the existing paper-processor-neo4j graph (new Session nodes, shared Concept nodes)
  - a FAISS vector index for semantic search over session text (query_sessions.py)

Reuses paper_processor's Neo4j MERGE-writer pattern and Ollama-calling convention,
and doc-classifier-gpu's chunk/embed approach (BAAI/bge-m3, char-based chunking).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import faiss
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "neo4j_viz"))
from neo4j_importer import clean_markdown_headers, parse_logic_definitions  # noqa: E402

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

DEFAULT_ROOT = Path.home() / "Documents" / "claude_creations"
DEFAULT_INDEX_DIR = Path("/mnt/nvme_staging/session_archive_index")
DEFAULT_MODEL = "qwen3:14b"
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
DEFAULT_CHUNK_CHARS = 4000
DEFAULT_OVERLAP = 200
LLM_PROMPT_CHAR_CAP = 16000  # cap what goes to the LLM; chunking/embedding still covers the full doc

SLUG_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_T]")
# Strips date + HHMMSS + optional unix-timestamp prefix segments for a readable title;
# folders that don't match this shape (e.g. GPU_COMMUNICATION_OPTIMIZATION_20260612_165929)
# just keep their full slug as the title.
SLUG_TITLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[_T]\d{6}_(?:\d{10}_)?")

SUMMARY_PROMPT = """You are indexing a personal engineering session log for later semantic search. Read the document below and respond with EXACTLY this markdown shape, nothing else:

## Summary
<2-4 sentence plain-English summary of what this session was about and what happened>

## Concepts
- **<ConceptName>**: <one-line description>
- **<ConceptName>**: <one-line description>
(list 3-8 concrete technical concepts, tools, hosts, or topics this session covers — short noun phrases as names, e.g. "RAID0", "GPU BAR allocation", "Neo4j", "worlock")

Document:
---
{text}
---
"""


def find_primary_doc(folder: Path) -> Path | None:
    for name in ("SESSION.md", "README.md"):
        p = folder / name
        if p.exists():
            return p
    md_files = sorted(folder.glob("*.md"))
    return md_files[0] if md_files else None


def chunk_text(text: str, chunk_chars: int, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Split text into overlapping chunks (mirrors doc-classifier-gpu's chunk_text)."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + chunk_chars])
        start += chunk_chars - overlap
    return chunks


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


def get_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer
    device = pick_device()
    print(f"  Loading embedding model '{model_name}' on {device} ...")
    return SentenceTransformer(model_name, device=device, trust_remote_code=True)


def call_ollama(prompt: str, model: str, timeout: int = 300) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 8192},
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=timeout, stream=True)
    r.raise_for_status()
    out = []
    for line in r.iter_lines():
        if not line:
            continue
        data = json.loads(line)
        if "response" in data:
            out.append(data["response"])
        if data.get("done"):
            break
    return "".join(out).strip()


class SessionIndex:
    """FAISS index + chunk id-map + ingested-folder tracking, persisted to index_dir."""

    def __init__(self, index_dir: Path, dim: int):
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = index_dir / "index.faiss"
        self.id_map_path = index_dir / "id_map.json"
        self.ingested_path = index_dir / "ingested_sessions.json"

        self.index = (
            faiss.read_index(str(self.index_path)) if self.index_path.exists() else faiss.IndexFlatIP(dim)
        )
        self.id_map = json.loads(self.id_map_path.read_text()) if self.id_map_path.exists() else []
        self.ingested = json.loads(self.ingested_path.read_text()) if self.ingested_path.exists() else {}

    def is_ingested(self, slug: str) -> bool:
        return slug in self.ingested

    def add(self, slug: str, source_path: str, chunks: list[str], vectors: np.ndarray):
        self.index.add(vectors.astype(np.float32))
        for i, chunk in enumerate(chunks):
            self.id_map.append({
                "session": slug,
                "path": source_path,
                "chunk_idx": i,
                "snippet": chunk[:300].replace("\n", " "),
            })
        self.ingested[slug] = {"processed_at": datetime.now().isoformat(), "chunks": len(chunks)}

    def save(self):
        faiss.write_index(self.index, str(self.index_path))
        self.id_map_path.write_text(json.dumps(self.id_map))
        self.ingested_path.write_text(json.dumps(self.ingested, indent=2))


def write_to_neo4j(driver, slug: str, title: str, path: str, date: str, summary: str, concepts: list[dict]):
    with driver.session() as session:
        session.run("""
            MERGE (s:Session {path: $path})
            SET s += {title: $title, slug: $slug, date: $date, summary: $summary}
        """, {"path": path, "title": title, "slug": slug, "date": date, "summary": summary})
        for c in concepts:
            name = c.get("name", "").strip()
            if not name:
                continue
            session.run("""
                MATCH (s:Session {path: $path})
                MERGE (c:Concept {name: $name})
                ON CREATE SET c.definition = $definition
                MERGE (s)-[:MENTIONS]->(c)
            """, {"path": path, "name": name, "definition": c.get("definition", "")})


def main():
    ap = argparse.ArgumentParser(description="Ingest claude_creations session folders into Neo4j + FAISS")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    ap.add_argument("--limit", type=int, default=None, help="Process at most N new folders this run")
    ap.add_argument("--folders", default=None, help="Comma-separated list of specific folder names to (re)process")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model for summary/concept extraction")
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    ap.add_argument("--reprocess", action="store_true", help="Re-ingest folders even if already marked done")
    ap.add_argument("--save-every", type=int, default=5, help="Persist index to disk every N folders")
    args = ap.parse_args()

    root = Path(args.root)
    index_dir = Path(args.index_dir)

    print(f"Connecting to Neo4j at {NEO4J_URI} ...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Connected.")

    if args.folders:
        folder_names = [f.strip() for f in args.folders.split(",") if f.strip()]
        folders = [root / name for name in folder_names]
        missing = [f for f in folders if not f.is_dir()]
        if missing:
            sys.exit(f"Folder(s) not found: {missing}")
    else:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

    encoder = get_encoder(args.embed_model)
    idx = SessionIndex(index_dir, dim=encoder.get_embedding_dimension())

    processed = 0
    for folder in folders:
        slug = folder.name
        if idx.is_ingested(slug) and not args.reprocess:
            continue
        if args.limit is not None and processed >= args.limit:
            break

        doc = find_primary_doc(folder)
        if doc is None:
            print(f"  [skip] {slug}: no markdown found")
            continue

        text = doc.read_text(errors="ignore").strip()
        if not text:
            print(f"  [skip] {slug}: empty")
            continue

        print(f"[{processed + 1}] {slug} ({doc.name}, {len(text):,} chars)")

        m = SLUG_DATE_RE.match(slug)
        date = m.group(1) if m else ""
        title = SLUG_TITLE_RE.sub("", slug).replace("_", " ")

        try:
            llm_out = call_ollama(SUMMARY_PROMPT.format(text=text[:LLM_PROMPT_CHAR_CAP]), args.model)
        except Exception as exc:
            print(f"  ! Ollama call failed for {slug}: {exc}")
            continue

        sections = clean_markdown_headers(llm_out)
        summary = sections.get("Summary", "").strip()
        concepts = parse_logic_definitions(sections.get("Concepts", ""))

        chunks = chunk_text(text, args.chunk_chars)
        if not chunks:
            print(f"  [skip] {slug}: no chunks")
            continue
        vectors = encoder.encode(
            chunks, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
        )

        write_to_neo4j(driver, slug, title, str(doc), date, summary, concepts)
        idx.add(slug, str(doc), chunks, vectors)

        print(f"  -> {len(concepts)} concepts, {len(chunks)} chunks")
        processed += 1

        if processed % args.save_every == 0:
            idx.save()
            print("  (checkpoint saved)")

    idx.save()
    driver.close()
    print(
        f"\nDone. Processed {processed} new folder(s). "
        f"Index has {idx.index.ntotal} vectors across {len(idx.ingested)} sessions."
    )


if __name__ == "__main__":
    main()
