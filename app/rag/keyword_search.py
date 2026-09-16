from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


DOMAIN_TERMS = [
    "mec", "5g", "5gc", "nef", "nef_nbi", "traffic influence",
    "pfd", "pfu", "upf", "smf", "amf", "pcf", "mep", "mepm",
    "mecm", "mec app", "n3", "n6", "pdr", "far", "分流", "信令",
    "基站", "核心网", "边缘计算",
]

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")


def tokenize_terms(text: str) -> list[str]:
    """Tokenize while retaining term frequency for BM25."""
    lowered = text.casefold()
    tokens: list[str] = []
    for token in _TOKEN_PATTERN.findall(lowered):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.append(token)
            if len(token) > 1:
                tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
    tokens.extend(term for term in DOMAIN_TERMS if term in lowered)
    return tokens


def tokenize(text: str) -> set[str]:
    """Backward-compatible set tokenizer used by the lightweight reranker."""
    return set(tokenize_terms(text))


class BM25Index:
    """Small in-process BM25 index suitable for the local RAG corpus."""

    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.term_frequencies = [
            Counter(tokenize_terms(str(document.get("content", ""))))
            for document in documents
        ]
        self.document_lengths = [sum(frequencies.values()) for frequencies in self.term_frequencies]
        self.average_document_length = (
            sum(self.document_lengths) / len(self.document_lengths)
            if self.document_lengths
            else 0.0
        )
        document_frequency: Counter[str] = Counter()
        for frequencies in self.term_frequencies:
            document_frequency.update(frequencies.keys())
        document_count = len(documents)
        self.inverse_document_frequency = {
            term: math.log(1.0 + (document_count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, top_k: int = 40) -> list[dict[str, Any]]:
        if top_k < 1 or not self.documents:
            return []
        query_terms = set(tokenize_terms(query))
        if not query_terms:
            return []

        scored: list[dict[str, Any]] = []
        average_length = self.average_document_length or 1.0
        for document, frequencies, document_length in zip(
            self.documents,
            self.term_frequencies,
            self.document_lengths,
        ):
            score = 0.0
            for term in query_terms:
                term_frequency = frequencies.get(term, 0)
                if not term_frequency:
                    continue
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * document_length / average_length
                )
                score += self.inverse_document_frequency.get(term, 0.0) * (
                    term_frequency * (self.k1 + 1.0) / denominator
                )
            if score <= 0.0:
                continue
            result = dict(document)
            result["score"] = float(score)
            result["retrieval_method"] = "bm25"
            result.setdefault("metadata", {})
            scored.append(result)
        return sorted(scored, key=lambda item: float(item["score"]), reverse=True)[:top_k]


def bm25_search(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int = 40,
) -> list[dict[str, Any]]:
    return BM25Index(documents).search(query, top_k=top_k)


def keyword_search(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int = 6,
) -> list[dict[str, Any]]:
    """Compatibility wrapper; keyword retrieval now uses BM25."""
    return bm25_search(query, documents, top_k=top_k)
