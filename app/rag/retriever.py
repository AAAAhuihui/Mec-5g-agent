from __future__ import annotations

from typing import Any

from app.config import settings
from app.rag.context_windows import build_context_windows
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.keyword_search import BM25Index
from app.rag.vector_store import VectorStore


class HybridRetriever:
    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self.vector_store = vector_store or VectorStore()

    def retrieve(self, queries: list[str], top_k: int | None = None) -> list[dict[str, Any]]:
        """Return RRF-fused candidates; final truncation happens after reranking."""
        limit = top_k or settings.rag_fused_candidate_k
        all_documents = self.vector_store.all_documents()
        bm25_index = BM25Index(all_documents)
        result_lists: list[tuple[str, list[dict[str, Any]]]] = []

        for query_index, query in enumerate(queries):
            vector_documents = self.vector_store.similarity_search(
                query,
                top_k=settings.rag_vector_candidate_k,
            )
            for document in vector_documents:
                document["retrieval_method"] = "vector"
            bm25_documents = bm25_index.search(
                query,
                top_k=settings.rag_bm25_candidate_k,
            )
            result_lists.extend(
                [
                    (f"q{query_index}:vector", vector_documents),
                    (f"q{query_index}:bm25", bm25_documents),
                ]
            )

        return reciprocal_rank_fusion(
            result_lists,
            top_k=limit,
            rrf_k=settings.rag_rrf_k,
        )

    def expand_context_windows(
        self,
        candidates: list[dict[str, Any]],
        *,
        radius: int = 1,
    ) -> list[dict[str, Any]]:
        return build_context_windows(
            candidates,
            self.vector_store.all_documents(),
            radius=radius,
        )
