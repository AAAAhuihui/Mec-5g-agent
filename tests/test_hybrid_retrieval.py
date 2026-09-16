from __future__ import annotations

from app.config import settings
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.keyword_search import BM25Index
from app.rag.reranker import rerank_docs
from app.rag.retriever import HybridRetriever


def _doc(index: int, content: str, score: float = 0.0) -> dict:
    return {
        "content": content,
        "source": "spec.pdf",
        "score": score,
        "metadata": {"source": "spec.pdf", "chunk_index": index},
    }


def test_bm25_prioritizes_a_rare_exact_identifier() -> None:
    documents = [
        _doc(0, "service service service request data"),
        _doc(1, "NIDD configuration uses externalGroupId"),
    ]

    results = BM25Index(documents).search("NIDD externalGroupId service", top_k=2)

    assert results[0]["metadata"]["chunk_index"] == 1
    assert results[0]["retrieval_method"] == "bm25"


def test_rrf_rewards_documents_ranked_by_both_retrievers() -> None:
    first = _doc(0, "first")
    consensus = _doc(1, "consensus")
    lexical_only = _doc(2, "lexical")

    results = reciprocal_rank_fusion(
        [
            ("vector", [first, consensus]),
            ("bm25", [consensus, lexical_only, first]),
        ],
        top_k=3,
        rrf_k=60,
    )

    assert results[0]["metadata"]["chunk_index"] == 1
    assert results[0]["fusion_ranks"] == {"vector": 2, "bm25": 1}
    assert results[0]["rrf_score"] > results[1]["rrf_score"]


def test_hybrid_retriever_uses_wide_vector_recall_and_rrf() -> None:
    class _VectorStore:
        requested_top_k = None

        def all_documents(self):
            return [_doc(0, "NEF common"), _doc(1, "externalGroupId NIDD")]

        def similarity_search(self, query: str, top_k: int):
            self.requested_top_k = top_k
            return [_doc(0, "NEF common", 0.9), _doc(1, "externalGroupId NIDD", 0.8)]

    store = _VectorStore()
    results = HybridRetriever(vector_store=store).retrieve(["NIDD externalGroupId"], top_k=2)

    assert store.requested_top_k == settings.rag_vector_candidate_k
    assert len(results) == 2
    assert results[0]["metadata"]["chunk_index"] == 1
    assert results[0]["retrieval_methods"] == ["q0:vector", "q0:bm25"]


def test_cross_encoder_reranker_uses_pair_scores(monkeypatch) -> None:
    from app.rag import reranker

    class _CrossEncoder:
        @staticmethod
        def predict(pairs, **kwargs):
            assert pairs == [["question", "weak"], ["question", "strong"]]
            return [0.1, 0.9]

    monkeypatch.setattr(reranker, "_get_cross_encoder", lambda *args: _CrossEncoder())

    results = rerank_docs(
        "question",
        [_doc(0, "weak"), _doc(1, "strong")],
        top_k=1,
        backend="cross-encoder",
    )

    assert results[0]["metadata"]["chunk_index"] == 1
    assert results[0]["reranker_backend"] == "cross-encoder"
    assert results[0]["score"] == 0.9
