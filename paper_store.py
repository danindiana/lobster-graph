#!/usr/bin/env python3
"""
paper_store.py — shared SQLite-backed storage for the paper_processor pipeline.

Replaces the old per-paper `_processed/<slug>/{metadata.json,*.md,diagrams/}`
file tree with rows in one consolidated database, keyed by `paper_hash`
(content hash of the source PDF). Used by all four processor scripts
(paper_processor.py, paper_processor_dir.py, paper_proc_smrtevict.py,
vram_resident_processor.py) plus neo4j_viz/neo4j_importer.py and
neo4j_viz/server.py.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

DEFAULT_DB_PATH = Path("/mnt/nvme_staging/paper_processor_data/papers.db")
DB_PATH_ENV_VAR = "PAPER_PROCESSOR_DB"

# Cross-process claim timeout — a claim older than this is assumed to be from
# a crashed worker, not a paper still legitimately processing, and is
# reclaimed. Matches the pre-SQLite STALE_LOCK_SECONDS constant.
STALE_LOCK_SECONDS = 4 * 3600

ALL_SECTIONS = {"summary", "logic", "cpp", "diagrams", "extras"}
_SECTION_COLUMNS = {
    "summary": "summary_md",
    "logic": "symbolic_logic_md",
    "cpp": "cpp_examples_md",
    "extras": "extras_md",
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    paper_hash          TEXT PRIMARY KEY,
    paper_name          TEXT NOT NULL,
    pdf_path            TEXT NOT NULL,
    page_count          INTEGER,
    chunk_strategy      TEXT,
    model_used          TEXT,
    code_model          TEXT,
    processed_at        TEXT,
    sections_completed  TEXT NOT NULL DEFAULT '[]',
    summary_md          TEXT,
    symbolic_logic_md   TEXT,
    cpp_examples_md     TEXT,
    extras_md           TEXT,
    diagrams_raw_output TEXT,
    source_corpus       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagrams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_hash  TEXT NOT NULL REFERENCES papers(paper_hash) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    dot_src     TEXT NOT NULL,
    svg_content TEXT,
    UNIQUE(paper_hash, idx)
);

-- No FK to papers(paper_hash): OCR caching happens during page extraction,
-- before page_count is known and thus before any papers row can exist yet
-- (upsert_paper_meta needs page_count as an argument).
CREATE TABLE IF NOT EXISTS ocr_cache (
    paper_hash  TEXT NOT NULL,
    page_idx    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (paper_hash, page_idx)
);

CREATE TABLE IF NOT EXISTS processing_locks (
    pdf_path    TEXT PRIMARY KEY,
    claimed_at  REAL NOT NULL,
    claimed_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_pdf_path ON papers(pdf_path);
CREATE INDEX IF NOT EXISTS idx_papers_name     ON papers(paper_name);
CREATE INDEX IF NOT EXISTS idx_diagrams_hash   ON diagrams(paper_hash);
"""


@dataclass(frozen=True)
class PaperRecord:
    paper_hash: str
    paper_name: str
    pdf_path: str
    page_count: Optional[int]
    chunk_strategy: Optional[str]
    model_used: Optional[str]
    code_model: Optional[str]
    processed_at: Optional[str]
    sections_completed: List[str]
    summary_md: Optional[str]
    symbolic_logic_md: Optional[str]
    cpp_examples_md: Optional[str]
    extras_md: Optional[str]
    diagrams_raw_output: Optional[str]
    source_corpus: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DiagramRecord:
    paper_hash: str
    idx: int
    title: str
    dot_src: str
    svg_content: Optional[str]


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    age_seconds: Optional[float] = None
    reclaimed: bool = False


# ── connection / lifecycle ──────────────────────────────────────────────────
def resolve_db_path(cli_value: Optional[str] = None) -> Path:
    if cli_value:
        return Path(os.path.expandvars(cli_value)).expanduser()
    env = os.environ.get(DB_PATH_ENV_VAR)
    if env:
        return Path(os.path.expandvars(env)).expanduser()
    return DEFAULT_DB_PATH


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout: the main processing loop writes while
    # _periodic_sync_worker() reads from a background thread every 300s, and
    # vram_resident_processor.py may run two OS processes against this same
    # file — WAL supports concurrent readers with one writer across both
    # threads and processes; busy_timeout retries instead of raising
    # "database is locked" on transient contention.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_paper_record(row: sqlite3.Row) -> PaperRecord:
    return PaperRecord(
        paper_hash=row["paper_hash"],
        paper_name=row["paper_name"],
        pdf_path=row["pdf_path"],
        page_count=row["page_count"],
        chunk_strategy=row["chunk_strategy"],
        model_used=row["model_used"],
        code_model=row["code_model"],
        processed_at=row["processed_at"],
        sections_completed=json.loads(row["sections_completed"] or "[]"),
        summary_md=row["summary_md"],
        symbolic_logic_md=row["symbolic_logic_md"],
        cpp_examples_md=row["cpp_examples_md"],
        extras_md=row["extras_md"],
        diagrams_raw_output=row["diagrams_raw_output"],
        source_corpus=row["source_corpus"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── claim / lock (replaces .processing.lock) ────────────────────────────────
def try_claim(
    conn: sqlite3.Connection,
    pdf_path: str,
    claimed_by: Optional[str] = None,
    stale_after: float = STALE_LOCK_SECONDS,
) -> ClaimResult:
    # Keyed by pdf_path, not paper_hash: the hash isn't known yet at claim
    # time (hashing happens after OCR extraction begins), so the claim must
    # be taken on the one identifier available up front — same ordering the
    # old .processing.lock (created from the pre-hash paper dir path) used.
    now = time.time()
    cur = conn.execute(
        "INSERT OR IGNORE INTO processing_locks(pdf_path, claimed_at, claimed_by) "
        "VALUES (?, ?, ?)",
        (pdf_path, now, claimed_by),
    )
    conn.commit()
    if cur.rowcount == 1:
        return ClaimResult(claimed=True)

    row = conn.execute(
        "SELECT claimed_at FROM processing_locks WHERE pdf_path = ?", (pdf_path,)
    ).fetchone()
    if row is None:
        # Released between the INSERT OR IGNORE and this SELECT — retry once.
        return try_claim(conn, pdf_path, claimed_by, stale_after)

    age = now - row["claimed_at"]
    if age < stale_after:
        return ClaimResult(claimed=False, age_seconds=age)

    cur = conn.execute(
        "UPDATE processing_locks SET claimed_at = ?, claimed_by = ? "
        "WHERE pdf_path = ? AND claimed_at = ?",
        (now, claimed_by, pdf_path, row["claimed_at"]),
    )
    conn.commit()
    if cur.rowcount == 1:
        return ClaimResult(claimed=True, age_seconds=age, reclaimed=True)
    # Lost the race to another worker reclaiming it at the same instant.
    return ClaimResult(claimed=False, age_seconds=age)


def release_claim(conn: sqlite3.Connection, pdf_path: str) -> None:
    conn.execute("DELETE FROM processing_locks WHERE pdf_path = ?", (pdf_path,))
    conn.commit()


# ── read ─────────────────────────────────────────────────────────────────────
def load_paper(conn: sqlite3.Connection, paper_hash: str) -> Optional[PaperRecord]:
    row = conn.execute(
        "SELECT * FROM papers WHERE paper_hash = ?", (paper_hash,)
    ).fetchone()
    return _row_to_paper_record(row) if row else None


def load_paper_by_pdf_path(conn: sqlite3.Connection, pdf_path: str) -> Optional[PaperRecord]:
    row = conn.execute(
        "SELECT * FROM papers WHERE pdf_path = ? ORDER BY updated_at DESC LIMIT 1",
        (pdf_path,),
    ).fetchone()
    return _row_to_paper_record(row) if row else None


def should_run(record: Optional[PaperRecord], section: str, reprocess: Optional[str]) -> bool:
    if reprocess in (section, "all"):
        return True
    completed = record.sections_completed if record else []
    return section not in completed


# ── write ────────────────────────────────────────────────────────────────────
def upsert_paper_meta(
    conn: sqlite3.Connection,
    paper_hash: str,
    paper_name: str,
    pdf_path: str,
    page_count: int,
    chunk_strategy: str,
    model_used: str,
    code_model: str,
    source_corpus: Optional[str] = None,
    processed_at: Optional[str] = None,
) -> None:
    # processed_at defaults to "now" (the normal live-processing case — this
    # call happens right after the paper was actually processed). Migration
    # tooling passes the real historical value from the source metadata.json
    # explicitly, since "now" would be the migration run's timestamp, not
    # when the paper was actually processed — and conflict resolution across
    # duplicate source copies depends on that timestamp being genuine.
    now = _now_iso()
    processed_at = processed_at or now
    conn.execute(
        """
        INSERT INTO papers (
            paper_hash, paper_name, pdf_path, page_count, chunk_strategy,
            model_used, code_model, processed_at, sections_completed,
            source_corpus, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
        ON CONFLICT(paper_hash) DO UPDATE SET
            paper_name     = excluded.paper_name,
            pdf_path       = excluded.pdf_path,
            page_count     = excluded.page_count,
            chunk_strategy = excluded.chunk_strategy,
            model_used     = excluded.model_used,
            code_model     = excluded.code_model,
            processed_at   = excluded.processed_at,
            source_corpus  = COALESCE(papers.source_corpus, excluded.source_corpus),
            updated_at     = excluded.updated_at
        """,
        (
            paper_hash, paper_name, pdf_path, page_count, chunk_strategy,
            model_used, code_model, processed_at, source_corpus, now, now,
        ),
    )
    conn.commit()


def mark_section_complete(conn: sqlite3.Connection, paper_hash: str, section: str) -> None:
    row = conn.execute(
        "SELECT sections_completed FROM papers WHERE paper_hash = ?", (paper_hash,)
    ).fetchone()
    if row is None:
        return
    completed = json.loads(row["sections_completed"] or "[]")
    if section not in completed:
        completed.append(section)
        conn.execute(
            "UPDATE papers SET sections_completed = ?, updated_at = ? WHERE paper_hash = ?",
            (json.dumps(completed), _now_iso(), paper_hash),
        )
        conn.commit()


def write_section(conn: sqlite3.Connection, paper_hash: str, section: str, content: str) -> None:
    if section not in _SECTION_COLUMNS:
        raise ValueError(f"Unknown markdown section: {section!r}")
    column = _SECTION_COLUMNS[section]
    conn.execute(
        f"UPDATE papers SET {column} = ?, updated_at = ? WHERE paper_hash = ?",
        (content, _now_iso(), paper_hash),
    )
    conn.commit()
    mark_section_complete(conn, paper_hash, section)


def replace_diagrams(
    conn: sqlite3.Connection,
    paper_hash: str,
    diagrams: List[Tuple[str, str, Optional[str]]],
) -> None:
    conn.execute("DELETE FROM diagrams WHERE paper_hash = ?", (paper_hash,))
    conn.executemany(
        "INSERT INTO diagrams (paper_hash, idx, title, dot_src, svg_content) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (paper_hash, idx, title, dot_src, svg_content)
            for idx, (title, dot_src, svg_content) in enumerate(diagrams, 1)
        ],
    )
    conn.execute(
        "UPDATE papers SET diagrams_raw_output = NULL, updated_at = ? WHERE paper_hash = ?",
        (_now_iso(), paper_hash),
    )
    conn.commit()


def write_diagrams_raw_output(conn: sqlite3.Connection, paper_hash: str, raw_text: str) -> None:
    conn.execute(
        "UPDATE papers SET diagrams_raw_output = ?, updated_at = ? WHERE paper_hash = ?",
        (raw_text, _now_iso(), paper_hash),
    )
    conn.commit()


def clear_section(conn: sqlite3.Connection, paper_hash: str, section: str) -> None:
    row = conn.execute(
        "SELECT sections_completed FROM papers WHERE paper_hash = ?", (paper_hash,)
    ).fetchone()
    if row is None:
        return  # nothing to clear yet

    now = _now_iso()
    if section == "all":
        conn.execute(
            """
            UPDATE papers SET
                summary_md = NULL, symbolic_logic_md = NULL, cpp_examples_md = NULL,
                extras_md = NULL, diagrams_raw_output = NULL,
                sections_completed = '[]', updated_at = ?
            WHERE paper_hash = ?
            """,
            (now, paper_hash),
        )
        conn.execute("DELETE FROM diagrams WHERE paper_hash = ?", (paper_hash,))
        conn.commit()
        return

    completed = json.loads(row["sections_completed"] or "[]")
    if section in completed:
        completed.remove(section)

    if section == "diagrams":
        conn.execute("DELETE FROM diagrams WHERE paper_hash = ?", (paper_hash,))
        conn.execute(
            "UPDATE papers SET diagrams_raw_output = NULL, sections_completed = ?, "
            "updated_at = ? WHERE paper_hash = ?",
            (json.dumps(completed), now, paper_hash),
        )
    elif section in _SECTION_COLUMNS:
        column = _SECTION_COLUMNS[section]
        conn.execute(
            f"UPDATE papers SET {column} = NULL, sections_completed = ?, "
            f"updated_at = ? WHERE paper_hash = ?",
            (json.dumps(completed), now, paper_hash),
        )
    else:
        raise ValueError(f"Unknown section: {section!r}")
    conn.commit()


# ── OCR cache (replaces .ocr_cache/<hash>_p####.txt) ────────────────────────
def get_cached_ocr_page(conn: sqlite3.Connection, paper_hash: str, page_idx: int) -> Optional[str]:
    row = conn.execute(
        "SELECT text FROM ocr_cache WHERE paper_hash = ? AND page_idx = ?",
        (paper_hash, page_idx),
    ).fetchone()
    return row["text"] if row else None


def put_cached_ocr_page(conn: sqlite3.Connection, paper_hash: str, page_idx: int, text: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ocr_cache (paper_hash, page_idx, text) VALUES (?, ?, ?)",
        (paper_hash, page_idx, text),
    )
    conn.commit()


# ── consumer-facing bulk read ────────────────────────────────────────────────
def iter_papers_for_sync(
    conn: sqlite3.Connection,
    since_hash_map: Optional[Dict[str, str]] = None,
) -> Iterator[PaperRecord]:
    cur = conn.execute("SELECT * FROM papers ORDER BY paper_hash")
    for row in cur:
        record = _row_to_paper_record(row)
        if since_hash_map is not None and since_hash_map.get(record.paper_hash) == record.paper_hash:
            continue
        yield record


def load_diagrams(conn: sqlite3.Connection, paper_hash: str) -> List[DiagramRecord]:
    rows = conn.execute(
        "SELECT * FROM diagrams WHERE paper_hash = ? ORDER BY idx", (paper_hash,)
    ).fetchall()
    return [
        DiagramRecord(
            paper_hash=r["paper_hash"],
            idx=r["idx"],
            title=r["title"],
            dot_src=r["dot_src"],
            svg_content=r["svg_content"],
        )
        for r in rows
    ]


def get_diagram_svg(conn: sqlite3.Connection, paper_hash: str, idx: int) -> Optional[Tuple[str, str]]:
    row = conn.execute(
        "SELECT title, svg_content FROM diagrams WHERE paper_hash = ? AND idx = ?",
        (paper_hash, idx),
    ).fetchone()
    if row is None or row["svg_content"] is None:
        return None
    return row["title"], row["svg_content"]
