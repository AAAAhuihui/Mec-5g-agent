from __future__ import annotations

import hashlib
from typing import Any


def document_key(document: dict[str, Any]) -> str:
    metadata = document.get("metadata", {})
    chunk_id = metadata.get("chunk_id")
    if chunk_id:
        return str(chunk_id)
    source = metadata.get("source", document.get("source", "unknown"))
    chunk_index = metadata.get("chunk_index")
    if chunk_index is not None:
        return f"{source}:{chunk_index}"
    content = str(document.get("content", ""))
    return hashlib.sha1(f"{source}:{content[:200]}".encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(
    result_lists: list[tuple[str, list[dict[str, Any]]]],
    *,
    top_k: int = 30,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """Fuse result lists by rank so incomparable raw scores are never mixed."""
    if top_k < 1:
        return []
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")

    fused: dict[str, dict[str, Any]] = {}
    first_seen: dict[str, int] = {}
    sequence = 0
    for list_name, documents in result_lists:
        seen_in_list: set[str] = set()
        for rank, document in enumerate(documents, start=1):
            key = document_key(document)
            if key in seen_in_list:
                continue
            seen_in_list.add(key)
            if key not in fused:
                fused[key] = {
                    **document,
                    "rrf_score": 0.0,
                    "fusion_ranks": {},
                    "retrieval_methods": [],
                }
                first_seen[key] = sequence
                sequence += 1
            item = fused[key]
            item["rrf_score"] += 1.0 / (rrf_k + rank)
            item["fusion_ranks"][list_name] = rank
            if list_name not in item["retrieval_methods"]:
                item["retrieval_methods"].append(list_name)

    for item in fused.values():
        item["score"] = float(item["rrf_score"])
    ranked = sorted(
        fused.items(),
        key=lambda pair: (-float(pair[1]["rrf_score"]), first_seen[pair[0]]),
    )
    return [item for _, item in ranked[:top_k]]
