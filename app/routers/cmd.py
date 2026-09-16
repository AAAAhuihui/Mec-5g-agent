from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent.memory import append_message
from app.agent.mysql_memory_store import get_react_task_by_approval, save_react_task
from app.agent.react_workflow import resume_react_workflow
from app.agent.task_timing import finalize_task_timing, task_timing_payload
from app.schemas import CmdApprovalRequest, CmdApprovalResponse
from app.tools.cmd_approvals import CmdApprovalError, approve_cmd_command, bind_cmd_approval
from app.tools.file_tools import FileToolError, bind_file_approval


router = APIRouter(prefix="/cmd", tags=["cmd"])


@router.post("/approve", response_model=CmdApprovalResponse)
def approve_cmd(request: CmdApprovalRequest) -> CmdApprovalResponse:
    try:
        result = approve_cmd_command(request.approval_id, request.conversation_id, request.approved)
    except CmdApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    task = get_react_task_by_approval(request.approval_id)
    if task:
        state = resume_react_workflow(dict(task["state"]), result)
        next_approval = dict(state.get("approval") or {})
        if next_approval and next_approval.get("approval_type") == "file_write":
            try:
                next_approval = bind_file_approval(next_approval["approval_id"], request.conversation_id)
                state["approval"] = next_approval
            except FileToolError as exc:
                state["approval"] = {}
                state["task_status"] = "failed"
                state["final_answer"] = f"下一项文件写入审批创建失败：{exc}"
                next_approval = {}
        elif next_approval:
            try:
                next_approval = bind_cmd_approval(next_approval["approval_id"], request.conversation_id)
                state["approval"] = next_approval
            except CmdApprovalError as exc:
                state["approval"] = {}
                state["task_status"] = "failed"
                state["final_answer"] = f"下一条 CMD 审批创建失败：{exc}"
                next_approval = {}
        if state.get("task_status") == "failed":
            finalize_task_timing(state)
        save_react_task(request.conversation_id, state)
        # The resumed ReAct result is a real assistant turn. Persisting it here also
        # refreshes the rolling conversation summary used by the next user question.
        evidence_sources = [
            str(doc.get("source", "unknown"))
            for doc in state.get("reranked_docs", [])[:3]
        ]
        append_message(
            request.conversation_id,
            "assistant",
            str(state.get("final_answer", "")),
            intent=state.get("intent"),
            evidence_sources=evidence_sources,
        )
        result.update(
            {
                "task_id": state.get("task_id", ""),
                "task_status": state.get("task_status", ""),
                "answer": state.get("final_answer", ""),
                "next_approval": next_approval,
                "task_timing": task_timing_payload(state),
            }
        )
    return CmdApprovalResponse(**result)
