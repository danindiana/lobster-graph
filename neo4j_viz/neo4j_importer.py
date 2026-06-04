#!/usr/bin/env python3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# neo4j_importer.py
# Parses processed paper outputs and loads them into Neo4j graph database.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import re
import json
from pathlib import Path
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")
PROCESSED_DIR = Path("/mnt/raid0/monolithic_pdf_folderv3/illoinois_edu/_processed")

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
    # Split text by - **Algorithm Name**:
    pattern = r"-\s*\*\*([^*]+)\*\*:\s*"
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

def main():
    print(f"🔗 Connecting to Neo4j at {NEO4J_URI}...")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        # Verify connection
        driver.verify_connectivity()
        print("✅ Connected to Neo4j successfully!")
    except Exception as e:
        print(f"❌ Failed to connect to Neo4j: {e}")
        return

    # Clear database
    print("🧹 Cleaning existing database graph...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

    if not PROCESSED_DIR.exists():
        print(f"❌ Processed directory does not exist: {PROCESSED_DIR}")
        driver.close()
        return

    # Walk through each processed subfolder
    for path in PROCESSED_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("_"):
            continue
            
        meta_file = path / "metadata.json"
        if not meta_file.exists():
            continue
            
        print(f"\n📂 Processing paper folder: {path.name}...")
        
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
        
        # Initialize default properties
        motivation = ""
        methodology = ""
        contributions = ""
        limitations = ""
        significance = ""
        extras = ""
        
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
                CREATE (p:Paper {
                    name: $name,
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
                })
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
            logic_sections = clean_markdown_headers(logic_content)
            
            # Extract concepts (definitions)
            if "1. Core Definitions & Notation" in logic_sections:
                defs = parse_logic_definitions(logic_sections["1. Core Definitions & Notation"])
                with driver.session() as session:
                    for d in defs:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            MERGE (c:Concept {name: $name})
                            ON CREATE SET c.definition = $definition
                            CREATE (p)-[:DEFINES]->(c)
                        """, {"paper_name": paper_name, "name": d["name"], "definition": d["definition"]})
                print(f"  🔣 Imported {len(defs)} Core Concepts")

            # Extract theorems
            if "2. Key Theorems & Propositions" in logic_sections:
                theorems = parse_logic_theorems(logic_sections["2. Key Theorems & Propositions"])
                with driver.session() as session:
                    for t in theorems:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            CREATE (t:Theorem {name: $name, statement: $statement})
                            CREATE (p)-[:PROPOSES]->(t)
                        """, {"paper_name": paper_name, "name": t["name"], "statement": t["statement"]})
                print(f"  📐 Imported {len(theorems)} Theorems")

            # Extract algorithms
            if "3. Algorithm Formalisation" in logic_sections:
                algs = parse_logic_algorithms(logic_sections["3. Algorithm Formalisation"])
                with driver.session() as session:
                    for a in algs:
                        session.run("""
                            MATCH (p:Paper {name: $paper_name})
                            CREATE (alg:Algorithm {name: $name, pseudocode: $code, invariant: $invariant})
                            CREATE (p)-[:FORMALISES]->(alg)
                        """, {"paper_name": paper_name, "name": a["name"], "code": a["pseudocode"], "invariant": a["invariant"]})
                print(f"  🤖 Imported {len(algs)} Algorithms")

        # 5. Parse C++ Examples
        cpp_file = path / "03_cpp_examples.md"
        if cpp_file.exists():
            cpp_content = cpp_file.read_text(encoding="utf-8")
            cpp_examples = parse_cpp_examples(cpp_content)
            with driver.session() as session:
                for c in cpp_examples:
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        CREATE (code:CodeSnippet {title: $title, language: 'cpp', code: $code})
                        CREATE (p)-[:PROVIDES_CODE]->(code)
                    """, {"paper_name": paper_name, "title": c["title"], "code": c["code"]})
            print(f"  💻 Imported {len(cpp_examples)} C++ Examples")

        # 6. Parse Diagrams (.dot files)
        diagrams_dir = path / "diagrams"
        if diagrams_dir.exists():
            dot_files = list(diagrams_dir.glob("*.dot"))
            with driver.session() as session:
                for dot_file in dot_files:
                    title = dot_file.stem[3:].replace("_", " ").title() # strip idx (e.g. 01_)
                    dot_src = dot_file.read_text(encoding="utf-8")
                    
                    # Relativize SVG path for static hosting serving
                    rel_svg = f"_processed/{path.name}/diagrams/{dot_file.stem}.svg"
                    
                    session.run("""
                        MATCH (p:Paper {name: $paper_name})
                        CREATE (d:Diagram {title: $title, dot_src: $dot, svg_path: $svg})
                        CREATE (p)-[:HAS_DIAGRAM]->(d)
                    """, {"paper_name": paper_name, "title": title, "dot": dot_src, "svg": rel_svg})
            print(f"  📊 Imported {len(dot_files)} DOT/SVG Diagrams")

    # 7. Post-import relationship heuristic creation:
    # Look for matching concepts in other papers' motivation/methodology texts
    # and link concepts related to each other based on keyword matching
    print("\n🔗 Generating concept inter-link relationships...")
    with driver.session() as session:
        # Link papers to concepts that are defined elsewhere but mentioned in their motivation/methodology
        session.run("""
            MATCH (p:Paper), (c:Concept)
            WHERE NOT (p)-[:DEFINES]->(c) 
              AND (toLower(p.motivation) CONTAINS toLower(c.name) 
                   OR toLower(p.methodology) CONTAINS toLower(c.name))
            MERGE (p)-[:MENTIONS]->(c)
        """)
        
        # Concept-to-Concept relationships based on definitions referring to each other
        session.run("""
            MATCH (c1:Concept), (c2:Concept)
            WHERE c1 <> c2 
              AND toLower(c1.definition) CONTAINS toLower(c2.name)
            MERGE (c1)-[:REFERS_TO]->(c2)
        """)
        
        # Link Algorithms and Code snippets to Concepts if their names match
        session.run("""
            MATCH (a:Algorithm), (c:Concept)
            WHERE toLower(a.name) CONTAINS toLower(c.name) 
               OR toLower(c.name) CONTAINS toLower(a.name)
            MERGE (a)-[:IMPLEMENTS]->(c)
        """)
        session.run("""
            MATCH (code:CodeSnippet), (c:Concept)
            WHERE toLower(code.title) CONTAINS toLower(c.name)
            MERGE (code)-[:IMPLEMENTS]->(c)
        """)

    print("🎉 Graph database load complete!")
    driver.close()

if __name__ == "__main__":
    main()
