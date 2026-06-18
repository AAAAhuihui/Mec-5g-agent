from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings
from app.rag.keyword_search import keyword_search
from app.rag.vector_store import VectorStore


class HybridRetriever:
    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self.vector_store = vector_store or VectorStore()

    def retrieve(self, queries: list[str], top_k: int | None = None) -> list[dict[str, Any]]:
        limit = top_k or settings.top_k
        merged: dict[str, dict[str, Any]] = {}
        all_docs = self.vector_store.all_documents()

        for query in queries:
            vector_docs = self.vector_store.similarity_search(query, top_k=limit)
            keyword_docs = keyword_search(query, all_docs, top_k=limit)
            for doc in [*vector_docs, *keyword_docs]:
                key = self._dedupe_key(doc)
                existing = merged.get(key)
                if existing is None or float(doc.get("score", 0.0)) > float(existing.get("score", 0.0)):
                    merged[key] = {
                        "content": doc.get("content", ""),
                        "source": doc.get("source", "unknown"),
                        "score": float(doc.get("score", 0.0)),
                        "metadata": doc.get("metadata", {}),
                    }

        return sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:limit]

    def _dedupe_key(self, doc: dict[str, Any]) -> str:
        metadata = doc.get("metadata", {})
        source = metadata.get("source", doc.get("source", "unknown"))
        chunk_index = metadata.get("chunk_index")
        if chunk_index is not None:
            return f"{source}:{chunk_index}"
        content = str(doc.get("content", ""))
        return hashlib.sha1(f"{source}:{content[:200]}".encode("utf-8")).hexdigest()

