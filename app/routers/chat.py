from __future__ import annotations

from fastapi import APIRouter
from typing import Any

from app.agent.contextualizer import build_standalone_question
from app.agent.memory import append_message, get_or_create_memory
from app.agent.workflow import run_workflow
from app.schemas import ChatRequest, ChatResponse, EvidenceItem


router = APIRouter(tags=["chat"])


def _preview_content(content: str, max_length: int = 600) -> str:
    content = content.strip()
    if len(content) <= max_length:
        return content
    return content[:max_length].rstrip() + "..."


def _build_trace(
    original_question: str,
    standalone_question: str,
    state: dict[str, Any],
    evidence: list[EvidenceItem],
) -> dict[str, Any]:
    return {
        "note": "这是可审计执行轨迹，不是模型原始隐藏思维链。",
        "question": original_question,
        "standalone_question": standalone_question,
        "classification": {
            "intent": state["intent"],
            "domain_entities": state["domain_entities"],
            "need_retrieval": state["need_retrieval"],
        },
        "retrieval": {
            "queries": state["queries"],
            "grade": state["retrieval_grade"],
            "score": state["retrieval_score"],
            "retry_count": state["retrieval_retry_count"],
            "sources": [item.source for item in evidence],
        },
        "evidence_facts": state["evidence_facts"][:6],
        "self_check": state["self_check_result"],
        "answer_rewrite_count": state["answer_rewrite_count"],
    }


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    memory = get_or_create_memory(request.conversation_id)
    standalone_question = build_standalone_question(request.question, memory)
    state = run_workflow(standalone_question)
    evidence = [
        EvidenceItem(
            source=str(doc.get("source", "unknown")),
            content=_preview_content(str(doc.get("content", ""))),
            metadata=dict(doc.get("metadata", {})),
        )
        for doc in state["reranked_docs"][:3]
    ]
    append_message(memory.conversation_id, "user", request.question)
    append_message(
        memory.conversation_id,
        "assistant",
        state["final_answer"],
        intent=state["intent"],
        evidence_sources=[item.source for item in evidence],
    )
    return ChatResponse(
        conversation_id=memory.conversation_id,
        standalone_question=standalone_question,
        answer=state["final_answer"],
        intent=state["intent"],
        retrieval_grade=state["retrieval_grade"],
        self_check=state["self_check_result"],
        evidence=evidence,
        trace=_build_trace(request.question, standalone_question, state, evidence)
        if request.include_trace
        else {},
    )
