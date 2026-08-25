"""
Tests for ocr_fallback.py.

The pure-logic tests (thresholds, mode validation, stats, graceful
degradation) run anywhere. The two OCR round-trip tests are skipped
automatically when Tesseract is not installed, so CI stays green on minimal
runners while still exercising the real path on a dev box.

Run:  pytest tests/test_ocr_fallback.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ocr_fallback as ocr  # noqa: E402

fitz = pytest.importorskip("fitz")  # pymupdf is a hard dependency

OCR_OK, _ = ocr.ocr_available()
needs_ocr = pytest.mark.skipif(not OCR_OK, reason="tesseract not installed")

SAMPLE_LINES = [
    "Attention Is All You Need",
    "We propose the Transformer, a model architecture",
    "relying entirely on an attention mechanism.",
]


# ── Fixtures: build a text PDF and an image-only (scanned) PDF ──────────────
@pytest.fixture
def text_pdf(tmp_path) -> Path:
    p = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in SAMPLE_LINES:
        page.insert_text((72, y), line, fontsize=14)
        y += 24
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture
def scanned_pdf(tmp_path) -> Path:
    """A PDF whose only content is a rasterised image of text (no text layer)."""
    from PIL import Image, ImageDraw, ImageFont

    # PIL's default bitmap font renders at ~10px and OCRs unreliably (e.g.
    # "Attention" -> "Atte ntion"). Use a real scalable font at a legible
    # size so Tesseract has something realistic to read.
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28
        )
    except OSError:
        font = ImageFont.load_default()

    img = Image.new("RGB", (1240, 600), "white")
    d = ImageDraw.Draw(img)
    y = 40
    for line in SAMPLE_LINES:
        d.text((60, y), line, fill="black", font=font)
        y += 80
    png = tmp_path / "_page.png"
    img.save(str(png))

    p = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=620, height=300)
    page.insert_image(page.rect, filename=str(png))
    doc.save(str(p))
    doc.close()
    return p


# ── Pure-logic tests (no Tesseract required) ────────────────────────────────
def test_page_needs_ocr_threshold():
    assert ocr.page_needs_ocr("", min_chars=100) is True
    assert ocr.page_needs_ocr("   \n\t ", min_chars=100) is True
    assert ocr.page_needs_ocr("x" * 50, min_chars=100) is True
    assert ocr.page_needs_ocr("x" * 150, min_chars=100) is False


def test_invalid_mode_raises(text_pdf):
    with pytest.raises(ValueError):
        ocr.extract_pages_with_ocr(text_pdf, mode="bogus")


def test_text_pdf_no_ocr(text_pdf):
    pages, stats = ocr.extract_pages_with_ocr(text_pdf, mode="auto")
    assert len(pages) == 1
    assert "Transformer" in pages[0]
    assert stats.native_pages == 1
    assert stats.ocr_pages == 0
    assert stats.ocr_used is False


def test_never_mode_skips_scanned(scanned_pdf):
    # Without OCR, an image-only PDF yields no usable pages — the original bug.
    pages, stats = ocr.extract_pages_with_ocr(scanned_pdf, mode="never")
    assert pages == []
    assert stats.ocr_used is False
    assert stats.skipped_blank == 1


def test_stats_summary_smoke():
    s = ocr.OcrStats(total_pages=3, native_pages=3)
    assert "native" in s.summary()


# ── Round-trip OCR tests (skipped without Tesseract) ────────────────────────
@needs_ocr
def test_scanned_pdf_recovered_via_ocr(scanned_pdf):
    pages, stats = ocr.extract_pages_with_ocr(scanned_pdf, mode="auto")
    assert len(pages) == 1
    assert "Attention" in pages[0]
    assert stats.ocr_pages == 1
    assert stats.ocr_used is True


@needs_ocr
def test_ocr_cache_hit(scanned_pdf, tmp_path):
    cache = tmp_path / ".ocr_cache"
    h = "cafebabecafebabe"

    _, s1 = ocr.extract_pages_with_ocr(scanned_pdf, mode="auto",
                                       paper_hash=h, cache_dir=cache)
    assert s1.ocr_pages == 1 and s1.cached_pages == 0
    assert any(cache.glob("*.txt"))

    _, s2 = ocr.extract_pages_with_ocr(scanned_pdf, mode="auto",
                                       paper_hash=h, cache_dir=cache)
    assert s2.ocr_pages == 0 and s2.cached_pages == 1
