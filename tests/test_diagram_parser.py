"""
tests/test_diagram_parser.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Locks down the LLM-output extraction helpers in paper_processor.py:
  • parse_diagrams   — delimited ===DIAGRAM_START/END=== blocks, with a
                       fenced-code-block fallback.
  • ensure_neon_black — non-destructive style injection.
  • build_chunks     — sliding-window page chunking.

Unlike the importer parsers, these are reasonably robust (parse_diagrams has a
fallback, ensure_neon_black is idempotent). This suite pins that good behaviour
so a future refactor can't silently regress it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("fitz")          # paper_processor imports pymupdf at module load
pytest.importorskip("requests")

import paper_processor as pp  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# parse_diagrams — delimited form (the format DIAGRAM_PROMPT asks for)
# ════════════════════════════════════════════════════════════════════════════
class TestParseDiagramsDelimited:
    def test_single_block(self):
        raw = "===DIAGRAM_START: Architecture===\ndigraph G { a -> b; }\n===DIAGRAM_END==="
        out = pp.parse_diagrams(raw)
        assert len(out) == 1
        title, dot = out[0]
        assert title == "Architecture"
        assert dot.startswith("digraph G")

    def test_multiple_blocks_preserve_order(self):
        raw = (
            "===DIAGRAM_START: First===\ndigraph G { a -> b; }\n===DIAGRAM_END===\n"
            "===DIAGRAM_START: Second===\ndigraph H { x -> y; }\n===DIAGRAM_END==="
        )
        out = pp.parse_diagrams(raw)
        assert [t for t, _ in out] == ["First", "Second"]

    def test_block_without_graph_keyword_is_skipped(self):
        raw = "===DIAGRAM_START: Bogus===\nthis is not dot source\n===DIAGRAM_END==="
        assert pp.parse_diagrams(raw) == []

    def test_prose_around_blocks_is_ignored(self):
        raw = (
            "Here are your diagrams:\n"
            "===DIAGRAM_START: A===\ngraph G { a -- b; }\n===DIAGRAM_END===\n"
            "Hope that helps!"
        )
        out = pp.parse_diagrams(raw)
        assert len(out) == 1 and out[0][0] == "A"


# ════════════════════════════════════════════════════════════════════════════
# parse_diagrams — fenced fallback (when the model ignores the delimiters)
# ════════════════════════════════════════════════════════════════════════════
class TestParseDiagramsFencedFallback:
    def test_dot_fence(self):
        raw = "```dot\ndigraph G { a -> b; }\n```"
        out = pp.parse_diagrams(raw)
        assert len(out) == 1
        assert out[0][0] == "diagram_01"          # auto-numbered title
        assert out[0][1].startswith("digraph G")

    def test_graphviz_fence_and_plain_fence(self):
        raw = (
            "```graphviz\ndigraph G { a -> b; }\n```\n"
            "```\ngraph H { c -- d; }\n```"
        )
        out = pp.parse_diagrams(raw)
        assert [t for t, _ in out] == ["diagram_01", "diagram_02"]

    def test_delimited_takes_precedence_over_fences(self):
        # If proper delimiters exist, the fenced fallback must NOT also fire.
        raw = (
            "===DIAGRAM_START: Real===\ndigraph G { a -> b; }\n===DIAGRAM_END===\n"
            "```dot\ndigraph Extra { z -> w; }\n```"
        )
        out = pp.parse_diagrams(raw)
        assert len(out) == 1 and out[0][0] == "Real"

    def test_no_diagrams_returns_empty(self):
        assert pp.parse_diagrams("The model refused and wrote a paragraph.") == []


# ════════════════════════════════════════════════════════════════════════════
# ensure_neon_black — non-destructive style injection
# ════════════════════════════════════════════════════════════════════════════
class TestEnsureNeonBlack:
    def test_injects_when_missing(self):
        out = pp.ensure_neon_black("digraph G { a -> b; }")
        assert "bgcolor" in out
        assert "#00FF41" in out                   # neon accent applied

    def test_idempotent(self):
        once = pp.ensure_neon_black("digraph G { a -> b; }")
        twice = pp.ensure_neon_black(once)
        assert once == twice

    def test_respects_existing_bgcolor(self):
        src = 'digraph G { graph [bgcolor="white"]; a -> b; }'
        assert pp.ensure_neon_black(src) == src    # untouched

    def test_handles_undirected_graph(self):
        out = pp.ensure_neon_black("graph H { c -- d; }")
        assert "bgcolor" in out


# ════════════════════════════════════════════════════════════════════════════
# build_chunks — sliding window with overlap
# ════════════════════════════════════════════════════════════════════════════
class TestBuildChunks:
    def test_small_doc_is_single_chunk(self):
        assert len(pp.build_chunks(["a", "b", "c"], window=12)) == 1

    def test_exactly_window_size_single_chunk(self):
        pages = [f"p{i}" for i in range(12)]
        assert len(pp.build_chunks(pages, window=12)) == 1

    def test_large_doc_chunks_with_overlap(self):
        pages = [f"p{i}" for i in range(30)]
        chunks = pp.build_chunks(pages, window=12, overlap=2)
        assert len(chunks) > 1
        # Overlap means consecutive chunks share boundary pages.
        assert "p10" in chunks[0] and "p10" in chunks[1]

    def test_every_page_appears_at_least_once(self):
        pages = [f"PAGE_{i}_END" for i in range(25)]
        joined = "\n".join(pp.build_chunks(pages, window=12, overlap=2))
        for i in range(25):
            assert f"PAGE_{i}_END" in joined

    def test_no_overlap_setting(self):
        pages = [f"p{i}" for i in range(20)]
        chunks = pp.build_chunks(pages, window=10, overlap=0)
        assert "p9" in chunks[0] and "p9" not in chunks[1]
