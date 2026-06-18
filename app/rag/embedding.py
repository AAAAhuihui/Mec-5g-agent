from __future__ import annotations

import hashlib
import math
import os
import re

from app.config import settings


class EmbeddingModel:
    """优先使用 sentence-transformers，缺失时使用确定性 Hashing Embedding 兜底。"""

    def __init__(self, model_name: str | None = None, fallback_dim: int = 384) -> None:
        self.model_name = model_name or settings.embedding_model
        self.fallback_dim = fallback_dim
        self._model = None
        self.backend = "hashing"
        self.load_error: str | None = None
        try:
            allow_download = os.getenv("EMBEDDING_ALLOW_DOWNLOAD", "0").lower() in {
                "1",
                "true",
                "yes",
            }
            if allow_download:
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            else:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")

            from sentence_transformers import SentenceTransformer

            if allow_download:
                self._model = SentenceTransformer(self.model_name)
            else:
                self._model = SentenceTransformer(self.model_name, local_files_only=True)
            self.backend = "sentence-transformers"
        except Exception as exc:
            self._model = None
            self.load_error = str(exc)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is not None:
            vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
            return [list(map(float, vector)) for vector in vectors]
        return [self._hash_embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _hash_embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())
        vector = [0.0] * self.fallback_dim
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.fallback_dim
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
