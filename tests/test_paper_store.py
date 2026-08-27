"""
Tests for paper_store.py, the SQLite-backed storage layer that replaces the
old per-paper _processed/<slug>/{metadata.json,*.md,diagrams/} file tree.

Run:  pytest tests/test_paper_store.py -v
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

paper_store = importlib.import_module("paper_store")


@pytest.fixture
def conn(tmp_path):
    return paper_store.connect(tmp_path / "papers.db")


def _make_paper(conn, paper_hash="h1", source_corpus=None):
    paper_store.upsert_paper_meta(
        conn,
        paper_hash=paper_hash,
        paper_name="attention.pdf",
        pdf_path=f"/papers/{paper_hash}.pdf",
        page_count=12,
        chunk_strategy="single-pass (12 pages)",
        model_used="qwen3.6:35b",
        code_model="qwen3.6:35b",
        source_corpus=source_corpus,
    )


# ── connection / schema ──────────────────────────────────────────────────────
def test_connect_creates_schema_idempotently(tmp_path):
    db_path = tmp_path / "papers.db"
    conn1 = paper_store.connect(db_path)
    _make_paper(conn1)
    conn1.close()

    conn2 = paper_store.connect(db_path)  # re-running CREATE TABLE IF NOT EXISTS
    record = paper_store.load_paper(conn2, "h1")
    assert record is not None
    assert record.paper_name == "attention.pdf"


# ── upsert_paper_meta ────────────────────────────────────────────────────────
def test_upsert_paper_meta_insert_then_update(conn):
    _make_paper(conn, source_corpus="treeA")
    record = paper_store.load_paper(conn, "h1")
    assert record.page_count == 12
    assert record.source_corpus == "treeA"

    paper_store.upsert_paper_meta(
        conn,
        paper_hash="h1",
        paper_name="attention.pdf",
        pdf_path="/papers/h1.pdf",
        page_count=99,
        chunk_strategy="sliding-window (3 chunks, 99 pages)",
        model_used="gemma4:26b-a4b-it-q4_K_M",
        code_model="gemma4:26b-a4b-it-q4_K_M",
        source_corpus="treeB",
    )
    updated = paper_store.load_paper(conn, "h1")
    assert updated.page_count == 99
    assert updated.model_used == "gemma4:26b-a4b-it-q4_K_M"
    # First-write provenance sticks — later upserts don't clobber it.
    assert updated.source_corpus == "treeA"


def test_upsert_paper_meta_processed_at_defaults_to_now(conn):
    _make_paper(conn)
    record = paper_store.load_paper(conn, "h1")
    assert record.processed_at is not None and record.processed_at != ""


def test_upsert_paper_meta_processed_at_override_for_migration(conn):
    # Migration tooling must preserve the source's real historical
    # processed_at, not stamp it with the migration run's own timestamp —
    # conflict resolution across duplicate source copies depends on this.
    paper_store.upsert_paper_meta(
        conn,
        paper_hash="h1",
        paper_name="attention.pdf",
        pdf_path="/papers/h1.pdf",
        page_count=12,
        chunk_strategy="single-pass (12 pages)",
        model_used="qwen3.6:35b",
        code_model="qwen3.6:35b",
        processed_at="2020-01-01T00:00:00",
    )
    record = paper_store.load_paper(conn, "h1")
    assert record.processed_at == "2020-01-01T00:00:00"


def test_load_paper_by_pdf_path(conn):
    _make_paper(conn, paper_hash="h1")
    record = paper_store.load_paper_by_pdf_path(conn, "/papers/h1.pdf")
    assert record is not None
    assert record.paper_hash == "h1"
    assert paper_store.load_paper_by_pdf_path(conn, "/papers/missing.pdf") is None


# ── should_run / write_section ──────────────────────────────────────────────
def test_should_run_before_and_after_write_section(conn):
    _make_paper(conn)
    record = paper_store.load_paper(conn, "h1")
    assert paper_store.should_run(record, "summary", None) is True

    paper_store.write_section(conn, "h1", "summary", "# Summary\n\nContent.")
    record = paper_store.load_paper(conn, "h1")
    assert record.summary_md == "# Summary\n\nContent."
    assert "summary" in record.sections_completed
    assert paper_store.should_run(record, "summary", None) is False


def test_should_run_reprocess_forces_true(conn):
    _make_paper(conn)
    paper_store.write_section(conn, "h1", "summary", "done")
    record = paper_store.load_paper(conn, "h1")
    assert paper_store.should_run(record, "summary", "summary") is True
    assert paper_store.should_run(record, "summary", "all") is True
    assert paper_store.should_run(record, "logic", None) is True


def test_should_run_with_no_record_yet():
    assert paper_store.should_run(None, "summary", None) is True


def test_write_section_rejects_unknown_section(conn):
    _make_paper(conn)
    with pytest.raises(ValueError):
        paper_store.write_section(conn, "h1", "diagrams", "nope")


# ── diagrams ─────────────────────────────────────────────────────────────────
def test_replace_diagrams_purges_stale_entries(conn):
    _make_paper(conn)
    paper_store.replace_diagrams(
        conn, "h1",
        [("First", "digraph{a->b}", "<svg>1</svg>"), ("Second", "digraph{c->d}", "<svg>2</svg>")],
    )
    diagrams = paper_store.load_diagrams(conn, "h1")
    assert [d.idx for d in diagrams] == [1, 2]
    assert [d.title for d in diagrams] == ["First", "Second"]

    paper_store.replace_diagrams(conn, "h1", [("Only", "digraph{x->y}", None)])
    diagrams = paper_store.load_diagrams(conn, "h1")
    assert len(diagrams) == 1
    assert diagrams[0].title == "Only"
    assert diagrams[0].svg_content is None


def test_get_diagram_svg(conn):
    _make_paper(conn)
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", "<svg>1</svg>")])
    assert paper_store.get_diagram_svg(conn, "h1", 1) == ("First", "<svg>1</svg>")
    assert paper_store.get_diagram_svg(conn, "h1", 2) is None  # no idx 2


def test_get_diagram_svg_returns_none_when_render_failed(conn):
    _make_paper(conn)
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", None)])
    assert paper_store.get_diagram_svg(conn, "h1", 1) is None


def test_write_diagrams_raw_output_does_not_purge_existing_diagrams(conn):
    _make_paper(conn)
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", "<svg>1</svg>")])
    paper_store.write_diagrams_raw_output(conn, "h1", "unparseable LLM output")

    diagrams = paper_store.load_diagrams(conn, "h1")
    assert len(diagrams) == 1  # untouched
    record = paper_store.load_paper(conn, "h1")
    assert record.diagrams_raw_output == "unparseable LLM output"


def test_replace_diagrams_clears_stale_raw_output(conn):
    _make_paper(conn)
    paper_store.write_diagrams_raw_output(conn, "h1", "unparseable LLM output")
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", "<svg>1</svg>")])
    record = paper_store.load_paper(conn, "h1")
    assert record.diagrams_raw_output is None


# ── clear_section (--reprocess) ─────────────────────────────────────────────
def test_clear_section_markdown(conn):
    _make_paper(conn)
    paper_store.write_section(conn, "h1", "summary", "s")
    paper_store.write_section(conn, "h1", "logic", "l")

    paper_store.clear_section(conn, "h1", "summary")
    record = paper_store.load_paper(conn, "h1")
    assert record.summary_md is None
    assert record.symbolic_logic_md == "l"
    assert record.sections_completed == ["logic"]


def test_clear_section_diagrams(conn):
    _make_paper(conn)
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", "<svg>1</svg>")])
    paper_store.mark_section_complete(conn, "h1", "diagrams")

    paper_store.clear_section(conn, "h1", "diagrams")
    assert paper_store.load_diagrams(conn, "h1") == []
    record = paper_store.load_paper(conn, "h1")
    assert record.diagrams_raw_output is None
    assert "diagrams" not in record.sections_completed


def test_clear_section_all_keeps_the_row(conn):
    _make_paper(conn)
    paper_store.write_section(conn, "h1", "summary", "s")
    paper_store.write_section(conn, "h1", "extras", "e")
    paper_store.replace_diagrams(conn, "h1", [("First", "digraph{a->b}", "<svg>1</svg>")])

    paper_store.clear_section(conn, "h1", "all")
    record = paper_store.load_paper(conn, "h1")
    assert record is not None  # row itself survives — only content is cleared
    assert record.summary_md is None
    assert record.extras_md is None
    assert record.sections_completed == []
    assert paper_store.load_diagrams(conn, "h1") == []


def test_clear_section_unknown_raises(conn):
    _make_paper(conn)
    with pytest.raises(ValueError):
        paper_store.clear_section(conn, "h1", "bogus")


def test_clear_section_on_missing_paper_is_a_noop(conn):
    paper_store.clear_section(conn, "does-not-exist", "all")  # must not raise
    assert paper_store.load_paper(conn, "does-not-exist") is None


# ── OCR cache ────────────────────────────────────────────────────────────────
def test_ocr_cache_round_trip(conn):
    assert paper_store.get_cached_ocr_page(conn, "h1", 3) is None
    paper_store.put_cached_ocr_page(conn, "h1", 3, "page three text")
    assert paper_store.get_cached_ocr_page(conn, "h1", 3) == "page three text"
    assert paper_store.get_cached_ocr_page(conn, "h1", 4) is None


# ── claim / lock ─────────────────────────────────────────────────────────────
def test_try_claim_blocks_second_claimant_then_release_unblocks(conn):
    first = paper_store.try_claim(conn, "/pdfs/a.pdf", claimed_by="worker-a")
    assert first.claimed is True
    assert first.reclaimed is False

    second = paper_store.try_claim(conn, "/pdfs/a.pdf", claimed_by="worker-b")
    assert second.claimed is False
    assert second.reclaimed is False
    assert second.age_seconds is not None

    paper_store.release_claim(conn, "/pdfs/a.pdf")

    third = paper_store.try_claim(conn, "/pdfs/a.pdf", claimed_by="worker-b")
    assert third.claimed is True


def test_try_claim_reclaims_stale_lock(conn, monkeypatch):
    times = iter([1000.0, 1000.0 + paper_store.STALE_LOCK_SECONDS + 1])

    class FakeTime:
        def time(self):
            return next(times)

    monkeypatch.setattr(paper_store, "time", FakeTime())

    first = paper_store.try_claim(conn, "/pdfs/a.pdf", claimed_by="worker-a")
    assert first.claimed is True

    second = paper_store.try_claim(conn, "/pdfs/a.pdf", claimed_by="worker-b")
    assert second.claimed is True
    assert second.reclaimed is True
    assert second.age_seconds == pytest.approx(paper_store.STALE_LOCK_SECONDS + 1)


# ── iter_papers_for_sync ─────────────────────────────────────────────────────
def test_iter_papers_for_sync_yields_all_without_hash_map(conn):
    _make_paper(conn, paper_hash="h1")
    _make_paper(conn, paper_hash="h2")
    hashes = {r.paper_hash for r in paper_store.iter_papers_for_sync(conn)}
    assert hashes == {"h1", "h2"}


def test_iter_papers_for_sync_skips_already_synced_unchanged_hash(conn):
    _make_paper(conn, paper_hash="h1")
    _make_paper(conn, paper_hash="h2")
    since = {"h1": "h1"}  # h1 already synced at its current hash; h2 is new
    hashes = {r.paper_hash for r in paper_store.iter_papers_for_sync(conn, since_hash_map=since)}
    assert hashes == {"h2"}
