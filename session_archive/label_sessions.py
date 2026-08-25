#!/usr/bin/env python3
"""
Interactive relevance-labeling loop for the session archive.

Ported from militia-classifier's militia-rlhf/labeler.py, collapsed to a
single modality (text chunks, bge-m3 vectors already stored in the FAISS
index) and simplified: no separate embedding cache (session_archive already
has one FAISS index + id_map.json covering every chunk) and no centroid-score
pre-sort (session_archive has no fixed target class to compute a centroid
against — see the "Phase 2" design note in the RAG consolidation plan).
Candidates are simply unlabeled-first before any probe exists, then sorted by
|probe_prob - 0.5| (most uncertain first) once one has been trained.

y = relevant   n = not relevant   s = skip   t = retrain now   q = quit + save
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from relevance_probe import MLPProbe

DEFAULT_INDEX_DIR = Path("/mnt/nvme_staging/session_archive_index")
RETRAIN_EVERY = 20


def _getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1).lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _key(entry: dict) -> str:
    return f"{entry['path']}::{entry['chunk_idx']}"


class InteractiveLabeler:
    def __init__(self, index: faiss.Index, id_map: list[dict], probe: MLPProbe,
                 labels_path: Path, probe_path: Path, console: Console) -> None:
        self._index = index
        self._id_map = id_map
        self._key_to_row = {_key(e): i for i, e in enumerate(id_map)}
        self._probe = probe
        self._labels_path = labels_path
        self._probe_path = probe_path
        self._console = console
        self._new_since_retrain = 0

        self._labels: list[dict] = []
        self._labeled_keys: set[str] = set()
        if labels_path.exists():
            self._labels = json.loads(labels_path.read_text())
            self._labeled_keys = {r["key"] for r in self._labels}

    def run(self) -> None:
        candidates = self._sort_candidates()
        total = len(candidates)

        if total == 0:
            self._console.print("[yellow]No unlabeled chunks in the index.[/yellow]")
            return

        self._console.print(
            f"[bold]{total}[/bold] unlabeled chunks | "
            f"probe {'[green]ready[/green]' if self._probe.trained else '[dim]untrained[/dim]'}"
        )

        i = 0
        while i < len(candidates):
            row, entry = candidates[i]

            probe_prob = None
            if self._probe.trained:
                vec = self._index.reconstruct(row).reshape(1, -1)
                probe_prob = float(self._probe.predict_proba(vec)[0])

            self._display_doc(entry, probe_prob, i + 1, len(candidates))

            ch = _getch()
            self._console.print()

            if ch == "y":
                self._label(row, entry, 1)
                if self._new_since_retrain >= RETRAIN_EVERY:
                    self._auto_retrain()
                    candidates = self._sort_candidates()
                i += 1
            elif ch == "n":
                self._label(row, entry, 0)
                if self._new_since_retrain >= RETRAIN_EVERY:
                    self._auto_retrain()
                    candidates = self._sort_candidates()
                i += 1
            elif ch == "s":
                i += 1
            elif ch == "t":
                self._auto_retrain()
                candidates = self._sort_candidates()
            elif ch in ("q", "\x03"):
                break

        n_pos = sum(1 for r in self._labels if r["label"] == 1)
        n_neg = len(self._labels) - n_pos
        self._console.print(
            f"\n[bold]Session done.[/bold] Total labeled: {len(self._labels)} "
            f"([green]{n_pos} pos[/green] / [red]{n_neg} neg[/red])"
        )
        self.save()

    def _sort_candidates(self) -> list[tuple[int, dict]]:
        unlabeled = [(i, e) for i, e in enumerate(self._id_map) if _key(e) not in self._labeled_keys]

        if not self._probe.trained:
            return unlabeled  # no centroid signal available pre-probe — unlabeled order

        rows = [i for i, _ in unlabeled]
        vecs = np.vstack([self._index.reconstruct(i) for i in rows])
        probs = self._probe.predict_proba(vecs)
        prob_map = dict(zip(rows, probs.tolist()))
        unlabeled.sort(key=lambda x: abs(prob_map[x[0]] - 0.5))
        return unlabeled

    def _display_doc(self, entry: dict, probe_prob: float | None, idx: int, total: int) -> None:
        self._console.print(Rule(f"{idx} / {total}"))

        probe_str = f"{probe_prob:.3f}" if probe_prob is not None else "n/a"
        meta = f"{entry['session']}  (chunk {entry['chunk_idx']})  │  probe: {probe_str}"
        content = f"[dim]{entry['path']}[/dim]\n[cyan]{meta}[/cyan]\n\n{entry['snippet']}"
        self._console.print(Panel(content, expand=False))

        n_pos = sum(1 for r in self._labels if r["label"] == 1)
        n_neg = len(self._labels) - n_pos
        self._console.print(
            f"Labeled {len(self._labels)} ([green]{n_pos}[/green] pos, [red]{n_neg}[/red] neg)"
            "  │  [bold][y][/bold]es  [bold][n][/bold]o  [bold][s][/bold]kip"
            "  [bold][t][/bold]rain  [bold][q][/bold]uit"
        )

    def _label(self, row: int, entry: dict, label: int) -> None:
        self._labels.append({
            "key": _key(entry),
            "path": entry["path"],
            "chunk_idx": entry["chunk_idx"],
            "session": entry["session"],
            "label": label,
            "labeled_at": datetime.now().isoformat(),
        })
        self._labeled_keys.add(_key(entry))
        self._new_since_retrain += 1

    def _auto_retrain(self) -> None:
        self._console.print("\n[bold yellow]Auto-retraining probe...[/bold yellow]")
        stats = self._retrain_probe()
        auc_str = f"{stats['auc']:.3f}" if stats["auc"] is not None else "n/a"
        self._console.print(f"  acc={stats['acc']:.3f}  auc={auc_str}  n={stats['n']}")
        self._new_since_retrain = 0
        self.save()

    def _retrain_probe(self) -> dict:
        rows = [self._key_to_row[r["key"]] for r in self._labels]
        y = np.array([r["label"] for r in self._labels], dtype=int)
        X = np.vstack([self._index.reconstruct(i) for i in rows]).astype(np.float32)

        self._probe.fit(X, y)
        self._probe.save(self._probe_path)

        y_prob = self._probe.predict_proba(X)
        acc = float(((y_prob >= 0.5).astype(int) == y).mean())

        auc = None
        if len(set(y.tolist())) >= 2:
            from sklearn.metrics import roc_auc_score
            auc = float(roc_auc_score(y, y_prob))

        return {"acc": acc, "auc": auc, "n": len(rows)}

    def save(self) -> None:
        tmp = self._labels_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._labels, indent=2))
        os.replace(tmp, self._labels_path)


def main():
    ap = argparse.ArgumentParser(description="Interactive relevance labeling for the session archive")
    ap.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    args = ap.parse_args()

    index_dir = Path(args.index_dir)
    index_path = index_dir / "index.faiss"
    id_map_path = index_dir / "id_map.json"
    labels_path = index_dir / "relevance_labels.json"
    probe_path = index_dir / "relevance_probe.pt"

    if not index_path.exists() or not id_map_path.exists():
        raise SystemExit(f"No index found at {index_dir} — run ingest_sessions.py first.")

    index = faiss.read_index(str(index_path))
    id_map = json.loads(id_map_path.read_text())
    probe = MLPProbe.load(probe_path) if probe_path.exists() else MLPProbe()

    console = Console(highlight=False)
    labeler = InteractiveLabeler(index, id_map, probe, labels_path, probe_path, console)
    labeler.run()


if __name__ == "__main__":
    main()
