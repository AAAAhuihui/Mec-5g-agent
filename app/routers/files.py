from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pathlib import Path

from app.agent.memory import append_message
from app.agent.mysql_memory_store import delete_session_artifact_by_path, get_react_task_by_approval, save_react_task, upsert_session_artifact
from app.agent.react_workflow import resume_react_workflow
from app.agent.task_timing import finalize_task_timing, task_timing_payload
from app.schemas import FileApprovalRequest, FileApprovalResponse
from app.tools.file_tools import FileToolError, approve_file_operation, bind_file_approval
from app.tools.cmd_approvals import CmdApprovalError, bind_cmd_approval


router = APIRouter(prefix="/files", tags=["files"])


@router.post("/approve", response_model=FileApprovalResponse)
def approve_file(request: FileApprovalRequest) -> FileApprovalResponse:
    try:
        result = approve_file_operation(request.approval_id, request.conversation_id, request.approved)
    except FileToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if result.get("status") == "completed" and result.get("approval_type") == "file_delete":
        delete_session_artifact_by_path(request.conversation_id, str(result["path"]))
    elif result.get("status") == "completed":
        path = Path(str(result["path"]))
        upsert_session_artifact(
            request.conversation_id,
            name=path.stem or path.name,
            artifact_type="file",
            file_path=str(path),
            summary=str(result.get("rationale") or "本地文件"),
            content_sha256=str(result.get("content_sha256") or "") or None,
        )
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
                state["final_answer"] = f"下一项 CMD 审批创建失败：{exc}"
                next_approval = {}
        if state.get("task_status") == "failed":
            finalize_task_timing(state)
        save_react_task(request.conversation_id, state)
        append_message(request.conversation_id, "assistant", str(state.get("final_answer", "")), intent=state.get("intent"))
        result.update(
            {
                "task_id": state.get("task_id", ""),
                "task_status": state.get("task_status", ""),
                "answer": state.get("final_answer", ""),
                "next_approval": next_approval,
                "task_timing": task_timing_payload(state),
            }
        )
    return FileApprovalResponse(**result)
