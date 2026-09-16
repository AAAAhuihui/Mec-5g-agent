from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings
from app.rag.keyword_search import tokenize


logger = logging.getLogger(__name__)
_MODEL_CACHE: dict[tuple[str, str, int], Any] = {}
_MODEL_ERRORS: dict[tuple[str, str, int], Exception] = {}
_MODEL_LOCK = threading.Lock()


def rerank_docs(
    question: str,
    docs: list[dict[str, Any]],
    top_k: int = 6,
    *,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    if top_k < 1 or not docs:
        return []
    selected_backend = (backend or settings.reranker_backend).lower()
    if selected_backend in {"cross-encoder", "cross_encoder", "crossencoder"}:
        try:
            return _cross_encoder_rerank(question, docs, top_k)
        except Exception as exc:
            if not settings.reranker_fail_open:
                raise
            logger.warning("Cross-Encoder unavailable; using lightweight reranker: %s", exc)
            return _lightweight_rerank(question, docs, top_k, error=str(exc))
    if selected_backend != "lightweight":
        raise ValueError(f"unsupported reranker backend: {selected_backend}")
    return _lightweight_rerank(question, docs, top_k)


def _cross_encoder_rerank(
    question: str,
    docs: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    model = _get_cross_encoder(
        settings.reranker_model,
        settings.reranker_device,
        settings.reranker_max_length,
    )
    pairs = [[question, str(doc.get("content", ""))] for doc in docs]
    scores = model.predict(
        pairs,
        batch_size=settings.reranker_batch_size,
        show_progress_bar=False,
    )
    reranked: list[dict[str, Any]] = []
    for doc, value in zip(docs, scores):
        score = _score_as_float(value)
        enriched = dict(doc)
        enriched["retrieval_score"] = float(doc.get("score", 0.0))
        enriched["reranker_score"] = score
        enriched["reranker_backend"] = "cross-encoder"
        enriched["score"] = score
        reranked.append(enriched)
    return sorted(reranked, key=lambda item: float(item["score"]), reverse=True)[:top_k]


def _lightweight_rerank(
    question: str,
    docs: list[dict[str, Any]],
    top_k: int,
    *,
    error: str | None = None,
) -> list[dict[str, Any]]:
    question_tokens = tokenize(question)
    reranked: list[dict[str, Any]] = []
    for rank, doc in enumerate(docs, start=1):
        content_tokens = tokenize(str(doc.get("content", "")))
        overlap = len(question_tokens & content_tokens)
        lexical_score = overlap / max(len(question_tokens), 1)
        rank_score = 1.0 / rank
        combined = rank_score * 0.35 + lexical_score * 0.65
        enriched = dict(doc)
        enriched["retrieval_score"] = float(doc.get("score", 0.0))
        enriched["reranker_score"] = combined
        enriched["reranker_backend"] = "lightweight"
        if error:
            enriched["reranker_error"] = error
        enriched["score"] = combined
        reranked.append(enriched)
    return sorted(reranked, key=lambda item: float(item["score"]), reverse=True)[:top_k]


def _get_cross_encoder(model_name: str, device: str, max_length: int) -> Any:
    key = (model_name, device, max_length)
    with _MODEL_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        if key in _MODEL_ERRORS:
            raise RuntimeError(str(_MODEL_ERRORS[key])) from _MODEL_ERRORS[key]
        try:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(
                model_name,
                device=device,
                max_length=max_length,
                local_files_only=not settings.reranker_allow_download,
            )
        except Exception as exc:
            _MODEL_ERRORS[key] = exc
            raise
        _MODEL_CACHE[key] = model
        return model


def _score_as_float(value: Any) -> float:
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except ValueError:
            pass
    if hasattr(value, "reshape"):
        flattened = value.reshape(-1)
        if len(flattened):
            return float(flattened[-1])
    if isinstance(value, (list, tuple)):
        return _score_as_float(value[-1])
    return float(value)
