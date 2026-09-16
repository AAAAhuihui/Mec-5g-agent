from __future__ import annotations

from fastapi import APIRouter
from typing import Any

from app.agent.contextualizer import build_detail_followup_question, build_standalone_question
from app.agent.memory import append_message, append_operation, get_or_create_memory
from app.agent.mysql_memory_store import save_react_task
from app.agent.react_workflow import is_answer_elaboration_request, run_react_workflow
from app.agent.task_timing import finalize_task_timing, task_timing_payload
from app.schemas import ChatRequest, ChatResponse, EvidenceItem
from app.tools.cmd_approvals import CmdApprovalError, bind_cmd_approval
from app.tools.file_tools import FileToolError, bind_file_approval


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
        "task": {
            "task_id": state.get("task_id", ""),
            "status": state.get("task_status", ""),
            "plan": state.get("task_plan", []),
            "steps": state.get("react_steps", []),
            "step_count": state.get("step_count", 0),
            "no_progress_count": state.get("no_progress_count", 0),
            "loop_detected": state.get("loop_detected", False),
            "task_completed": state.get("task_completed", False),
            "timing": task_timing_payload(state),
            "reflection": state.get("reflection_context", {}),
            "command_history": state.get("command_history", []),
        },
        "tool_selection": {
            "selected_tool": state.get("selected_tool", ""),
            "args": state.get("tool_args", {}),
            "source": state.get("tool_selection_source", ""),
            "reason": state.get("tool_selection_reason", ""),
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
    answer_only = is_answer_elaboration_request(request.question)
    standalone_question = (
        build_detail_followup_question(request.question, memory)
        if answer_only
        else build_standalone_question(request.question, memory)
    )
    state = run_react_workflow(
        standalone_question,
        answer_only=answer_only,
    )
    approval = dict(state.get("approval", {}))
    if approval and approval.get("approval_type") == "file_write":
        try:
            approval = bind_file_approval(str(approval["approval_id"]), memory.conversation_id)
            state["approval"] = approval
        except FileToolError as exc:
            state["approval"] = {}
            state["task_status"] = "failed"
            state["final_answer"] = f"文件写入审批创建失败：{exc}"
            approval = {}
    elif approval:
        try:
            approval = bind_cmd_approval(str(approval["approval_id"]), memory.conversation_id)
            state["approval"] = approval
        except CmdApprovalError as exc:
            state["approval"] = {}
            state["task_status"] = "failed"
            state["final_answer"] = f"CMD 命令审批创建失败：{exc}"
            approval = {}
    if state.get("task_status") == "failed":
        finalize_task_timing(state)
    save_react_task(memory.conversation_id, state)
    evidence = [
        EvidenceItem(
            source=str(doc.get("source", "unknown")),
            content=_preview_content(str(doc.get("content", ""))),
            metadata=dict(doc.get("metadata", {})),
        )
        for doc in state["reranked_docs"][:3]
    ]
    evidence_sources = [item.source for item in evidence]
    operation_tool_args = dict(state.get("tool_args", {}))
    if state.get("selected_tool") == "cmd_execute":
        # Do not copy the raw shell command into the operation audit record.
        operation_tool_args = {
            "approval_id": approval.get("approval_id", ""),
            "status": approval.get("status", ""),
            "working_dir": approval.get("working_dir", ""),
        }
    append_operation(
        memory.conversation_id,
        standalone_question,
        intent=state["intent"],
        selected_tool=state.get("selected_tool", ""),
        tool_args=operation_tool_args,
        queries=state.get("queries", []),
        evidence_sources=evidence_sources,
        retrieval_grade=state.get("retrieval_grade", ""),
        retrieval_score=float(state.get("retrieval_score", 0.0)),
        self_check_result=state.get("self_check_result", {}),
    )
    append_message(memory.conversation_id, "user", request.question)
    append_message(
        memory.conversation_id,
        "assistant",
        state["final_answer"],
        intent=state["intent"],
        evidence_sources=evidence_sources,
    )
    return ChatResponse(
        conversation_id=memory.conversation_id,
        task_id=state.get("task_id", ""),
        task_status=state.get("task_status", "completed"),
        standalone_question=standalone_question,
        answer=state["final_answer"],
        intent=state["intent"],
        retrieval_grade=state["retrieval_grade"],
        self_check=state["self_check_result"],
        evidence=evidence,
        approval=approval,
        task_timing=task_timing_payload(state),
        trace=_build_trace(request.question, standalone_question, state, evidence)
        if request.include_trace
        else {},
    )
