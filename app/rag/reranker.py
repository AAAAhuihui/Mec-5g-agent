from __future__ import annotations

from typing import Any

from app.rag.keyword_search import tokenize


def rerank_docs(question: str, docs: list[dict[str, Any]], top_k: int = 6) -> list[dict[str, Any]]:
    question_tokens = tokenize(question)
    reranked: list[dict[str, Any]] = []
    for doc in docs:
        content_tokens = tokenize(str(doc.get("content", "")))
        overlap = len(question_tokens & content_tokens)
        lexical_score = overlap / max(len(question_tokens), 1)
        combined = float(doc.get("score", 0.0)) * 0.7 + lexical_score * 0.3
        enriched = dict(doc)
        enriched["score"] = combined
        reranked.append(enriched)
    return sorted(reranked, key=lambda item: item["score"], reverse=True)[:top_k]

