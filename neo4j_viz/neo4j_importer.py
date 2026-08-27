#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# neo4j_importer.py
# Parses processed paper outputs and loads them into Neo4j graph database.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import re
import json
import time
from pathlib import Path
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")
import sys
PROCESSED_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/mnt/raid0/monolithic_pdf_folderv3/illoinois_edu/_processed")

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

def extract_touched_names(defs: list, algs: list, cpp_examples: list) -> tuple:
    """Given the parsed def/algorithm/code-example lists for ONE synced paper,
    return (Concept names, Algorithm names, CodeSnippet titles) touched this
    cycle — the same identity keys used as MERGE match keys in the per-paper
    loop below. Used to scope the relationship-inference queries to just what
    changed instead of a full cartesian graph scan.
    """
    concept_names = {d.get("name", "") for d in defs} - {""}
    algorithm_names = {a.get("name", "") for a in algs} - {""}
    codesnippet_titles = {c.get("name", c.get("title", "")) for c in cpp_examples} - {""}
    return concept_names, algorithm_names, codesnippet_titles

def main():
    import sys
    global PROCESSED_DIR
    if len(sys.argv) > 1:
        PROCESSED_DIR = Path(sys.argv[1])
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

    if not PROCESSED_DIR.exists():
        print(f"❌ Processed directory does not exist: {PROCESSED_DIR}")
        driver.close()
        return

    # Fetch every already-synced Paper's hash in ONE query, so unchanged
    # papers can be skipped without redoing their (many-round-trip) Cypher
    # work. Without this, a full re-scan of a large corpus (thousands of
    # papers, each requiring several separate driver.session() calls below)
    # takes far longer than any reasonable sync-call timeout — measured at
    # >1 hour for ~2300 papers on this project's reference hardware, which
    # silently made every periodic sync a no-op past that corpus size. See
    # speculative-paper-proc's docs/FINDINGS.md for how this was found.
    already_synced = {}
    try:
        with driver.session() as session:
            for rec in session.run("MATCH (p:Paper) WHERE p.paper_hash IS NOT NULL RETURN p.name AS name, p.paper_hash AS hash"):
                already_synced[rec["name"]] = rec["hash"]
        print(f"⚡ {len(already_synced)} papers already synced (will skip unchanged ones)")
    except Exception as e:
        print(f"  ⚠️ Could not pre-fetch synced hashes, will re-sync everything: {e}")

    skipped = 0
    synced = 0
    touched_papers = set()
    touched_concepts = set()
    touched_algorithms = set()
    touched_codesnippets = set()
    # Walk through each processed paper folder, at any depth. Newer processing
    # runs (paper_proc_smrtevict.py) mirror the source PDF tree under
    # _processed/ (e.g. _processed/<source-subfolder>/<paper>/metadata.json)
    # so a paper's own folder is not always an immediate child of
    # PROCESSED_DIR — rglob so nested corpora are found, not just flat ones.
    for meta_file in sorted(PROCESSED_DIR.rglob("metadata.json")):
        path = meta_file.parent
        if any(part.startswith("_") for part in path.relative_to(PROCESSED_DIR).parts):
            continue

        # 1. Metadata
        try:
            with open(meta_file, "r") as f:
                meta = json.load(f)
        except Exception as e:
            print(f"  ⚠️ Error reading metadata: {e}")
            continue

        paper_name = meta.get("paper_name", path.name)
        pdf_path = meta.get("pdf_path", "")
        page_count = meta.get("page_count", 0)
        chunk_strategy = meta.get("chunk_strategy", "")
        processed_at = meta.get("processed_at", "")
        paper_hash = meta.get("paper_hash", "")

        if paper_hash and already_synced.get(paper_name) == paper_hash:
            skipped += 1
            continue

        synced += 1
        touched_papers.add(paper_name)
        print(f"\n📂 Processing paper folder: {path.name}...")

        # Initialize default properties
        motivation = ""
        methodology = ""
        contributions = ""
        limitations = ""
        significance = ""
        extras = ""
        defs, algs, cpp_examples = [], [], []

        # 2. Parse Summary
        summary_file = path / "01_summary.md"
        if summary_file.exists():
            content = summary_file.read_text(encoding="utf-8")
            sum_sections = clean_markdown_headers(content)
            motivation = sum_sections.get("Motivation & Problem Statement", "")
            methodology = sum_sections.get("Core Methodology", "")
            contributions = sum_sections.get("Key Contributions", "")
            limitations = sum_sections.get("Limitations & Failure Modes", "")
            significance = sum_sections.get("Significance", "")

        # 3. Parse Extras
        extras_file = path / "04_extras.md"
        if extras_file.exists():
            extras = extras_file.read_text(encoding="utf-8").strip()

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
                "pdf_path": pdf_path,
                "page_count": page_count,
                "chunk_strategy": chunk_strategy,
                "processed_at": processed_at,
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
        logic_file = path / "02_symbolic_logic.md"
        if logic_file.exists():
            logic_content = logic_file.read_text(encoding="utf-8")
            
            logic_json = extract_json_block(logic_content)
            if logic_json and ("concepts" in logic_json or "theorems" in logic_json or "algorithms" in logic_json):
                defs = logic_json.get("concepts", [])
                theorems = logic_json.get("theorems", [])
                algs = logic_json.get("algorithms", [])
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
                    """, {"paper_name": paper_name, "name": d.get("name", ""), "definition": d.get("description", d.get("definition", ""))})
            print(f"  🔣 Imported {len(defs)} Core Concepts")

            with driver.session() as session:
                for t in theorems:
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        MERGE (t:Theorem {name: $name})
                        SET t.statement = $statement
                        MERGE (p)-[:PROPOSES]->(t)
                    """, {"paper_name": paper_name, "name": t.get("name", ""), "statement": t.get("statement", "")})
            print(f"  📐 Imported {len(theorems)} Theorems")

            with driver.session() as session:
                for a in algs:
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        MERGE (alg:Algorithm {name: $name})
                        SET alg.pseudocode = $code, alg.invariant = $invariant
                        MERGE (p)-[:FORMALISES]->(alg)
                    """, {"paper_name": paper_name, "name": a.get("name", ""), "code": a.get("pseudocode", ""), "invariant": a.get("invariant", "")})
            print(f"  🤖 Imported {len(algs)} Algorithms")

        # 5. Parse C++ Examples
        cpp_file = path / "03_cpp_examples.md"
        if cpp_file.exists():
            cpp_content = cpp_file.read_text(encoding="utf-8")
            
            cpp_json = extract_json_block(cpp_content)
            if cpp_json and "examples" in cpp_json:
                cpp_examples = cpp_json.get("examples", [])
            else:
                cpp_examples = parse_cpp_examples(cpp_content)
                
            with driver.session() as session:
                for c in cpp_examples:
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        MERGE (code:CodeSnippet {title: $title})
                        SET code.language = 'cpp', code.code = $code
                        MERGE (p)-[:PROVIDES_CODE]->(code)
                    """, {"paper_name": paper_name, "title": c.get("name", c.get("title", "")), "code": c.get("code", "")})
            print(f"  💻 Imported {len(cpp_examples)} C++ Examples")

        # 6. Parse Diagrams (.dot files)
        diagrams_dir = path / "diagrams"
        if diagrams_dir.exists():
            dot_files = list(diagrams_dir.glob("*.dot"))
            with driver.session() as session:
                for dot_file in dot_files:
                    title = dot_file.stem[3:].replace("_", " ").title() # strip idx (e.g. 01_)
                    dot_src = dot_file.read_text(encoding="utf-8")
                    
                    # Relativize SVG path for static hosting serving. Uses the
                    # full path relative to PROCESSED_DIR (not just path.name)
                    # so nested corpora (_processed/<subfolder>/<paper>/...)
                    # resolve to the real on-disk location, not a truncated one.
                    rel_paper_path = path.relative_to(PROCESSED_DIR).as_posix()
                    rel_svg = f"_processed/{rel_paper_path}/diagrams/{dot_file.stem}.svg"
                    
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        MERGE (d:Diagram {title: $title})
                        SET d.dot_src = $dot, d.svg_path = $svg
                        MERGE (p)-[:HAS_DIAGRAM]->(d)
                    """, {"paper_name": paper_name, "title": title, "dot": dot_src, "svg": rel_svg})
            print(f"  📊 Imported {len(dot_files)} DOT/SVG Diagrams")

        # Fold this paper's parsed names into the running touched-sets used
        # to scope the relationship-inference queries below.
        c_names, a_names, code_titles = extract_touched_names(defs, algs, cpp_examples)
        touched_concepts |= c_names
        touched_algorithms |= a_names
        touched_codesnippets |= code_titles

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
        print(f"⚡ Skipped {skipped} unchanged paper(s) (matching paper_hash already in graph)")
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

    print(f"⚡ Skipped {skipped} unchanged paper(s) (matching paper_hash already in graph)")
    print("🎉 Graph database load complete!")
    driver.close()

if __name__ == "__main__":
    main()
