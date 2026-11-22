"""Embedding backends. E5 for real runs, a hashing embedder for offline tests."""

from __future__ import annotations

import re
import unicodedata
import zlib
from typing import Protocol

import numpy as np

DEFAULT_EMBEDDER = "e5"  # "hash" is for tests and offline runs only


class Embedder(Protocol):
    """May also define `count_tokens(text) -> int` (tokens of `text` embedded as a
    passage); chunking then sizes passages exactly, otherwise it estimates."""

    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class E5Embedder:
    """intfloat/multilingual-e5-small with the required `query: ` / `passage: ` prefixes."""

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small", device: str | None = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise SystemExit(f"cannot load the e5 embedder ({e}); run `uv sync` or pass --embedder hash") from None

        self.name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        # e5-small reads 512 tokens; longer input is silently truncated. Chunking measures
        # passages with count_tokens and keeps them at ~400 (chunking.MAX_TOKENS).
        self._model.max_seq_length = 512

    def count_tokens(self, text: str) -> int:
        """Tokens the model sees for `text` as a passage (prefix and special tokens included)."""
        return len(self._model.tokenizer(f"passage: {text}", add_special_tokens=True, verbose=False)["input_ids"])

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(
            [f"passage: {t}" for t in texts], batch_size=32, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        vec = self._model.encode([f"query: {text}"], normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vec[0], dtype=np.float32)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


class HashEmbedder:
    """Deterministic character n-gram hashing embedder (no download, for tests).

    Captures surface similarity and Polish inflection (shared stems), not real
    semantics; real paraphrase quality comes from E5.
    """

    def __init__(self, dim: int = 512, ngram: tuple[int, int] = (3, 5)):
        self.dim = dim
        self.ngram = ngram
        self.name = f"hash-{dim}-{ngram[0]}{ngram[1]}"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"\w+", _fold(text)):
            w = f" {word} "
            for n in range(self.ngram[0], self.ngram[1] + 1):
                for i in range(max(1, len(w) - n + 1)):
                    g = w[i : i + n]
                    h = zlib.crc32(g.encode("utf-8"))
                    v[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        return v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize_rows(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize_rows(self._vec(text)[None, :])[0]


def get_embedder(name: str) -> Embedder:
    if name == "hash":
        return HashEmbedder()
    if name == "e5":
        return E5Embedder()
    return E5Embedder(name)
