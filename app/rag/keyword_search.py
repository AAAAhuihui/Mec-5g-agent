from __future__ import annotations

import re
from typing import Any


DOMAIN_TERMS = [
    "mec",
    "5g",
    "5gc",
    "nef",
    "nef_nbi",
    "traffic influence",
    "pfd",
    "pfu",
    "upf",
    "smf",
    "amf",
    "pcf",
    "mep",
    "mepm",
    "mecm",
    "mec app",
    "n3",
    "n6",
    "pdr",
    "far",
    "分流",
    "信令",
    "基站",
    "核心网",
    "边缘计算",
]


def tokenize(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", lowered))
    for term in DOMAIN_TERMS:
        if term in lowered:
            tokens.add(term)
    return tokens


def keyword_search(query: str, documents: list[dict[str, Any]], top_k: int = 6) -> list[dict[str, Any]]:
    query_tokens = tokenize(query)
    scored: list[dict[str, Any]] = []
    for document in documents:
        content = str(document.get("content", ""))
        doc_tokens = tokenize(content)
        overlap = query_tokens & doc_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(query_tokens), 1)
        boosted = dict(document)
        boosted["score"] = max(float(document.get("score", 0.0)), score)
        boosted.setdefault("metadata", {})
        scored.append(boosted)
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]

