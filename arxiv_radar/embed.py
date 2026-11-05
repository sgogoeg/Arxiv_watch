"""Paper embeddings.

Default: SPECTER2 base (allenai/specter2_base) through sentence-transformers,
with CLS pooling as in the SPECTER2 paper. Input is "title [SEP] abstract".

"hashing" is a dependency-free bag-of-words fallback for tests and dry runs.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def paper_text(p: dict, sep: str = " [SEP] ") -> str:
    return f"{p.get('title', '')}{sep}{p.get('abstract', '')}".strip()


def normalize_rows(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return x / n


class HashingEmbedder:
    """Deterministic hashed unigram+bigram vectors. No downloads; only for tests and quick checks."""

    name = "hashing"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        words = re.findall(r"[a-z0-9]+", text.lower())
        grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        for g in grams:
            h = int.from_bytes(hashlib.md5(g.encode()).digest()[:4], "little")
            v[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        return v

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return normalize_rows(np.stack([self._vec(t) for t in texts]))


class STEmbedder:
    """sentence-transformers model with CLS pooling (SPECTER2 convention)."""

    def __init__(self, model: str, batch_size: int = 32):
        from sentence_transformers import SentenceTransformer

        try:  # sentence-transformers >= 6
            from sentence_transformers.sentence_transformer.modules import Pooling, Transformer
        except ImportError:
            from sentence_transformers.models import Pooling, Transformer

        word = Transformer(model, max_seq_length=512)
        dim = (getattr(word, "get_embedding_dimension", None) or word.get_word_embedding_dimension)()
        pool = Pooling(dim, pooling_mode="cls")
        self.model = SentenceTransformer(modules=[word, pool], device="cpu")
        self.name = model
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            dim = self.model.get_sentence_embedding_dimension()
            return np.zeros((0, dim), dtype=np.float32)
        x = self.model.encode(texts, batch_size=self.batch_size, convert_to_numpy=True, show_progress_bar=False)
        return normalize_rows(x.astype(np.float32))


def make_embedder(cfg) -> HashingEmbedder | STEmbedder:
    if cfg.backend == "hashing":
        return HashingEmbedder()
    return STEmbedder(cfg.model, cfg.batch_size)


class EmbeddingCache:
    """Embeddings for papers reused across runs (seed and feedback papers).

    Kept in .cache/ (restored by actions/cache), not in git.
    """

    def __init__(self, path: str | Path, model_name: str):
        self.path = Path(path)
        self.model_name = model_name
        self.vecs: dict[str, np.ndarray] = {}
        if self.path.exists():
            z = np.load(self.path, allow_pickle=False)
            if str(z["model"]) == model_name:
                self.vecs = dict(zip(z["ids"].tolist(), z["vecs"]))

    def embed(self, embedder, items: list[tuple[str, str]]) -> np.ndarray:
        """items: (key, text). Returns stacked vectors, computing missing ones."""
        missing = [(k, t) for k, t in items if k not in self.vecs]
        if missing:
            vecs = embedder.encode([t for _, t in missing])
            for (k, _), v in zip(missing, vecs):
                self.vecs[k] = v
        if not items:
            return np.zeros((0, 1), dtype=np.float32)
        return np.stack([self.vecs[k] for k, _ in items])

    def save(self) -> None:
        if not self.vecs:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ids = np.array(list(self.vecs.keys()))
        vecs = np.stack(list(self.vecs.values()))
        np.savez_compressed(self.path, ids=ids, vecs=vecs, model=np.array(self.model_name))
