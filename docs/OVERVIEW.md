# Lobster Graph — A Plain-Language Overview

*For researchers and technical readers deciding whether this tool is worth setting up. No prior knowledge of the project assumed. If you want ports, model tiers, and VRAM math, the [README](../README.md) has all of that — this document is about **what you get** and **why you might want it**.*

---

## The one-paragraph version

You have a folder full of AI/ML research papers as PDFs — the pile that grows faster than you can read it. Lobster Graph reads each paper with a local large language model (the same kind of AI behind chat assistants, but running entirely on your own machine) and turns it into a structured **study dossier**: a thorough summary, a formal-logic restatement of the core ideas, runnable C++ implementations of the key algorithms, a set of diagrams, and a critical analysis. It then stitches every paper into a **knowledge graph** so you can see how concepts, theorems, and methods connect *across* your whole library — not just within one paper at a time. Nothing leaves your computer.

---

## The problem it's built for

Reading a research paper well is slow, and reading a hundred of them is a part-time job. The usual coping strategies all have costs:

- **Skimming** is fast but lossy — you miss the assumptions buried in section 4 that quietly undermine the headline result.
- **Cloud AI summarizers** are convenient but mean uploading unpublished work, paywalled PDFs, or sensitive material to someone else's servers — often a non-starter for institutional or pre-publication work.
- **Reference managers** (Zotero, Mendeley, and friends) organize *metadata* — authors, years, tags — but they don't read the papers. They can't tell you that the loss function in paper A is a special case of the objective in paper C.

Lobster Graph sits in the gap: it does the *deep-reading* work, locally, and then makes the results **connectable**.

Think of it less as a summarizer and more as a **tireless research assistant** who reads every paper the same way, takes the same six kinds of notes every time, and — crucially — keeps a giant wall-chart of red string connecting every idea to every related idea across the whole corpus.

---

## What you actually get, per paper

For every PDF, the pipeline produces a small folder of artifacts:

| File | What it is | Why it's useful |
| --- | --- | --- |
| `01_summary.md` | A structured summary covering motivation, methodology, contributions, experimental results, **limitations**, and significance. | The "do I need to read the full thing?" triage document — and it's specifically prompted to surface failure modes, not just sell the paper. |
| `02_symbolic_logic.md` | The paper's core claims restated in formal notation — definitions, theorems, algorithm pseudocode with complexity bounds, convergence conditions. | Strips marketing language down to the actual mathematical claims. Useful for spotting whether two papers are really making the same claim in different clothes. |
| `03_cpp_examples.md` | Modern C++ implementations of the key algorithms, with comments mapping math → code. | Turns "I think I understand the method" into "here's code that runs." A sanity check on whether the paper is actually specified well enough to implement. |
| `diagrams/` | Six Graphviz diagrams per paper — architecture, data flow, the core algorithm as a flowchart, a concept hierarchy, the training loop, and a comparison to prior work. | Six different angles on one paper. The flowchart view in particular often makes a dense method legible in a way the prose doesn't. |
| `04_extras.md` | Critical analysis: open questions, connections to other work, deployment tradeoffs, a steelman, and the strongest critique. | This is the "what would a sharp colleague say in the reading group?" layer. |
| `metadata.json` | An audit trail: which model was used, the file hash, timestamps, which sections finished. | Reproducibility, and the basis for resume-on-interruption (see below). |

Two honest caveats about these artifacts. First, they're **LLM-generated**, so they are a high-quality *starting point*, not a citable source — treat them like notes from a very well-read assistant who occasionally misremembers. Second, the C++ examples are illustrative; they compile-and-run as learning scaffolds, not as the paper's official reference implementation.

---

## The part that's genuinely different: the graph

Most paper-summarizing tools treat each paper as an island. Lobster Graph's distinguishing move is to load every dossier into a **Neo4j graph database**, where papers and their contents become nodes connected by typed relationships:

- A **Paper** `DEFINES` **Concepts**, `PROPOSES` **Theorems**, `FORMALISES` **Algorithms**, `PROVIDES_CODE` (**CodeSnippets**), and `HAS_DIAGRAM`s.
- Across papers, a paper that uses a concept defined elsewhere gets a `MENTIONS` link to it; concepts whose definitions reference each other get `REFERS_TO` links; algorithms and code that match a concept's name get `IMPLEMENTS` links.

The analogy: reading papers one at a time gives you a **stack of index cards**. The graph turns that stack into a **subway map** — you can suddenly see which concepts are the major interchange stations that everything routes through, and which papers are end-of-the-line stops that nothing else connects to.

That structure unlocks questions you can't easily ask a folder of PDFs:

- *Which concept appears in the most papers?* (Your field's load-bearing ideas.)
- *What links these two papers I didn't think were related?*
- *Which of my papers are isolated* — using vocabulary nothing else in my library touches?

A browser-based dashboard ships with the project for exploring this visually — drag papers around, focus on one node's neighborhood, size nodes by how connected they are, and export the whole graph as a portable file. It runs as a local web page; like everything else, no data leaves your machine.

**One candid limitation worth knowing up front:** the cross-paper links are built by *text matching* — if two papers both contain the word "attention," they get linked. That's powerful for discovery but noisy: a concept named with a common word will over-connect, and a concept phrased two different ways won't connect at all. Treat the graph as a **map of leads to investigate**, not a citation network of verified relationships. (The maintainers are aware of this and it's on the roadmap to improve with meaning-based matching.)

---

## What it assumes about your setup

This is the most important section for an evaluator, because it's where the project is opinionated. Lobster Graph is **not** a cloud service you sign up for or a lightweight script that runs on a laptop. It assumes:

- **Linux** (Ubuntu 22.04+ is the tested target).
- **A capable NVIDIA GPU — ideally two.** The whole headline "Zero-Swap Concurrency" feature exists to keep two large AI models loaded on two separate GPUs at once, so the pipeline never pauses to swap models in and out. The reference setup is a 16 GB + 10 GB dual-GPU rig. You can run it on a single smaller GPU using the lighter model tiers, but you'll feel the model-swap delays the dual-GPU mode is designed to eliminate.
- **[Ollama](https://ollama.com)** installed locally to actually run the models, plus a few system tools (`graphviz` for diagrams, `tesseract-ocr` for scanned PDFs).
- **Comfort with the command line.** There's an interactive setup wizard and a terminal dashboard to smooth this out, but this is a developer-facing tool, not a desktop app.

The plain truth: if you have the hardware, this is a powerful local research accelerator. If you don't have a decent GPU, the local-first design that makes it private also makes it impractical — that's the fundamental tradeoff, and there's no way around it without sending your papers to the cloud, which is exactly what the project refuses to do.

---

## Touches that signal it's built for real use

A few design choices matter more in practice than they sound on paper:

- **It resumes where it left off.** Processing a large corpus takes time. If you interrupt it (or it crashes), it checkpoints after each section of each paper and picks up exactly where it stopped — it won't redo finished work.
- **It handles scanned PDFs.** Older or photographed papers have no embedded text. The pipeline detects these and runs local OCR to read them anyway, instead of silently producing an empty summary.
- **It's honest about its own brittleness.** The codebase has a test suite that explicitly documents where its parsers are fragile — which is a good sign in a tool you're trusting with your reading.
- **The graph syncs as it works.** You don't have to wait for the whole corpus to finish; the dashboard fills in as papers complete.

---

## Is it for you? A quick self-check

**Good fit if** you have a backlog of AI/ML (or similar technical) papers, you care about *privacy or work with unpublished/sensitive material*, you have a reasonable NVIDIA GPU, and you want to find structure *across* your library rather than just summaries of individual papers.

**Probably not your tool if** you need polished, citable summaries you can quote directly (these are AI drafts), you don't have GPU hardware, you're on Windows/macOS without a Linux environment, or you only ever read one paper at a time (the graph — the best part — only pays off at corpus scale).

---

## Where to go next

- **To set it up:** the [README](../README.md) has prerequisites, the model-selection details, and the quickstart.
- **To understand the internals:** see [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
- **If something breaks:** [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

*Everything here runs on your own hardware. No accounts, no API keys, no data leaving your machine — that constraint is the whole point of the project, and it shapes every tradeoff above.*
