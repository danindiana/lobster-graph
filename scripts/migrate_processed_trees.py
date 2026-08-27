#!/usr/bin/env python3
"""
migrate_processed_trees.py — one-off consolidation of every scattered
_processed/ file tree into the shared paper_store.py SQLite database.

Usage:
  python scripts/migrate_processed_trees.py                  # backup + migrate + verify (no deletion)
  python scripts/migrate_processed_trees.py --delete-originals  # also delete/git-rm originals,
                                                                  # only after a prior clean run

Source trees (from the 2026-08-27 disk-wide inventory — see the session doc
for the full accounting): tolaria-aiml-vault/{_processed,transformers,
nanobots,EBF}/_processed (git-tracked, markdown-only — no metadata.json, no
diagrams; reconciled by folder-slug against AI-ML_Papers below, not migrated
directly), Documents/AI-ML_Papers/{_processed,transformers,nanobots,EBF}/
_processed, Documents/computers/_processed, "Documents/computer science"/
_processed, Documents/_processed, and the repo's own
fork_2026-05-09T184929Z/_processed (an older format variant — no
.ocr_cache/, occasional leftover diagrams/_raw_llm_output.txt — same
metadata.json field set, tolerated by the same parser).

Excluded on purpose: pdf_downloader/.../pdfs/_processed (stub — sampled
folders have no metadata.json/no real output) and the /mnt/sdf1 OS backup
snapshot copies (already a backup of a past state, not live output).
"""
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_store

HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent

# (path, source_corpus label, has_real_metadata)
REAL_TREES: List[Tuple[Path, str, bool]] = [
    (HOME / "Documents/AI-ML_Papers/_processed", "AI-ML_Papers", True),
    (HOME / "Documents/AI-ML_Papers/transformers/_processed", "AI-ML_Papers/transformers", True),
    (HOME / "Documents/AI-ML_Papers/nanobots/_processed", "AI-ML_Papers/nanobots", True),
    (HOME / "Documents/AI-ML_Papers/EBF/_processed", "AI-ML_Papers/EBF", True),
    (HOME / "Documents/computers/_processed", "Documents/computers", True),
    (HOME / "Documents/computer science/_processed", "Documents/computer science", True),
    (HOME / "Documents/_processed", "Documents", True),
    (REPO_ROOT / "fork_2026-05-09T184929Z/_processed", "fork_2026-05-09T184929Z", True),
]

# tolaria-aiml-vault has no metadata.json/paper_hash of its own (markdown-only
# vault export) — reconciled against REAL_TREES by folder slug, never
# migrated as an independent source of paper_hash identity.
VAULT_TREES: List[Tuple[Path, str]] = [
    (HOME / "tolaria-aiml-vault/_processed", "tolaria-aiml-vault"),
    (HOME / "tolaria-aiml-vault/transformers/_processed", "tolaria-aiml-vault/transformers"),
    (HOME / "tolaria-aiml-vault/nanobots/_processed", "tolaria-aiml-vault/nanobots"),
    (HOME / "tolaria-aiml-vault/EBF/_processed", "tolaria-aiml-vault/EBF"),
]

BACKUP_DIR = Path("/mnt/nvme_staging/paper_processor_data/backups")

SECTION_FILES = {
    "summary": "01_summary.md",
    "logic": "02_symbolic_logic.md",
    "cpp": "03_cpp_examples.md",
    "extras": "04_extras.md",
}


def backup_trees() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    archive = BACKUP_DIR / f"processed_trees_backup_{ts}.tar.zst"
    paths = [str(p) for p, _, _ in REAL_TREES if p.exists()] + [
        str(p) for p, _ in VAULT_TREES if p.exists()
    ]
    print(f"📦 Backing up {len(paths)} trees → {archive}")
    tar = subprocess.Popen(["tar", "-cf", "-"] + paths, stdout=subprocess.PIPE)
    with open(archive, "wb") as f:
        zstd = subprocess.Popen(["zstd", "-q", "-T0"], stdin=tar.stdout, stdout=f)
        tar.stdout.close()
        zstd.communicate()
    tar.wait()
    if tar.returncode != 0 or zstd.returncode != 0:
        sys.exit(f"❌ Backup failed (tar={tar.returncode}, zstd={zstd.returncode})")
    size_mb = archive.stat().st_size / 1_000_000
    print(f"✅ Backup complete: {archive} ({size_mb:.1f} MB)")
    return archive


def migrate_real_tree(
    conn, tree_path: Path, source_corpus: str, conflicts: List[str]
) -> Dict[str, str]:
    slug_to_hash: Dict[str, str] = {}
    if not tree_path.exists():
        return slug_to_hash

    for meta_file in sorted(tree_path.rglob("metadata.json")):
        paper_dir = meta_file.parent
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ⚠️  Unreadable metadata.json at {meta_file}: {exc}")
            continue

        paper_hash = meta.get("paper_hash")
        if not paper_hash:
            print(f"  ⚠️  Missing paper_hash in {meta_file}, skipping")
            continue
        slug_to_hash[paper_dir.name] = paper_hash

        incoming_processed_at = meta.get("processed_at", "")
        existing = paper_store.load_paper(conn, paper_hash)
        # Always respect timestamp ordering, even within the same
        # source_corpus label — a single tree can itself contain multiple
        # stale duplicate copies of the same paper under different category
        # subfolders (reorganized over time), and rglob's alphabetical walk
        # order has no relationship to which copy was actually processed
        # more recently.
        if (
            existing
            and existing.processed_at
            and incoming_processed_at
            and existing.processed_at > incoming_processed_at
        ):
            if existing.source_corpus != source_corpus:
                conflicts.append(
                    f"{paper_hash}: kept {existing.source_corpus} "
                    f"({existing.processed_at}) over {source_corpus} ({incoming_processed_at})"
                )
            continue
        if existing and existing.source_corpus and existing.source_corpus != source_corpus:
            conflicts.append(
                f"{paper_hash}: {source_corpus} ({incoming_processed_at}) "
                f"overwrote {existing.source_corpus} ({existing.processed_at})"
            )

        paper_store.upsert_paper_meta(
            conn,
            paper_hash=paper_hash,
            paper_name=meta.get("paper_name", paper_dir.name),
            pdf_path=meta.get("pdf_path", ""),
            page_count=meta.get("page_count", 0),
            chunk_strategy=meta.get("chunk_strategy", ""),
            model_used=meta.get("model_used", ""),
            code_model=meta.get("code_model", ""),
            source_corpus=source_corpus,
            processed_at=incoming_processed_at or None,
        )

        for section, fname in SECTION_FILES.items():
            fpath = paper_dir / fname
            if fpath.exists():
                paper_store.write_section(
                    conn, paper_hash, section, fpath.read_text(encoding="utf-8")
                )

        diagrams_dir = paper_dir / "diagrams"
        if diagrams_dir.exists():
            rows: List[Tuple[str, str, Optional[str]]] = []
            for dot_file in sorted(diagrams_dir.glob("*.dot")):
                title = dot_file.stem[3:].replace("_", " ").title()
                dot_src = dot_file.read_text(encoding="utf-8")
                svg_file = dot_file.with_suffix(".svg")
                svg_content = svg_file.read_text(encoding="utf-8") if svg_file.exists() else None
                rows.append((title, dot_src, svg_content))
            if rows:
                paper_store.replace_diagrams(conn, paper_hash, rows)
            raw_out = diagrams_dir / "_raw_llm_output.txt"
            if raw_out.exists():
                paper_store.write_diagrams_raw_output(
                    conn, paper_hash, raw_out.read_text(encoding="utf-8")
                )

        # Faithfully reproduce the original sections_completed regardless of
        # which physical files happen to exist now.
        for section in meta.get("sections_completed", []):
            paper_store.mark_section_complete(conn, paper_hash, section)

    return slug_to_hash


def reconcile_vault_tree(
    tree_path: Path, source_corpus: str, slug_to_hash: Dict[str, str]
) -> Tuple[int, List[str]]:
    """tolaria-aiml-vault has no metadata.json of its own — every leaf folder
    is checked against the hash map built from the real trees. A slug match
    means the content is already migrated (with full metadata+diagrams, which
    the vault export never had); an unmatched slug has no PDF/hash anywhere
    and can't be given a paper_hash identity, so it's flagged, not migrated.
    """
    matched = 0
    orphans: List[str] = []
    if not tree_path.exists():
        return matched, orphans

    for summary_file in sorted(tree_path.rglob("01_summary.md")):
        slug = summary_file.parent.name
        if slug in slug_to_hash:
            matched += 1
        else:
            orphans.append(f"{source_corpus}/{slug}")
    return matched, orphans


def main():
    delete_originals = "--delete-originals" in sys.argv

    db_path = paper_store.resolve_db_path()
    conn = paper_store.connect(db_path)
    print(f"🗄️   Target database: {db_path}")

    backup_trees()

    print("\n📥 Migrating real (metadata.json-bearing) trees...")
    all_slug_to_hash: Dict[str, str] = {}
    conflicts: List[str] = []
    per_tree_counts: Dict[str, int] = {}
    for tree_path, source_corpus, _ in REAL_TREES:
        before = len(all_slug_to_hash)
        slug_map = migrate_real_tree(conn, tree_path, source_corpus, conflicts)
        all_slug_to_hash.update(slug_map)
        per_tree_counts[source_corpus] = len(slug_map)
        print(f"  {source_corpus}: {len(slug_map)} papers")

    if conflicts:
        print(f"\n⚠️  {len(conflicts)} cross-tree conflict(s) resolved by processed_at:")
        for c in conflicts[:50]:
            print(f"    - {c}")
        if len(conflicts) > 50:
            print(f"    ... and {len(conflicts) - 50} more")

    print("\n🔎 Reconciling tolaria-aiml-vault (markdown-only, no metadata.json)...")
    total_matched = 0
    all_orphans: List[str] = []
    for tree_path, source_corpus in VAULT_TREES:
        matched, orphans = reconcile_vault_tree(tree_path, source_corpus, all_slug_to_hash)
        total_matched += matched
        all_orphans.extend(orphans)
        print(f"  {source_corpus}: {matched} matched, {len(orphans)} orphan(s)")

    if all_orphans:
        print(f"\n⚠️  {len(all_orphans)} vault entries have no matching source elsewhere "
              f"(no PDF/hash available — NOT migrated, needs manual review):")
        for o in all_orphans:
            print(f"    - {o}")

    row_count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    print(f"\n📊 papers table now has {row_count} rows")
    for row in conn.execute(
        "SELECT source_corpus, COUNT(*) FROM papers GROUP BY source_corpus ORDER BY source_corpus"
    ):
        print(f"    {row[0] or '(none)'}: {row[1]}")

    print(
        "\n✅ Migration pass complete. Originals have NOT been deleted "
        "(re-run with --delete-originals after verifying the above)."
        if not delete_originals
        else "\n⚠️  --delete-originals was passed but deletion is not yet implemented "
        "in this script — verify the migration first, then delete manually per the "
        "session doc's plan (plain rm -rf for untracked trees, git rm + commit for "
        "tolaria-aiml-vault, gated behind its own explicit confirmation)."
    )


if __name__ == "__main__":
    main()
