"""Offline, reproducible retrieval evaluation utilities."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.config import settings
from app.rag.context_windows import build_context_windows, window_contains_gold
from app.rag.document_loader import load_documents
from app.rag.embedding import EmbeddingModel
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.keyword_search import BM25Index
from app.rag.reranker import rerank_docs
from app.rag.text_splitter import split_documents


def load_eval_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate labelled retrieval cases from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("evaluation cases must be a non-empty JSON list")

    cases: list[dict[str, Any]] = []
    required_keys = {"id", "question"}
    for item in data:
        if not isinstance(item, dict) or not required_keys.issubset(item):
            raise ValueError(f"invalid evaluation case: {item!r}")
        terms = item.get("required_terms", [])
        if not isinstance(terms, list) or not all(isinstance(term, str) and term for term in terms):
            raise ValueError(f"case {item.get('id', '<unknown>')} has invalid required_terms")
        expected_source = str(item.get("expected_source", "")).strip()
        expected_chunk_indexes = item.get("expected_chunk_indexes", [])
        if not isinstance(expected_chunk_indexes, list) or not all(
            isinstance(index, int) and index >= 0 for index in expected_chunk_indexes
        ):
            raise ValueError(
                f"case {item.get('id', '<unknown>')} has invalid expected_chunk_indexes"
            )
        expected_chunk_ids = item.get("expected_chunk_ids", [])
        if not isinstance(expected_chunk_ids, list) or not all(
            isinstance(chunk_id, str) and chunk_id.strip() for chunk_id in expected_chunk_ids
        ):
            raise ValueError(f"case {item.get('id', '<unknown>')} has invalid expected_chunk_ids")
        if not expected_source and not expected_chunk_indexes and not expected_chunk_ids:
            raise ValueError(
                f"case {item.get('id', '<unknown>')} needs a source or Gold chunk label"
            )
        cases.append(
            {
                "id": str(item["id"]),
                "question": str(item["question"]),
                "expected_source": expected_source,
                "expected_chunk_indexes": expected_chunk_indexes,
                "expected_chunk_ids": expected_chunk_ids,
                "required_terms": terms,
                "gold_answer": str(item.get("gold_answer", "")),
                "category": str(item.get("category", "")),
            }
        )
    return cases


def evaluate_source_dir(
    source_dir: str,
    cases: list[dict[str, Any]],
    *,
    top_k: int = 6,
    embedding_model: EmbeddingModel | None = None,
    vector_candidate_k: int | None = None,
    bm25_candidate_k: int | None = None,
    fused_candidate_k: int | None = None,
    reranker_backend: str | None = None,
) -> dict[str, Any]:
    """Evaluate hybrid retrieval against labelled questions without an LLM API."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    vector_limit = vector_candidate_k or settings.rag_vector_candidate_k
    bm25_limit = bm25_candidate_k or settings.rag_bm25_candidate_k
    fused_limit = fused_candidate_k or settings.rag_fused_candidate_k
    if min(vector_limit, bm25_limit, fused_limit) < 1:
        raise ValueError("candidate limits must be positive")
    if fused_limit < top_k:
        raise ValueError("fused_candidate_k must be greater than or equal to top_k")

    chunks = split_documents(load_documents(source_dir))
    if not chunks:
        raise ValueError("source_dir did not produce any document chunks")

    model = embedding_model or EmbeddingModel()
    records = _embed_chunks(chunks, model)
    bm25_index = BM25Index(records)
    results = [
        _evaluate_case(
            case,
            records,
            model,
            bm25_index,
            top_k,
            vector_limit,
            bm25_limit,
            fused_limit,
            reranker_backend,
        )
        for case in cases
    ]
    retrieval_hits = sum(bool(result["retrieval_hit"]) for result in results)
    candidate_hits = sum(bool(result["candidate_retrieval_hit"]) for result in results)
    expanded_candidate_hits = sum(
        bool(result["expanded_candidate_retrieval_hit"]) for result in results
    )
    vector_hits = sum(bool(result["vector_retrieval_hit"]) for result in results)
    bm25_hits = sum(bool(result["bm25_retrieval_hit"]) for result in results)
    union_hits = sum(bool(result["union_retrieval_hit"]) for result in results)
    source_hits = sum(bool(result["expected_source_hit"]) for result in results)
    usable = sum(bool(result["evidence_usable"]) for result in results)
    mrr = sum(float(result["reciprocal_rank"]) for result in results) / len(results)
    discriminative = len(records) > top_k
    chunk_labelled = sum(
        bool(result["expected_chunk_ids"] or result["expected_chunk_indexes"])
        for result in results
    )
    return {
        "scope": "offline_hybrid_retrieval_evaluation",
        "embedding_backend": model.backend,
        "embedding_model": model.model_name,
        "embedding_load_error": model.load_error,
        "source_dir": str(source_dir),
        "document_chunks": len(records),
        "case_count": len(results),
        "top_k": top_k,
        "pipeline": {
            "vector_candidate_k": vector_limit,
            "bm25_candidate_k": bm25_limit,
            "fused_candidate_k": fused_limit,
            "rrf_k": settings.rag_rrf_k,
            "reranker_backend": reranker_backend or settings.reranker_backend,
            "reranker_model": settings.reranker_model,
            "neighbor_radius": settings.rag_neighbor_radius,
            "reranker_input": "same-section context windows",
        },
        "metrics": {
            "vector_recall": round(vector_hits / len(results), 4),
            "bm25_recall": round(bm25_hits / len(results), 4),
            "union_recall": round(union_hits / len(results), 4),
            "candidate_recall": round(candidate_hits / len(results), 4),
            "expanded_candidate_recall": round(expanded_candidate_hits / len(results), 4),
            "recall_at_k": round(retrieval_hits / len(results), 4),
            "window_recall_at_k": round(retrieval_hits / len(results), 4),
            "mrr_at_k": round(mrr, 4),
            "window_mrr_at_k": round(mrr, 4),
            "evidence_usable_rate": round(usable / len(results), 4),
            "retrieval_hit_count": retrieval_hits,
            "candidate_hit_count": candidate_hits,
            "expanded_candidate_hit_count": expanded_candidate_hits,
            "vector_hit_count": vector_hits,
            "bm25_hit_count": bm25_hits,
            "union_hit_count": union_hits,
            "source_hit_count": source_hits,
            "evidence_usable_count": usable,
        },
        "metric_validity": {
            "retrieval_pool_larger_than_top_k": discriminative,
            "chunk_labelled_cases": chunk_labelled,
            "source_only_cases": len(results) - chunk_labelled,
            "suitable_for_resume_claim": discriminative and chunk_labelled == len(results),
            "warning": _metric_warning(
                discriminative=discriminative,
                chunk_labelled=chunk_labelled,
                case_count=len(results),
            ),
        },
        "cases": results,
        "limitations": [
            "This measures retrieval only; it does not measure answer correctness or hallucination.",
            "Results depend on the selected source documents and embedding backend.",
        ],
    }


def _embed_chunks(chunks: list[dict[str, Any]], model: EmbeddingModel) -> list[dict[str, Any]]:
    embeddings = model.embed([str(chunk["content"]) for chunk in chunks])
    records: list[dict[str, Any]] = []
    for chunk, embedding in zip(chunks, embeddings):
        records.append(
            {
                "id": _chunk_id(chunk),
                "content": str(chunk["content"]),
                "source": str(chunk.get("source", "unknown")),
                "metadata": dict(chunk.get("metadata", {})),
                "embedding": embedding,
            }
        )
    return records


def _evaluate_case(
    case: dict[str, Any],
    records: list[dict[str, Any]],
    model: EmbeddingModel,
    bm25_index: BM25Index,
    top_k: int,
    vector_candidate_k: int,
    bm25_candidate_k: int,
    fused_candidate_k: int,
    reranker_backend: str | None,
) -> dict[str, Any]:
    query_embedding = model.embed_query(case["question"])
    vector_docs = [
        {
            "content": record["content"],
            "source": record["source"],
            "metadata": record["metadata"],
            "score": _cosine(query_embedding, record["embedding"]),
        }
        for record in records
    ]
    vector_docs = sorted(vector_docs, key=lambda item: item["score"], reverse=True)[
        :vector_candidate_k
    ]
    for document in vector_docs:
        document["retrieval_method"] = "vector"
    bm25_docs = bm25_index.search(case["question"], top_k=bm25_candidate_k)
    expected_chunk_indexes = list(case.get("expected_chunk_indexes", []))
    expected_set = set(expected_chunk_indexes)
    expected_chunk_ids = list(case.get("expected_chunk_ids", []))
    expected_id_set = set(expected_chunk_ids)
    vector_retrieval_hit = _documents_hit_label(
        vector_docs, case, expected_set, expected_id_set
    )
    bm25_retrieval_hit = _documents_hit_label(
        bm25_docs, case, expected_set, expected_id_set
    )
    union_retrieval_hit = vector_retrieval_hit or bm25_retrieval_hit
    candidates = reciprocal_rank_fusion(
        [("vector", vector_docs), ("bm25", bm25_docs)],
        top_k=fused_candidate_k,
        rrf_k=settings.rag_rrf_k,
    )
    windows = build_context_windows(
        candidates,
        records,
        radius=settings.rag_neighbor_radius,
    )
    expanded_candidate_retrieval_hit = any(
        _window_hits_label(window, case, expected_set, expected_id_set)
        for window in windows
    )
    ranked = rerank_docs(
        case["question"],
        windows,
        top_k=top_k,
        backend=reranker_backend,
    )
    retrieved_sources = [str(doc["source"]) for doc in ranked]
    retrieved_chunk_indexes = [
        _chunk_index(doc.get("metadata", {}).get("chunk_index")) for doc in ranked
    ]
    candidate_chunk_indexes = [
        _chunk_index(doc.get("metadata", {}).get("chunk_index")) for doc in candidates
    ]
    retrieved_chunk_ids = [
        str(doc.get("metadata", {}).get("chunk_id", "")) for doc in ranked
    ]
    candidate_chunk_ids = [
        str(doc.get("metadata", {}).get("chunk_id", "")) for doc in candidates
    ]
    retrieved_window_member_chunk_ids = [
        list(doc.get("metadata", {}).get("member_chunk_ids", [])) for doc in ranked
    ]
    retrieved_window_member_chunk_indexes = [
        list(doc.get("metadata", {}).get("member_chunk_indexes", [])) for doc in ranked
    ]
    candidate_window_member_chunk_ids = [
        list(doc.get("metadata", {}).get("member_chunk_ids", [])) for doc in windows
    ]
    evidence = "\n".join(str(doc["content"]) for doc in ranked).casefold()
    term_coverage = {
        term: term.casefold() in evidence for term in case["required_terms"]
    }
    if expected_id_set:
        candidate_chunk_hit = any(chunk_id in expected_id_set for chunk_id in candidate_chunk_ids)
    else:
        candidate_chunk_hit = any(
            index is not None and index in expected_set for index in candidate_chunk_indexes
        )
    centre_chunk_ranks = [
        rank
        for rank, document in enumerate(ranked, start=1)
        if _documents_hit_label([document], case, expected_set, expected_id_set)
    ]
    window_ranks = [
        rank
        for rank, window in enumerate(ranked, start=1)
        if _window_hits_label(window, case, expected_set, expected_id_set)
    ]
    source_ranks = [
        rank
        for rank, source in enumerate(retrieved_sources, start=1)
        if case.get("expected_source") and source == case["expected_source"]
    ]
    has_chunk_labels = bool(expected_chunk_ids or expected_chunk_indexes)
    matching_ranks = window_ranks if has_chunk_labels else source_ranks
    candidate_source_hit = bool(case.get("expected_source")) and any(
        str(doc.get("source", "")) == case["expected_source"] for doc in candidates
    )
    candidate_retrieval_hit = (
        candidate_chunk_hit if has_chunk_labels else candidate_source_hit
    )
    first_rank = min(matching_ranks) if matching_ranks else None
    source_hit = bool(case.get("expected_source")) and case["expected_source"] in retrieved_sources
    return {
        "id": case["id"],
        "question": case["question"],
        "expected_source": case["expected_source"],
        "expected_chunk_indexes": expected_chunk_indexes,
        "expected_chunk_ids": expected_chunk_ids,
        "retrieved_sources": retrieved_sources,
        "retrieved_chunk_indexes": retrieved_chunk_indexes,
        "candidate_chunk_indexes": candidate_chunk_indexes,
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "candidate_chunk_ids": candidate_chunk_ids,
        "retrieved_window_member_chunk_ids": retrieved_window_member_chunk_ids,
        "retrieved_window_member_chunk_indexes": retrieved_window_member_chunk_indexes,
        "candidate_window_member_chunk_ids": candidate_window_member_chunk_ids,
        "vector_retrieval_hit": vector_retrieval_hit,
        "bm25_retrieval_hit": bm25_retrieval_hit,
        "union_retrieval_hit": union_retrieval_hit,
        "candidate_retrieval_hit": candidate_retrieval_hit,
        "expanded_candidate_retrieval_hit": expanded_candidate_retrieval_hit,
        "expected_source_hit": source_hit,
        "expected_center_chunk_hit": bool(centre_chunk_ranks),
        "expected_chunk_hit": bool(window_ranks),
        "window_hit": bool(window_ranks),
        "retrieval_hit": first_rank is not None,
        "first_relevant_rank": first_rank,
        "reciprocal_rank": round(1.0 / first_rank, 4) if first_rank else 0.0,
        "required_terms": case["required_terms"],
        "term_coverage": term_coverage,
        "evidence_usable": all(term_coverage.values()),
    }


def _chunk_index(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _documents_hit_label(
    documents: list[dict[str, Any]],
    case: dict[str, Any],
    expected_chunk_indexes: set[int],
    expected_chunk_ids: set[str],
) -> bool:
    if expected_chunk_ids:
        return any(
            str(document.get("metadata", {}).get("chunk_id", "")) in expected_chunk_ids
            for document in documents
        )
    if expected_chunk_indexes:
        return any(
            _chunk_index(document.get("metadata", {}).get("chunk_index"))
            in expected_chunk_indexes
            for document in documents
        )
    expected_source = str(case.get("expected_source", ""))
    return bool(expected_source) and any(
        str(document.get("source", "")) == expected_source for document in documents
    )


def _window_hits_label(
    window: dict[str, Any],
    case: dict[str, Any],
    expected_chunk_indexes: set[int],
    expected_chunk_ids: set[str],
) -> bool:
    if expected_chunk_ids or expected_chunk_indexes:
        return window_contains_gold(
            window,
            expected_chunk_ids=expected_chunk_ids,
            expected_chunk_indexes=expected_chunk_indexes,
        )
    expected_source = str(case.get("expected_source", ""))
    return bool(expected_source) and str(window.get("source", "")) == expected_source


def _metric_warning(*, discriminative: bool, chunk_labelled: int, case_count: int) -> str:
    messages: list[str] = []
    if not discriminative:
        messages.append(
            "The knowledge base has no more chunks than Top-K, so every chunk can be returned. "
            "Increase corpus size or lower Top-K before using this score as a comparison metric."
        )
    if chunk_labelled < case_count:
        messages.append(
            "Some cases only label the source document. For a single-document corpus, source-level "
            "Recall@K cannot distinguish correct chunks from incorrect chunks."
        )
    return " ".join(messages)


def _chunk_id(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata", {})
    stable_id = str(metadata.get("chunk_id", "")).strip()
    if stable_id:
        return stable_id
    source = metadata.get("source") or chunk.get("source", "unknown")
    index = metadata.get("chunk_index", "")
    content = str(chunk.get("content", ""))
    return hashlib.sha1(f"{source}:{index}:{content}".encode("utf-8")).hexdigest()


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
    return numerator / (left_norm * right_norm)
