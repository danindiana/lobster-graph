#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# neo4j_importer.py
# Parses processed paper output from the shared paper_store SQLite database
# and loads it into Neo4j graph database.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import re
import sys
import json
import time
from pathlib import Path
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_store

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")
DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else paper_store.resolve_db_path()

def extract_json_block(text: str) -> dict:
    """Attempts to find and parse a fenced JSON block from the text."""
    matches = list(re.finditer(r"```json\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE))
    if matches:
        try:
            return json.loads(matches[-1].group(1))
        except json.JSONDecodeError:
            pass
    return {}

def clean_markdown_headers(content: str) -> dict:
    """Splits markdown content by H2 headers and returns a dict mapping headers to text."""
    sections = {}
    current_header = "Intro"
    current_text = []

    # Split lines
    for line in content.splitlines():
        if line.startswith("## "):
            if current_text:
                sections[current_header] = "\n".join(current_text).strip()
            current_header = line[3:].strip()
            current_text = []
        elif line.startswith("# "):
            # Skip title
            continue
        else:
            current_text.append(line)

    if current_text:
        sections[current_header] = "\n".join(current_text).strip()

    return sections

def parse_logic_definitions(text: str) -> list:
    """Parses definitions under Core Definitions & Notation."""
    definitions = []
    # Match bullet points: - **Name**: Description
    pattern = r"-\s*\*\*([^*]+)\*\*:\s*(.*)"
    for line in text.splitlines():
        m = re.match(pattern, line.strip())
        if m:
            definitions.append({
                "name": m.group(1).strip(),
                "definition": m.group(2).strip()
            })
    return definitions

def parse_logic_theorems(text: str) -> list:
    """Parses theorems and propositions."""
    theorems = []
    pattern = r"-\s*\*\*([^*]+)\*\*:\s*(.*)"
    for line in text.splitlines():
        m = re.match(pattern, line.strip())
        if m:
            theorems.append({
                "name": m.group(1).strip(),
                "statement": m.group(2).strip()
            })
    return theorems

def parse_logic_algorithms(text: str) -> list:
    """Parses pseudocode blocks."""
    algorithms = []
    # Split text by - **Algorithm Name**: but ignore - **Invariant**:
    pattern = r"-\s*\*\*(?![Ii]nvariant\*\*)([^*]+)\*\*:\s*"
    parts = re.split(pattern, text)
    if len(parts) > 1:
        # parts[0] is intro text before the first algorithm
        for i in range(1, len(parts), 2):
            alg_name = parts[i].strip()
            rest = parts[i+1] if i+1 < len(parts) else ""
            # Extract code blocks
            code_m = re.search(r"```(?:pseudocode)?\s*\n(.*?)\n```", rest, re.DOTALL)
            code = code_m.group(1).strip() if code_m else ""
            # Extract invariant/complexity
            invariant = ""
            inv_m = re.search(r"-\s*\*\*Invariant\*\*:\s*(.*)", rest, re.IGNORECASE)
            if inv_m:
                invariant = inv_m.group(1).strip()
            algorithms.append({
                "name": alg_name,
                "pseudocode": code,
                "invariant": invariant
            })
    return algorithms

def parse_cpp_examples(content: str) -> list:
    """Parses C++ examples from 03_cpp_examples.md."""
    examples = []
    # Split by ### Example
    parts = re.split(r"###\s*Example\s*\d+:\s*", content)
    if len(parts) > 1:
        for part in parts[1:]:
            lines = part.splitlines()
            if not lines:
                continue
            title = lines[0].strip()
            rest = "\n".join(lines[1:])
            # Extract code block
            code_m = re.search(r"```cpp\s*\n(.*?)\n```", rest, re.DOTALL)
            code = code_m.group(1).strip() if code_m else ""
            examples.append({
                "title": title,
                "code": code
            })
    return examples

def _sanitize_items(items) -> list:
    """The per-item LLM JSON output occasionally doesn't match the expected
    {name, ...} dict shape (e.g. a bare string in the list instead of an
    object). Drop anything that isn't a dict rather than letting a single
    malformed paper crash the whole corpus's sync."""
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _safe_str(d: dict, key: str) -> str:
    """dict.get(key, "") only substitutes the default when the key is
    ABSENT — an explicit `"key": null` in the source JSON makes it return
    None instead, which crashes a Cypher MERGE on that property. Coerce
    None/non-string values to an empty string."""
    val = d.get(key)
    return val if isinstance(val, str) else ""


def extract_touched_names(defs: list, algs: list, cpp_examples: list) -> tuple:
    """Given the parsed def/algorithm/code-example lists for ONE synced paper,
    return (Concept names, Algorithm names, CodeSnippet titles) touched this
    cycle — the same identity keys used as MERGE match keys in the per-paper
    loop below. Used to scope the relationship-inference queries to just what
    changed instead of a full cartesian graph scan.
    """
    concept_names = {_safe_str(d, "name") for d in defs} - {""}
    algorithm_names = {_safe_str(a, "name") for a in algs} - {""}
    codesnippet_titles = {_safe_str(c, "name") or _safe_str(c, "title") for c in cpp_examples} - {""}
    return concept_names, algorithm_names, codesnippet_titles

def main():
    global DB_PATH
    if len(sys.argv) > 1:
        DB_PATH = Path(sys.argv[1])
    print(f"🔗 Connecting to Neo4j at {NEO4J_URI}...")
    driver = None
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            driver.verify_connectivity()
            print("✅ Connected to Neo4j successfully!")
            break
        except Exception as e:
            if driver is not None:
                driver.close()
                driver = None
            if attempt == max_attempts:
                print(f"❌ Failed to connect to Neo4j after {max_attempts} attempts: {e}")
                return
            # The host machine is often CPU-starved by concurrent LLM inference at the
            # moment this subprocess is spawned, which can push the driver's handshake
            # past Neo4j's 30s auth timeout on the first try. Back off and retry.
            wait = 2 ** attempt
            print(f"⚠️  Connect attempt {attempt}/{max_attempts} failed ({e}); retrying in {wait}s...")
            time.sleep(wait)

    # Remove the full database drop so background syncs don't cause sudden disconnects/blank screens
    print("🔄 Ensuring database graph is ready...")

    # Property indexes so MATCH (x:Label {prop: $v}) lookups (used throughout
    # the per-paper loop below, and by the touched-node-scoped relationship
    # queries in step 7) can use an index seek instead of a full label scan.
    # IF NOT EXISTS makes this a cheap no-op on every run after the first.
    try:
        with driver.session() as session:
            session.run("CREATE INDEX paper_name_idx IF NOT EXISTS FOR (p:Paper) ON (p.name)")
            session.run("CREATE INDEX concept_name_idx IF NOT EXISTS FOR (c:Concept) ON (c.name)")
            session.run("CREATE INDEX theorem_name_idx IF NOT EXISTS FOR (t:Theorem) ON (t.name)")
            session.run("CREATE INDEX algorithm_name_idx IF NOT EXISTS FOR (a:Algorithm) ON (a.name)")
            session.run("CREATE INDEX codesnippet_title_idx IF NOT EXISTS FOR (c:CodeSnippet) ON (c.title)")
    except Exception as e:
        print(f"  ⚠️ Could not ensure indexes exist (continuing without them): {e}")

    if not DB_PATH.exists():
        print(f"❌ Database does not exist: {DB_PATH}")
        driver.close()
        return
    conn = paper_store.connect(DB_PATH)

    # Fetch every already-synced Paper's hash in ONE query, so unchanged
    # papers can be skipped without redoing their (many-round-trip) Cypher
    # work. Without this, a full re-scan of a large corpus (thousands of
    # papers, each requiring several separate driver.session() calls below)
    # takes far longer than any reasonable sync-call timeout — measured at
    # >1 hour for ~2300 papers on this project's reference hardware, which
    # silently made every periodic sync a no-op past that corpus size. See
    # speculative-paper-proc's docs/FINDINGS.md for how this was found.
    #
    # Keyed by paper_hash (not paper_name, as the old file-tree-era version
    # did) — paper_hash is the DB's actual identity key, and two differently
    # named papers could otherwise collide in a name-keyed skip-set.
    already_synced = {}
    try:
        with driver.session() as session:
            for rec in session.run("MATCH (p:Paper) WHERE p.paper_hash IS NOT NULL RETURN p.paper_hash AS hash"):
                already_synced[rec["hash"]] = rec["hash"]
        print(f"⚡ {len(already_synced)} papers already synced (will skip unchanged ones)")
    except Exception as e:
        print(f"  ⚠️ Could not pre-fetch synced hashes, will re-sync everything: {e}")

    total_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    synced = 0
    touched_papers = set()
    touched_concepts = set()
    touched_algorithms = set()
    touched_codesnippets = set()
    for record in paper_store.iter_papers_for_sync(conn, since_hash_map=already_synced):
        paper_name = record.paper_name
        paper_hash = record.paper_hash

        synced += 1
        touched_papers.add(paper_name)
        print(f"\n📂 Processing paper: {paper_name}...")

        try:
            # Initialize default properties
            motivation = ""
            methodology = ""
            contributions = ""
            limitations = ""
            significance = ""
            extras = ""
            defs, algs, cpp_examples = [], [], []

            # 2. Parse Summary
            if record.summary_md:
                sum_sections = clean_markdown_headers(record.summary_md)
                motivation = sum_sections.get("Motivation & Problem Statement", "")
                methodology = sum_sections.get("Core Methodology", "")
                contributions = sum_sections.get("Key Contributions", "")
                limitations = sum_sections.get("Limitations & Failure Modes", "")
                significance = sum_sections.get("Significance", "")

            # 3. Parse Extras
            if record.extras_md:
                extras = record.extras_md.strip()

            # Create Paper Node
            with driver.session() as session:
                session.run("""
                    MERGE (p:Paper {name: $name})
                    SET p += {
                        pdf_path: $pdf_path,
                        page_count: $page_count,
                        chunk_strategy: $chunk_strategy,
                        processed_at: $processed_at,
                        paper_hash: $paper_hash,
                        motivation: $motivation,
                        methodology: $methodology,
                        contributions: $contributions,
                        limitations: $limitations,
                        significance: $significance,
                        extras: $extras
                    }
                """, {
                    "name": paper_name,
                    "pdf_path": record.pdf_path or "",
                    "page_count": record.page_count or 0,
                    "chunk_strategy": record.chunk_strategy or "",
                    "processed_at": record.processed_at or "",
                    "paper_hash": paper_hash,
                    "motivation": motivation,
                    "methodology": methodology,
                    "contributions": contributions,
                    "limitations": limitations,
                    "significance": significance,
                    "extras": extras
                })
                print(f"  📝 Created Paper node: {paper_name}")

            # 4. Parse Logic Definitions, Theorems, Algorithms
            if record.symbolic_logic_md:
                logic_content = record.symbolic_logic_md

                logic_json = extract_json_block(logic_content)
                if logic_json and ("concepts" in logic_json or "theorems" in logic_json or "algorithms" in logic_json):
                    defs = _sanitize_items(logic_json.get("concepts", []))
                    theorems = _sanitize_items(logic_json.get("theorems", []))
                    algs = _sanitize_items(logic_json.get("algorithms", []))
                else:
                    logic_sections = clean_markdown_headers(logic_content)
                    defs = parse_logic_definitions(logic_sections.get("1. Core Definitions & Notation", ""))
                    theorems = parse_logic_theorems(logic_sections.get("2. Key Theorems & Propositions", ""))
                    algs = parse_logic_algorithms(logic_sections.get("3. Algorithm Formalisation", ""))

                with driver.session() as session:
                    for d in defs:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (c:Concept {name: $name})
                            ON CREATE SET c.definition = $definition
                            CREATE (p)-[:DEFINES]->(c)
                        """, {"paper_name": paper_name, "name": _safe_str(d, "name"), "definition": _safe_str(d, "description") or _safe_str(d, "definition")})
                print(f"  🔣 Imported {len(defs)} Core Concepts")

                with driver.session() as session:
                    for t in theorems:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (t:Theorem {name: $name})
                            SET t.statement = $statement
                            MERGE (p)-[:PROPOSES]->(t)
                        """, {"paper_name": paper_name, "name": _safe_str(t, "name"), "statement": _safe_str(t, "statement")})
                print(f"  📐 Imported {len(theorems)} Theorems")

                with driver.session() as session:
                    for a in algs:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (alg:Algorithm {name: $name})
                            SET alg.pseudocode = $code, alg.invariant = $invariant
                            MERGE (p)-[:FORMALISES]->(alg)
                        """, {"paper_name": paper_name, "name": _safe_str(a, "name"), "code": _safe_str(a, "pseudocode"), "invariant": _safe_str(a, "invariant")})
                print(f"  🤖 Imported {len(algs)} Algorithms")

            # 5. Parse C++ Examples
            if record.cpp_examples_md:
                cpp_content = record.cpp_examples_md

                cpp_json = extract_json_block(cpp_content)
                if cpp_json and "examples" in cpp_json:
                    cpp_examples = _sanitize_items(cpp_json.get("examples", []))
                else:
                    cpp_examples = parse_cpp_examples(cpp_content)

                with driver.session() as session:
                    for c in cpp_examples:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (code:CodeSnippet {title: $title})
                            SET code.language = 'cpp', code.code = $code
                            MERGE (p)-[:PROVIDES_CODE]->(code)
                        """, {"paper_name": paper_name, "title": _safe_str(c, "name") or _safe_str(c, "title"), "code": _safe_str(c, "code")})
                print(f"  💻 Imported {len(cpp_examples)} C++ Examples")

            # 6. Parse Diagrams
            diagrams = paper_store.load_diagrams(conn, paper_hash)
            if diagrams:
                with driver.session() as session:
                    for d in diagrams:
                        # No leading slash: neo4j_viz/{index,webgl}.html render
                        # this as `<img src="/${props.svg_path}">`, prepending
                        # the slash themselves — a leading slash here would
                        # produce a protocol-relative "//diagram/..." URL.
                        svg_path = f"diagram/{paper_hash}/{d.idx}.svg"
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (dg:Diagram {title: $title})
                            SET dg.dot_src = $dot, dg.svg_path = $svg
                            MERGE (p)-[:HAS_DIAGRAM]->(dg)
                        """, {"paper_name": paper_name, "title": d.title, "dot": d.dot_src, "svg": svg_path})
                print(f"  📊 Imported {len(diagrams)} DOT/SVG Diagrams")

            # Fold this paper's parsed names into the running touched-sets used
            # to scope the relationship-inference queries below.
            c_names, a_names, code_titles = extract_touched_names(defs, algs, cpp_examples)
            touched_concepts |= c_names
            touched_algorithms |= a_names
            touched_codesnippets |= code_titles
        except Exception as e:
            print(f"  ⚠️  Error processing paper {paper_name!r} — skipping this paper, continuing batch: {e}")
            continue

    # 7. Post-import relationship heuristic creation:
    # Look for matching concepts in other papers' motivation/methodology texts
    # and link concepts related to each other based on keyword matching
    #
    # This is an unconditional full-graph cartesian product (Paper×Concept,
    # Concept×Concept, Algorithm×Concept, CodeSnippet×Concept — ~84M pair
    # comparisons at ~1400 papers/6300 concepts) with no index support for
    # CONTAINS-on-toLower(), so it costs ~170-190s server-side regardless of
    # corpus size delta. Running it every periodic sync (every 5 min) even
    # when nothing changed is what intermittently tripped the caller's 120s
    # subprocess timeout. Only run it when this pass actually synced new or
    # changed papers.
    if synced == 0:
        print(f"⚡ Skipped {total_papers - synced} unchanged paper(s) (matching paper_hash already in graph)")
        print("  ⏭️  No new/changed papers this cycle — skipping full-graph relationship rescan")
        print("🎉 Graph database load complete!")
        driver.close()
        return

    # Scoped to touched nodes rather than a full cartesian rescan: each
    # relationship type runs as two statements, one filtered by each side
    # (touched-side x ALL-of-other-label). This is equivalent in coverage to
    # the original full scan restricted to "at least one side changed this
    # cycle" — an edge can only newly become true if a property on one of its
    # endpoints changed, and untouched<->untouched pairs were already
    # resolved by a prior cycle. Cost is O(touched x all + all x touched)
    # instead of O(all x all).
    print("\n🔗 Generating concept inter-link relationships (scoped to touched nodes)...")
    touched_papers_list = list(touched_papers)
    touched_concepts_list = list(touched_concepts)
    touched_algorithms_list = list(touched_algorithms)
    touched_codesnippets_list = list(touched_codesnippets)

    with driver.session() as session:
        # MENTIONS: Paper -> Concept
        if touched_papers_list:
            session.run("""
                MATCH (p:Paper), (c:Concept)
                WHERE p.name IN $touched
                  AND NOT (p)-[:DEFINES]->(c)
                  AND (toLower(p.motivation) CONTAINS toLower(c.name)
                       OR toLower(p.methodology) CONTAINS toLower(c.name))
                MERGE (p)-[:MENTIONS]->(c)
            """, {"touched": touched_papers_list})
        if touched_concepts_list:
            session.run("""
                MATCH (p:Paper), (c:Concept)
                WHERE c.name IN $touched
                  AND NOT (p)-[:DEFINES]->(c)
                  AND (toLower(p.motivation) CONTAINS toLower(c.name)
                       OR toLower(p.methodology) CONTAINS toLower(c.name))
                MERGE (p)-[:MENTIONS]->(c)
            """, {"touched": touched_concepts_list})

        # REFERS_TO: Concept -> Concept, based on definitions referring to each other
        if touched_concepts_list:
            session.run("""
                MATCH (c1:Concept), (c2:Concept)
                WHERE c1.name IN $touched
                  AND c1 <> c2
                  AND toLower(c1.definition) CONTAINS toLower(c2.name)
                MERGE (c1)-[:REFERS_TO]->(c2)
            """, {"touched": touched_concepts_list})
            session.run("""
                MATCH (c1:Concept), (c2:Concept)
                WHERE c2.name IN $touched
                  AND c1 <> c2
                  AND toLower(c1.definition) CONTAINS toLower(c2.name)
                MERGE (c1)-[:REFERS_TO]->(c2)
            """, {"touched": touched_concepts_list})

        # IMPLEMENTS: Algorithm -> Concept, if their names match
        if touched_algorithms_list:
            session.run("""
                MATCH (a:Algorithm), (c:Concept)
                WHERE a.name IN $touched
                  AND (toLower(a.name) CONTAINS toLower(c.name)
                       OR toLower(c.name) CONTAINS toLower(a.name))
                MERGE (a)-[:IMPLEMENTS]->(c)
            """, {"touched": touched_algorithms_list})
        if touched_concepts_list:
            session.run("""
                MATCH (a:Algorithm), (c:Concept)
                WHERE c.name IN $touched
                  AND (toLower(a.name) CONTAINS toLower(c.name)
                       OR toLower(c.name) CONTAINS toLower(a.name))
                MERGE (a)-[:IMPLEMENTS]->(c)
            """, {"touched": touched_concepts_list})

        # IMPLEMENTS: CodeSnippet -> Concept, if title contains concept name
        if touched_codesnippets_list:
            session.run("""
                MATCH (code:CodeSnippet), (c:Concept)
                WHERE code.title IN $touched
                  AND toLower(code.title) CONTAINS toLower(c.name)
                MERGE (code)-[:IMPLEMENTS]->(c)
            """, {"touched": touched_codesnippets_list})
        if touched_concepts_list:
            session.run("""
                MATCH (code:CodeSnippet), (c:Concept)
                WHERE c.name IN $touched
                  AND toLower(code.title) CONTAINS toLower(c.name)
                MERGE (code)-[:IMPLEMENTS]->(c)
            """, {"touched": touched_concepts_list})

    print(f"⚡ Skipped {total_papers - synced} unchanged paper(s) (matching paper_hash already in graph)")
    print("🎉 Graph database load complete!")
    driver.close()

if __name__ == "__main__":
    main()
