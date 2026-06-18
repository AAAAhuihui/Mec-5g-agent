from __future__ import annotations

from typing import Any

from app.agent.answer_generator import generate_answer
from app.agent.classifier import classify_question
from app.agent.crag_evaluator import crag_evaluate
from app.agent.query_rewriter import generate_queries
from app.agent.self_checker import self_check
from app.agent.state import RAGState, initial_state
from app.config import settings
from app.llm.deepseek_client import DeepSeekClient
from app.rag.evidence_compressor import compress_evidence
from app.rag.reranker import rerank_docs
from app.rag.retriever import HybridRetriever


MAX_RETRIEVAL_RETRY = 2
MAX_ANSWER_REWRITE = 1


class RAGWorkflow:
    def __init__(self, retriever: HybridRetriever | None = None) -> None:
        self.retriever = retriever or HybridRetriever()

    def run(self, question: str) -> RAGState:
        state = initial_state(question)
        classification = classify_question(question)
        state.update(classification)

        if not state["need_retrieval"]:
            state["draft_answer"] = self._answer_general(question)
            state["self_check_result"] = {
                "faithfulness": "supported",
                "unsupported_claims": [],
                "missing_points": [],
                "need_more_retrieval": False,
                "action": "final",
            }
            state["final_answer"] = state["draft_answer"]
            return state

        state["queries"] = generate_queries(
            state["question"],
            state["intent"],
            state["domain_entities"],
        )
        self._retrieve_until_usable(state)
        state["evidence_facts"] = compress_evidence(state["reranked_docs"])
        state["draft_answer"] = generate_answer(
            state["question"],
            state["evidence_facts"],
            state["intent"],
        )
        state["self_check_result"] = self_check(
            state["question"],
            state["draft_answer"],
            state["evidence_facts"],
        )

        if (
            state["self_check_result"].get("action") == "revise_answer"
            and state["answer_rewrite_count"] < MAX_ANSWER_REWRITE
        ):
            state["answer_rewrite_count"] += 1
            state["draft_answer"] = generate_answer(
                state["question"],
                state["evidence_facts"],
                state["intent"],
                revise=True,
            )
            state["self_check_result"] = self_check(
                state["question"],
                state["draft_answer"],
                state["evidence_facts"],
            )

        if (
            state["self_check_result"].get("action") == "retrieve_more"
            and state["retrieval_retry_count"] < MAX_RETRIEVAL_RETRY
        ):
            state["queries"].append(state["question"] + " 补充证据")
            self._retrieve_until_usable(state)
            state["evidence_facts"] = compress_evidence(state["reranked_docs"])
            state["draft_answer"] = generate_answer(
                state["question"],
                state["evidence_facts"],
                state["intent"],
                revise=True,
            )
            state["self_check_result"] = self_check(
                state["question"],
                state["draft_answer"],
                state["evidence_facts"],
            )

        state["final_answer"] = state["draft_answer"]
        return state

    def _retrieve_until_usable(self, state: RAGState) -> None:
        while True:
            docs = self.retriever.retrieve(state["queries"], top_k=settings.top_k)
            state["retrieved_docs"] = docs
            state["reranked_docs"] = rerank_docs(state["question"], docs, top_k=settings.top_k)
            evaluation = crag_evaluate(state["question"], state["reranked_docs"])
            state["retrieval_grade"] = evaluation["retrieval_grade"]
            state["retrieval_score"] = float(evaluation.get("score", 0.0))

            if state["retrieval_grade"] in {"correct", "empty"}:
                return
            if state["retrieval_retry_count"] >= MAX_RETRIEVAL_RETRY:
                return
            state["retrieval_retry_count"] += 1
            for query in evaluation.get("next_queries", []):
                if query and query not in state["queries"]:
                    state["queries"].append(query)

    def _answer_general(self, question: str) -> str:
        client = DeepSeekClient()
        answer = client.chat(
            [
                {"role": "system", "content": "你是一个面向 MEC/5G 信令场景的Agent。"},
                {"role": "user", "content": question},
            ]
        )
        return answer or "我是一个面向 MEC/5G 信令场景的 RAG Agent。这个问题看起来不属于当前领域知识库范围。"


def run_workflow(question: str) -> RAGState:
    return RAGWorkflow().run(question)

