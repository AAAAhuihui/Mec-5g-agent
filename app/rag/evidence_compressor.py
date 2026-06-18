from __future__ import annotations

import re
from typing import Any


IMPORTANT_TERMS = [
    "NEF",
    "NEF_NBI",
    "Traffic Influence",
    "SMF",
    "UPF",
    "PDR",
    "FAR",
    "N3",
    "N6",
    "MEP",
    "MEC APP",
    "分流",
    "策略",
    "成功",
    "失败",
]


def compress_evidence(docs: list[dict[str, Any]], max_facts: int = 8) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    seen: set[str] = set()
    for doc in docs:
        content = str(doc.get("content", ""))
        sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", content)
        for sentence in sentences:
            sentence = sentence.strip("- *\t ")
            if len(sentence) < 12:
                continue
            if not any(term.lower() in sentence.lower() for term in IMPORTANT_TERMS):
                continue
            claim = sentence[:220]
            key = claim.lower()
            if key in seen:
                continue
            facts.append({"claim": claim, "source": str(doc.get("source", "unknown"))})
            seen.add(key)
            if len(facts) >= max_facts:
                return facts
    return facts

