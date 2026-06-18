from __future__ import annotations

from typing import Any, TypedDict


class RAGState(TypedDict):
    question: str
    intent: str
    domain_entities: list[str]
    need_retrieval: bool
    queries: list[str]
    retrieved_docs: list[dict[str, Any]]
    reranked_docs: list[dict[str, Any]]
    retrieval_grade: str
    retrieval_score: float
    retrieval_retry_count: int
    evidence_facts: list[dict[str, Any]]
    draft_answer: str
    self_check_result: dict[str, Any]
    answer_rewrite_count: int
    final_answer: str


def initial_state(question: str) -> RAGState:
    return {
        "question": question,
        "intent": "general",
        "domain_entities": [],
        "need_retrieval": False,
        "queries": [],
        "retrieved_docs": [],
        "reranked_docs": [],
        "retrieval_grade": "empty",
        "retrieval_score": 0.0,
        "retrieval_retry_count": 0,
        "evidence_facts": [],
        "draft_answer": "",
        "self_check_result": {},
        "answer_rewrite_count": 0,
        "final_answer": "",
    }

