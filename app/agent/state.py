from __future__ import annotations

from typing import Any, TypedDict


class RAGState(TypedDict):
    question: str
    task_id: str
    task_status: str
    task_timing: dict[str, Any]
    task_plan: list[dict[str, Any]]
    react_steps: list[dict[str, Any]]
    iteration_count: int
    tool_call_count: int
    cmd_approval_count: int
    file_approval_count: int
    command_history: list[dict[str, Any]]
    step_count: int
    no_progress_count: int
    loop_detected: bool
    task_completed: bool
    reflection_context: dict[str, Any]
    llm_messages: list[dict[str, Any]]
    last_tool_call_id: str
    intent: str
    domain_entities: list[str]
    need_retrieval: bool
    selected_tool: str
    tool_args: dict[str, Any]
    tool_selection_source: str
    tool_selection_reason: str
    approval: dict[str, Any]
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
        "task_id": "",
        "task_status": "running",
        "task_timing": {},
        "task_plan": [],
        "react_steps": [],
        "iteration_count": 0,
        "tool_call_count": 0,
        "cmd_approval_count": 0,
        "file_approval_count": 0,
        "command_history": [],
        "step_count": 0,
        "no_progress_count": 0,
        "loop_detected": False,
        "task_completed": False,
        "reflection_context": {},
        "llm_messages": [],
        "last_tool_call_id": "",
        "intent": "general",
        "domain_entities": [],
        "need_retrieval": False,
        "selected_tool": "",
        "tool_args": {},
        "tool_selection_source": "",
        "tool_selection_reason": "",
        "approval": {},
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
