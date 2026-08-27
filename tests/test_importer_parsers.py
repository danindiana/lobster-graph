"""
tests/test_importer_parsers.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Locks down the pure markdown-parsing functions in neo4j_viz/neo4j_importer.py.

These parsers turn free-form LLM markdown into graph nodes. They are the most
fragile surface in the pipeline: each one assumes the model emits an EXACT
shape, and when the model drifts they return nothing — silently producing an
empty graph with no error.

This suite does three jobs:
  1. Pins the canonical happy-path behaviour the prompts are written to elicit.
  2. Documents the known brittleness explicitly (drifted inputs → empty), so the
     fragility is a visible, testable contract rather than a surprise. If/when a
     parser is hardened, the matching `xfail` flips to a pass and tells you.
  3. Captures one genuine correctness BUG in parse_logic_algorithms (the
     "Invariant" line is mis-parsed as its own algorithm). The bug test is
     marked xfail(strict=True): it will start FAILING THE BUILD the moment the
     bug is fixed, prompting you to convert it into a normal assertion.

The Neo4j driver is stubbed so these run with no database and no `neo4j`
package installed (CI-friendly).
"""
import sys
import types
from pathlib import Path

import pytest

# ── Import the importer's pure functions without a live Neo4j driver ────────
_fake_neo4j = types.ModuleType("neo4j")
_fake_neo4j.GraphDatabase = object  # never touched by the parser functions
sys.modules.setdefault("neo4j", _fake_neo4j)

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "neo4j_viz"))

import neo4j_importer as imp  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# clean_markdown_headers
# ════════════════════════════════════════════════════════════════════════════
class TestCleanMarkdownHeaders:
    def test_splits_on_h2_and_skips_title(self):
        md = (
            "# Summary\n\n"
            "## Motivation & Problem Statement\nThe gap is X.\n\n"
            "## Core Methodology\nWe do Y.\n"
        )
        out = imp.clean_markdown_headers(md)
        assert out["Motivation & Problem Statement"] == "The gap is X."
        assert out["Core Methodology"] == "We do Y."
        # The H1 title line is dropped, not turned into a section.
        assert "Summary" not in out

    def test_preserves_number_prefixes_in_keys(self):
        md = "## 1. Core Definitions & Notation\n- **Tensor**: array.\n"
        out = imp.clean_markdown_headers(md)
        # The number prefix is part of the key — callers must match it exactly.
        assert "1. Core Definitions & Notation" in out
        assert "Core Definitions & Notation" not in out

    def test_h3_is_content_not_a_section(self):
        md = "## Section\nbody\n### Subsection\nmore\n"
        out = imp.clean_markdown_headers(md)
        assert set(out) == {"Section"}
        assert "### Subsection" in out["Section"]

    def test_intro_key_only_when_pre_header_content_exists(self):
        # No content before the first H2 → no 'Intro' key at all.
        assert "Intro" not in imp.clean_markdown_headers("## A\nbody\n")
        # A bare H1 title contributes no body text, so still no 'Intro'.
        assert "Intro" not in imp.clean_markdown_headers("# Title\n## A\nbody\n")
        # Actual prose before the first H2 → 'Intro' captures it.
        assert imp.clean_markdown_headers("lead\n## A\nb\n")["Intro"] == "lead"

    def test_content_before_first_header_lands_in_intro(self):
        out = imp.clean_markdown_headers("preamble text\n## A\nbody\n")
        assert out["Intro"] == "preamble text"

    def test_empty_input(self):
        assert imp.clean_markdown_headers("") == {}


# ════════════════════════════════════════════════════════════════════════════
# parse_logic_definitions  (also covers parse_logic_theorems — same regex)
# ════════════════════════════════════════════════════════════════════════════
class TestParseLogicDefinitions:
    def test_canonical_bullet_bold_colon(self):
        text = "- **Tensor**: a multi-dimensional array.\n- **Norm**: magnitude ||x||."
        out = imp.parse_logic_definitions(text)
        assert out == [
            {"name": "Tensor", "definition": "a multi-dimensional array."},
            {"name": "Norm", "definition": "magnitude ||x||."},
        ]

    def test_theorems_use_identical_shape(self):
        text = "- **Convergence**: the loss tends to a minimum."
        out = imp.parse_logic_theorems(text)
        assert out == [{"name": "Convergence", "statement": "the loss tends to a minimum."}]

    def test_empty_and_prose_yield_nothing(self):
        assert imp.parse_logic_definitions("") == []
        assert imp.parse_logic_definitions("Just a paragraph with no bullets.") == []

    # ── Documented brittleness: realistic LLM drift silently extracts nothing ──
    @pytest.mark.xfail(reason="known brittleness: single-asterisk emphasis not matched",
                       strict=True)
    def test_drift_single_asterisk(self):
        assert imp.parse_logic_definitions("- *Tensor*: an array.")

    @pytest.mark.xfail(reason="known brittleness: em-dash separator not matched",
                       strict=True)
    def test_drift_emdash_separator(self):
        assert imp.parse_logic_definitions("- **Tensor** — an array.")

    @pytest.mark.xfail(reason="known brittleness: numbered list not matched",
                       strict=True)
    def test_drift_numbered_list(self):
        assert imp.parse_logic_definitions("1. **Tensor**: an array.")

    @pytest.mark.xfail(reason="known brittleness: asterisk/plus bullets not matched",
                       strict=True)
    def test_drift_asterisk_bullet(self):
        assert imp.parse_logic_definitions("* **Tensor**: an array.")


# ════════════════════════════════════════════════════════════════════════════
# parse_logic_algorithms
# ════════════════════════════════════════════════════════════════════════════
class TestParseLogicAlgorithms:
    def test_canonical_extracts_name_and_pseudocode(self):
        text = (
            "- **Gradient Descent**:\n"
            "```pseudocode\nfor t in 1..T: theta -= lr * grad\n```\n"
        )
        out = imp.parse_logic_algorithms(text)
        assert len(out) == 1
        assert out[0]["name"] == "Gradient Descent"
        assert "theta -= lr * grad" in out[0]["pseudocode"]

    def test_code_fence_without_language_tag(self):
        text = "- **Foo**:\n```\nx = 1\n```\n"
        out = imp.parse_logic_algorithms(text)
        assert out and "x = 1" in out[0]["pseudocode"]

    # ── Genuine BUG (not mere brittleness) ─────────────────────────────────
    # The "- **Invariant**:" line is split by the same `- **Name**:` pattern,
    def test_invariant_should_attach_not_become_its_own_node(self):
        text = (
            "- **Newton's Method**:\n"
            "```pseudocode\nx -= f(x)/f'(x)\n```\n"
            "- **Invariant**: |f(x)| shrinks each step.\n"
        )
        out = imp.parse_logic_algorithms(text)
        names = [a["name"] for a in out]
        assert "Invariant" not in names                 # no junk node
        assert len(out) == 1                            # exactly one algorithm
        assert out[0]["invariant"] != ""                # invariant attached


# ════════════════════════════════════════════════════════════════════════════
# parse_cpp_examples
# ════════════════════════════════════════════════════════════════════════════
class TestParseCppExamples:
    def test_canonical_numbered_examples(self):
        content = (
            "### Example 1: Attention\n```cpp\nint main(){return 0;}\n```\n"
            "### Example 2: FFN\n```cpp\ndouble relu(double x){return x>0?x:0;}\n```\n"
        )
        out = imp.parse_cpp_examples(content)
        assert [c["title"] for c in out] == ["Attention", "FFN"]
        assert "int main()" in out[0]["code"]

    def test_example_without_cpp_fence_keeps_title_empty_code(self):
        out = imp.parse_cpp_examples("### Example 1: NoCode\njust prose\n")
        assert out == [{"title": "NoCode", "code": ""}]

    def test_empty_input(self):
        assert imp.parse_cpp_examples("") == []

    # ── Documented brittleness: the cpp PROMPT never mandates "### Example N:" ──
    # so a model that uses any other heading style yields zero CodeSnippet nodes.
    @pytest.mark.xfail(reason="known brittleness: requires literal '### Example N:' heading",
                       strict=True)
    def test_drift_h2_heading(self):
        assert imp.parse_cpp_examples("## Attention\n```cpp\nint main(){}\n```\n")

    @pytest.mark.xfail(reason="known brittleness: 'Example N -' (dash) not matched",
                       strict=True)
    def test_drift_dash_not_colon(self):
        assert imp.parse_cpp_examples("### Example 1 - Attention\n```cpp\nint x;\n```\n")


# ════════════════════════════════════════════════════════════════════════════
# Cross-parser contract: summary vs logic header-key inconsistency
# ════════════════════════════════════════════════════════════════════════════
class TestImporterLookupContract:
    """The importer's main() looks up summary sections by UNNUMBERED key
    (e.g. 'Core Methodology') but logic sections by NUMBERED key
    (e.g. '1. Core Definitions & Notation'). A model is unlikely to be
    consistent across both, so at least one extraction tends to come back
    empty. These tests pin the exact keys main() relies on, so any change to
    the prompt headers or the lookup keys is caught here."""

    SUMMARY_KEYS = [
        "Motivation & Problem Statement",
        "Core Methodology",
        "Key Contributions",
        "Limitations & Failure Modes",
        "Significance",
    ]
    LOGIC_KEYS = [
        "1. Core Definitions & Notation",
        "2. Key Theorems & Propositions",
        "3. Algorithm Formalisation",
    ]

    def test_summary_keys_are_unnumbered(self):
        # If the model numbers its summary headers, these lookups miss.
        md = "## Core Methodology\nbody\n"
        assert "Core Methodology" in imp.clean_markdown_headers(md)
        assert all(not k[0].isdigit() for k in self.SUMMARY_KEYS)

    def test_logic_keys_are_numbered(self):
        # If the model omits the numbers, these lookups miss.
        md = "## 1. Core Definitions & Notation\nbody\n"
        assert "1. Core Definitions & Notation" in imp.clean_markdown_headers(md)
        assert all(k[0].isdigit() for k in self.LOGIC_KEYS)


# ════════════════════════════════════════════════════════════════════════════
# extract_touched_names
# ════════════════════════════════════════════════════════════════════════════
class TestExtractTouchedNames:
    """extract_touched_names() feeds the touched-node sets that scope
    main()'s relationship-inference queries. Its outputs must match the exact
    identity keys (name/title) those queries later MERGE on, or the scoped
    queries silently miss nodes."""

    def test_collects_names_from_defs_algs_and_cpp_examples(self):
        defs = [{"name": "Tensor"}, {"name": "Norm"}]
        algs = [{"name": "Gradient Descent"}]
        cpp = [{"name": "Attention"}, {"title": "FFN"}]  # exercise name/title fallback
        concepts, algorithms, snippets = imp.extract_touched_names(defs, algs, cpp)
        assert concepts == {"Tensor", "Norm"}
        assert algorithms == {"Gradient Descent"}
        assert snippets == {"Attention", "FFN"}

    def test_empty_inputs_yield_empty_sets(self):
        assert imp.extract_touched_names([], [], []) == (set(), set(), set())

    def test_missing_name_keys_are_dropped_not_kept_as_empty_string(self):
        concepts, algorithms, snippets = imp.extract_touched_names(
            [{"definition": "no name key"}], [{}], [{}]
        )
        assert concepts == set()
        assert algorithms == set()
        assert snippets == set()


# ════════════════════════════════════════════════════════════════════════════
# _sanitize_items / _safe_str
# ════════════════════════════════════════════════════════════════════════════
class TestSanitizeItems:
    """Guards against a real crash seen backfilling ~19k real papers: the
    LLM occasionally emits a list item that isn't a {name, ...} object (a
    bare string), or an explicit `"name": null` rather than omitting the
    key. Both used to crash main() mid-corpus via AttributeError /
    'MERGE cannot use a null property value' — losing every remaining
    paper in that run. These must be dropped/coerced, not raise."""

    def test_drops_non_dict_items(self):
        assert imp._sanitize_items(["a bare string", 123, None, {"name": "ok"}]) == [{"name": "ok"}]

    def test_non_list_input_yields_empty_list(self):
        assert imp._sanitize_items("not a list") == []
        assert imp._sanitize_items(None) == []

    def test_empty_list_passes_through(self):
        assert imp._sanitize_items([]) == []


class TestSafeStr:
    def test_missing_key_returns_empty_string(self):
        assert imp._safe_str({}, "name") == ""

    def test_explicit_null_returns_empty_string_not_none(self):
        # dict.get(key, "") would return None here, not "" — the exact bug
        # that crashed a Theorem MERGE on a null name property.
        assert imp._safe_str({"name": None}, "name") == ""

    def test_non_string_value_returns_empty_string(self):
        assert imp._safe_str({"name": 42}, "name") == ""

    def test_string_value_passes_through(self):
        assert imp._safe_str({"name": "Tensor"}, "name") == "Tensor"
