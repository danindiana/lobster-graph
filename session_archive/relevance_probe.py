"""
MLPProbe: a small relevance/quality classifier over bge-m3 chunk vectors.

Ported from militia-classifier's militia-rlhf/probe.py, collapsed to a single
modality (in_dim=1024 for bge-m3) since session_archive has no text/image
split — see the "Phase 2" section of the RAG consolidation plan for the
design note on what this probe does and doesn't model (a general, corpus-wide
relevance/quality signal accumulated across all labeling sessions, not true
per-query relevance).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.optim import Adam

EMBED_DIM = 1024  # BAAI/bge-m3


class _MLP(nn.Module):
    def __init__(self, in_dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(128, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class MLPProbe:
    LR = 1e-3
    WEIGHT_DECAY = 1e-4
    MAX_EPOCHS = 200
    PATIENCE = 20
    MIN_DELTA = 1e-4

    def __init__(self, in_dim: int = EMBED_DIM) -> None:
        self._in_dim = in_dim
        self._net: _MLP | None = None
        self._device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self._trained = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> float:
        net = _MLP(self._in_dim).to(self._device)
        opt = Adam(net.parameters(), lr=self.LR, weight_decay=self.WEIGHT_DECAY)
        loss_fn = nn.BCEWithLogitsLoss()

        X_t = torch.FloatTensor(X).to(self._device)
        y_t = torch.FloatTensor(y.astype(np.float32)).to(self._device)

        best_loss = math.inf
        patience_ctr = 0

        for _ in range(self.MAX_EPOCHS):
            net.train()
            opt.zero_grad()
            loss = loss_fn(net(X_t), y_t)
            loss.backward()
            opt.step()

            val = loss.item()
            if best_loss - val > self.MIN_DELTA:
                best_loss = val
                patience_ctr = 0
            else:
                patience_ctr += 1
            if patience_ctr >= self.PATIENCE:
                break

        self._net = net
        self._trained = True
        return best_loss

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._net is None:
            return np.full(len(X), 0.5, dtype=np.float32)

        self._net.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self._device)
            logits = self._net(X_t)
            return torch.sigmoid(logits).cpu().numpy().astype(np.float32)

    def save(self, path: str | Path) -> None:
        torch.save(
            {
                "state": self._net.state_dict() if self._net else None,
                "meta": {"in_dim": self._in_dim, "trained": self._trained},
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "MLPProbe":
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        meta = ckpt["meta"]
        probe = cls(in_dim=meta["in_dim"])
        if ckpt["state"] is not None:
            net = _MLP(meta["in_dim"])
            net.load_state_dict(ckpt["state"])
            probe._net = net.to(probe._device)
            probe._trained = meta.get("trained", True)
        return probe

    @property
    def trained(self) -> bool:
        return self._trained
