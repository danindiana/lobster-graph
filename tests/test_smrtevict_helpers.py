"""
Tests for the new pure-logic helpers in paper_proc_smrtevict.py:
tier-sort cost estimation, the persisted default-papers-dir config, and
proactive model eviction. These don't need a GPU or a real Ollama model
loaded — Ollama calls are monkeypatched.

Run:  pytest tests/test_smrtevict_helpers.py -v
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("fitz")

pe = importlib.import_module("paper_proc_smrtevict")


# ── _quick_page_count ───────────────────────────────────────────────────────
def test_quick_page_count_reads_real_pdf(tmp_path):
    import fitz

    pdf = tmp_path / "doc.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    assert pe._quick_page_count(pdf) == 3


def test_quick_page_count_returns_zero_on_bad_file(tmp_path):
    bad = tmp_path / "not_a_pdf.pdf"
    bad.write_text("nope")
    assert pe._quick_page_count(bad) == 0


# ── _estimate_sort_cost ─────────────────────────────────────────────────────
def test_estimate_sort_cost_empty_list():
    seconds, cache = pe._estimate_sort_cost([])
    assert seconds == 0.0
    assert cache == {}


def test_estimate_sort_cost_scales_with_total_count(tmp_path, monkeypatch):
    pdfs = [tmp_path / f"p{i}.pdf" for i in range(10)]
    for p in pdfs:
        p.write_text("x")

    monkeypatch.setattr(pe, "_quick_page_count", lambda p: 1)

    class FakeTime:
        _ticks = iter([0.0, 1.0])

        def monotonic(self):
            return next(self._ticks)

    monkeypatch.setattr(pe, "time", FakeTime())

    est_seconds, cache = pe._estimate_sort_cost(pdfs, sample_size=5)
    # 1 second / 5 sampled files = 0.2s/file, extrapolated over all 10
    assert est_seconds == pytest.approx(2.0)
    assert len(cache) == 5


# ── config persistence / resolve_papers_dir ─────────────────────────────────
@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setenv(pe.CONFIG_ENV_VAR, str(cfg))
    return cfg


def test_config_round_trip(isolated_config):
    assert pe._load_config() == {}
    pe._save_default_papers_dir(Path("/tmp/some_papers"))

    data = json.loads(isolated_config.read_text())
    assert data["papers_dir"] == "/tmp/some_papers"
    assert pe._load_default_papers_dir() == Path("/tmp/some_papers")


def test_load_default_papers_dir_falls_back_when_unset(isolated_config):
    assert pe._load_default_papers_dir() == pe.DEFAULT_PAPERS_DIR


def test_resolve_papers_dir_explicit_cli_arg_skips_config(isolated_config):
    resolved = pe.resolve_papers_dir("/explicit/path")
    assert resolved == Path("/explicit/path")
    # explicit CLI arg must not mutate the saved default
    assert not isolated_config.exists()


def test_resolve_papers_dir_noninteractive_uses_saved_default(isolated_config, monkeypatch):
    pe._save_default_papers_dir(Path("/saved/default"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert pe.resolve_papers_dir(None) == Path("/saved/default")


# ── _ensure_model_exclusive ──────────────────────────────────────────────────
def test_ensure_model_exclusive_evicts_other_tier_model(monkeypatch):
    evicted = []
    monkeypatch.setattr(pe, "_ollama_get_loaded", lambda: ["qwen3.6:35b"])
    monkeypatch.setattr(pe, "_ollama_evict", lambda m: evicted.append(m))

    pe._ensure_model_exclusive("gemma4:26b-a4b-it-q4_K_M")

    assert evicted == ["qwen3.6:35b"]


def test_ensure_model_exclusive_noop_when_already_loaded(monkeypatch):
    evicted = []
    monkeypatch.setattr(pe, "_ollama_get_loaded", lambda: ["gemma4:26b-a4b-it-q4_K_M"])
    monkeypatch.setattr(pe, "_ollama_evict", lambda m: evicted.append(m))

    pe._ensure_model_exclusive("gemma4:26b-a4b-it-q4_K_M")

    assert evicted == []


def test_ensure_model_exclusive_ignores_unrelated_models(monkeypatch):
    """A small model outside MODEL_TIERS (e.g. an unrelated model someone
    left loaded) shouldn't be evicted just to make room for a tier model."""
    evicted = []
    monkeypatch.setattr(pe, "_ollama_get_loaded", lambda: ["llama3.2:3b"])
    monkeypatch.setattr(pe, "_ollama_evict", lambda m: evicted.append(m))

    pe._ensure_model_exclusive("gemma4:26b-a4b-it-q4_K_M")

    assert evicted == []
